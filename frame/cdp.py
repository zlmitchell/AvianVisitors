#!/usr/bin/env python3
"""Drive chrome-headless-shell over CDP directly, with no Playwright driver.

Playwright's Node driver measured 101.6MB PSS on the Pi's footprint - a third
of the render's whole process tree, and more than the renderer that draws the
plate. It is there to relay JSON between this process and the browser, which is
a websocket and a message loop. This is that websocket and that message loop.

It deliberately implements the Playwright *surface* rather than a better one,
so shoot() can swap between the two by changing which module it imports and
nothing else. That is what makes the two comparable: any difference in a
measurement is the driver, not a different capture.

Stdlib only, on purpose. A dependency that costs 100MB is what this replaces,
so buying a websocket library back would be a strange way to spend the win.
"""
from __future__ import annotations

import base64
import json
import os
import re
import select
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request


class TimeoutError(Exception):                  # noqa: A001 - mirrors PWTimeout
    """Raised where playwright would raise its own TimeoutError."""


# --- the websocket, in the small part of RFC 6455 a CDP client uses ---------
#
# Text frames out, text frames in, masked one way, fragmented the other. No
# extensions, no permessage-deflate, no subprotocols. CDP never sends a binary
# frame and the only control frames chrome sends are ping and close.
class _WS:
    def __init__(self, url, timeout=30):
        m = re.match(r"ws://([^:/]+):(\d+)(/.*)", url)
        if not m:
            raise RuntimeError(f"not a ws:// url: {url}")
        host, port, path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        self.buf = bytearray()
        head = self._until(b"\r\n\r\n", deadline=time.monotonic() + timeout)
        if b"101" not in head.split(b"\r\n")[0]:
            raise RuntimeError(f"websocket upgrade refused: {head[:120]!r}")

    # Reading is deadline-driven rather than blocking, because the caller is
    # usually waiting for one command reply while the page is still firing
    # interception events that have to be answered or the load stalls.
    def _fill(self, deadline):
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        r, _, _ = select.select([self.sock], [], [], left)
        if not r:
            return False
        chunk = self.sock.recv(262144)
        if not chunk:
            raise RuntimeError("websocket closed by browser")
        self.buf += chunk
        return True

    def _until(self, sep, deadline):
        while sep not in self.buf:
            if not self._fill(deadline):
                raise TimeoutError("timed out reading handshake")
        i = self.buf.index(sep) + len(sep)
        head, self.buf = bytes(self.buf[:i]), self.buf[i:]
        return head

    def _exact(self, n, deadline):
        while len(self.buf) < n:
            if not self._fill(deadline):
                return None
        out, self.buf = bytes(self.buf[:n]), self.buf[n:]
        return out

    def recv(self, timeout):
        """One complete message, reassembling continuation frames, or None."""
        deadline = time.monotonic() + timeout
        payload = bytearray()
        while True:
            head = self._exact(2, deadline)
            if head is None:
                return None
            fin, opcode = head[0] & 0x80, head[0] & 0x0F
            length = head[1] & 0x7F
            if length == 126:
                ext = self._exact(2, deadline)
                if ext is None:
                    return None
                length = struct.unpack(">H", ext)[0]
            elif length == 127:
                ext = self._exact(8, deadline)
                if ext is None:
                    return None
                length = struct.unpack(">Q", ext)[0]
            body = self._exact(length, deadline) if length else b""
            if body is None:
                return None
            if opcode == 0x9:                   # ping -> pong, keep waiting
                self._frame(0xA, body)
                continue
            if opcode == 0x8:
                raise RuntimeError("websocket closed by browser")
            payload += body
            if fin:
                return payload.decode("utf-8", "replace")

    def _frame(self, opcode, data):
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        n = len(data)
        if n < 126:
            header = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        self.sock.sendall(header + mask + masked)

    def send(self, text):
        self._frame(0x1, text.encode())

    def close(self):
        try:
            self._frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# --- glob matching, as much of it as the frame's four route patterns need ---
def _glob(pattern):
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


_TYPES = {".png": "image/png", ".ttf": "font/ttf", ".js": "application/javascript",
          ".json": "application/json", ".css": "text/css", ".webp": "image/webp"}


class _Response:
    def __init__(self, status):
        self.status = status
        self.ok = 200 <= status < 300 or status == 0


class _Fetched:
    """What route.fetch() returns. Done in this process with urllib rather than
    through the browser: the bytes are wanted here, to be rewritten here."""

    def __init__(self, body):
        self._body = body

    def text(self):
        return self._body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self._body)


class _Request:
    def __init__(self, params):
        self.url = params["request"]["url"]
        self.headers = {k.lower(): v for k, v in params["request"].get("headers", {}).items()}


class _Route:
    def __init__(self, page, request_id, params):
        self._page, self._id = page, request_id
        self.request = _Request(params)
        self.handled = False

    def continue_(self):
        self.handled = True
        self._page._call("Fetch.continueRequest", {"requestId": self._id})

    def fetch(self, url=None, headers=None):
        req = urllib.request.Request(url or self.request.url,
                                     headers=headers or self.request.headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            return _Fetched(r.read())

    def fulfill(self, status=200, content_type=None, body=None, headers=None, path=None):
        self.handled = True
        hdrs = dict(headers or {})
        if path:
            with open(path, "rb") as f:
                raw = f.read()
            content_type = content_type or _TYPES.get(os.path.splitext(path)[1].lower())
        else:
            raw = body.encode() if isinstance(body, str) else (body or b"")
        if content_type:
            hdrs["content-type"] = content_type
        self._page._call("Fetch.fulfillRequest", {
            "requestId": self._id,
            "responseCode": status,
            "responseHeaders": [{"name": k, "value": str(v)} for k, v in hdrs.items()],
            "body": base64.b64encode(raw).decode(),
        })


class Page:
    def __init__(self, browser, session_id):
        self._b, self._sid = browser, session_id
        self._routes = []
        self._frame_id = None
        self._lifecycle = set()
        self._credentials = None

    # --- plumbing ----------------------------------------------------------
    def _call(self, method, params=None, timeout=45.0):
        return self._b._call(method, params, session_id=self._sid, timeout=timeout)

    def _on_auth(self, params):
        # A station behind basic auth challenges every asset, not just the page,
        # so this has to answer at the browser rather than be papered over by
        # the handlers that add their own header for their own fetches.
        creds = self._credentials
        answer = ({"response": "ProvideCredentials",
                   "username": creds.get("username", ""),
                   "password": creds.get("password") or ""}
                  if creds else {"response": "Default"})
        self._call("Fetch.continueWithAuth",
                   {"requestId": params["requestId"], "authChallengeResponse": answer})

    def _on_paused(self, params):
        route = _Route(self, params["requestId"], params)
        for pattern, handler in reversed(self._routes):
            if pattern.match(route.request.url):
                try:
                    handler(route)
                except Exception as e:          # a handler that dies must not hang the load
                    print(f"route handler failed: {e}", file=sys.stderr)
                if not route.handled:
                    route.continue_()
                return
        route.continue_()

    # --- the playwright surface shoot() uses -------------------------------
    def route(self, pattern, handler):
        self._routes.append((_glob(pattern), handler))

    def add_init_script(self, script):
        self._call("Page.addScriptToEvaluateOnNewDocument", {"source": script})

    def goto(self, url, wait_until="domcontentloaded", timeout=45000):
        self._lifecycle.clear()
        res = self._call("Page.navigate", {"url": url}, timeout=timeout / 1000.0)
        if res.get("errorText"):
            raise RuntimeError(f"navigation failed: {res['errorText']}")
        self._frame_id = res.get("frameId")
        want = {"domcontentloaded": "DOMContentLoaded", "load": "load"}.get(wait_until, "DOMContentLoaded")
        deadline = time.monotonic() + timeout / 1000.0
        while want not in self._lifecycle:
            if not self._b._pump(deadline):
                raise TimeoutError(f"timed out waiting for {wait_until}")
        # performance.getEntriesByType('navigation') carries the HTTP status, so
        # the Network domain never has to be enabled just to learn one integer.
        status = self.evaluate(
            "() => { const n = performance.getEntriesByType('navigation')[0];"
            " return n ? (n.responseStatus || 200) : 200; }")
        return _Response(int(status or 200))

    def evaluate(self, expression, arg=None):
        call = f"({expression})({json.dumps(arg)})" if arg is not None else f"({expression})()"
        res = self._call("Runtime.evaluate", {
            "expression": call, "awaitPromise": True, "returnByValue": True})
        if "exceptionDetails" in res:
            detail = res["exceptionDetails"]
            text = (detail.get("exception") or {}).get("description") or detail.get("text")
            raise RuntimeError(f"evaluate failed: {text}")
        return res.get("result", {}).get("value")

    def wait_for_function(self, expression, timeout=45000, poll=0.05):
        deadline = time.monotonic() + timeout / 1000.0
        while True:
            if self.evaluate(expression):
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("wait_for_function timed out")
            self._b._pump(min(time.monotonic() + poll, deadline))

    def wait_for_selector(self, selector, state="attached", timeout=45000):
        self.wait_for_function(
            "() => !!document.querySelector(" + json.dumps(selector) + ")", timeout=timeout)

    def query_selector(self, selector):
        return True if self.evaluate(
            "() => !!document.querySelector(" + json.dumps(selector) + ")") else None

    def get_attribute(self, selector, name):
        return self.evaluate(
            "() => { const e = document.querySelector(" + json.dumps(selector) + ");"
            " return e ? e.getAttribute(" + json.dumps(name) + ") : null; }")

    def wait_for_timeout(self, ms):
        # Pumped, not slept: the page is still fetching while it settles, and an
        # unanswered Fetch.requestPaused is a request that never completes.
        deadline = time.monotonic() + ms / 1000.0
        while time.monotonic() < deadline:
            self._b._pump(deadline)

    def screenshot(self, path=None, clip=None):
        params = {"format": "png", "captureBeyondViewport": False}
        if clip:
            params["clip"] = {**{k: float(v) for k, v in clip.items()}, "scale": 1}
        data = base64.b64decode(self._call("Page.captureScreenshot", params)["data"])
        if path:
            with open(path, "wb") as f:
                f.write(data)
        return data


class _Context:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page


class Browser:
    def __init__(self, args, startup_timeout=300):
        self._tmp = tempfile.mkdtemp(prefix="frame-cdp-")
        exe = _find_shell()
        cmd = [exe, "--headless", "--remote-debugging-port=0",
               f"--user-data-dir={self._tmp}", "--no-first-run",
               "--no-default-browser-check", "about:blank"] + list(args or [])
        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # If the browser never comes up, the process it did start must still be
        # reaped. Leaking a chromium on a 415MB Pi turns one failed render into
        # a machine that cannot do the next one either.
        try:
            self._ws = _WS(self._endpoint(startup_timeout))
        except BaseException:
            self._kill()
            raise
        self._next_id = 0
        self._replies = {}
        self._page = None

    def _endpoint(self, timeout=300):
        """chrome writes DevToolsActivePort once it is listening: port, then the
        browser's own websocket path. Polling the file is how you learn a port
        you asked the OS to choose."""
        f = os.path.join(self._tmp, "DevToolsActivePort")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError(f"browser exited with {self._proc.returncode}")
            try:
                with open(f, "r", encoding="ascii") as fh:
                    lines = fh.read().split("\n")
                if len(lines) >= 2 and lines[1].strip():
                    return f"ws://127.0.0.1:{lines[0].strip()}{lines[1].strip()}"
            except OSError:
                pass
            time.sleep(0.02)
        raise TimeoutError("browser never reported a devtools port")

    # --- message loop ------------------------------------------------------
    def _dispatch(self, msg):
        if "id" in msg:
            self._replies[msg["id"]] = msg
            return
        if msg.get("method") == "Fetch.requestPaused" and self._page is not None:
            self._page._on_paused(msg["params"])
        elif msg.get("method") == "Fetch.authRequired" and self._page is not None:
            self._page._on_auth(msg["params"])
        elif msg.get("method") == "Page.lifecycleEvent" and self._page is not None:
            self._page._lifecycle.add(msg["params"].get("name"))

    def _pump(self, deadline):
        """Read and dispatch one message. False if the deadline passed first."""
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        raw = self._ws.recv(left)
        if raw is None:
            return False
        self._dispatch(json.loads(raw))
        return True

    def _call(self, method, params=None, session_id=None, timeout=45.0):
        self._next_id += 1
        msg_id = self._next_id
        payload = {"id": msg_id, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        self._ws.send(json.dumps(payload))
        deadline = time.monotonic() + timeout
        while msg_id not in self._replies:
            if not self._pump(deadline):
                raise TimeoutError(f"timed out waiting for {method}")
        reply = self._replies.pop(msg_id)
        if "error" in reply:
            raise RuntimeError(f"{method}: {reply['error'].get('message')}")
        return reply.get("result", {})

    # --- the playwright surface -------------------------------------------
    def new_context(self, viewport=None, device_scale_factor=1, color_scheme="light",
                    http_credentials=None):
        target = self._call("Target.createTarget", {"url": "about:blank"})["targetId"]
        session = self._call("Target.attachToTarget",
                             {"targetId": target, "flatten": True})["sessionId"]
        page = Page(self, session)
        self._page = page
        page._call("Page.enable")
        page._call("Runtime.enable")
        page._call("Page.setLifecycleEventsEnabled", {"enabled": True})
        if viewport:
            page._call("Emulation.setDeviceMetricsOverride", {
                "width": viewport["width"], "height": viewport["height"],
                "deviceScaleFactor": device_scale_factor, "mobile": False})
        page._call("Emulation.setEmulatedMedia", {
            "features": [{"name": "prefers-color-scheme", "value": color_scheme}]})
        # Everything is intercepted and most of it is continued unchanged, which
        # is what playwright does the moment a single route exists. Matching in
        # python keeps the glob semantics the routes were written against.
        page._credentials = http_credentials
        page._call("Fetch.enable", {
            "patterns": [{"urlPattern": "*", "requestStage": "Request"}],
            "handleAuthRequests": bool(http_credentials)})
        return _Context(page)

    def close(self):
        try:
            self._ws.close()
        except Exception:
            pass
        self._kill()

    def _kill(self):
        try:
            self._proc.terminate()
            self._proc.wait(timeout=10)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        shutil.rmtree(self._tmp, ignore_errors=True)


def _find_shell():
    """The headless shell playwright already downloaded, or one on PATH.

    Reusing playwright's binary rather than asking for another install: the
    point of this module is to stop running playwright's *driver*, not to stop
    using the browser it fetched."""
    root = os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright")
    if os.path.isdir(root):
        builds = sorted((d for d in os.listdir(root) if d.startswith("chromium_headless_shell-")),
                        key=lambda d: int(re.sub(r"\D", "", d) or 0), reverse=True)
        # Walked, and looking for either name. The x86 build unpacks
        # chrome-headless-shell-linux64/chrome-headless-shell; the aarch64 build
        # - which is the machine this exists for - unpacks chrome-linux/
        # headless_shell. Joining a fixed path finds the binary on the laptop it
        # was written on and not on the Pi it was written for.
        for build in builds:
            for dirpath, _, files in os.walk(os.path.join(root, build)):
                for name in ("chrome-headless-shell", "headless_shell"):
                    exe = os.path.join(dirpath, name)
                    if name in files and os.access(exe, os.X_OK):
                        return exe
    for name in ("chrome-headless-shell", "headless_shell", "chromium",
                 "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("no chrome-headless-shell found")


class _Chromium:
    def launch(self, args=None, **_):
        return Browser(args)


class _Playwright:
    chromium = _Chromium()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def sync_playwright():
    return _Playwright()
