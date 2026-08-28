#!/usr/bin/env python3
"""Screenshot the live AvianVisitors collage for the e-ink frame.

Loads the real site (the LAN default http://birdnet.local, or a forwarded
public URL) at a portrait viewport, hides the controls, sets the frame
titles, and rewrites a few of the page's own apt.js tunables at capture time
(cluster bias, count-to-size exponent, a rare-bird floor). The result is the
actual website, framed for the wall, with no changes to AvianVisitors.

Needs a real headless browser, so it runs on any 64-bit capable machine,
including the frame's own Pi (3 A+ / Zero 2 W) but NOT an original ARMv6
Pi Zero W. Writes a 1200x1600 PNG; display.py turns it into panel pixels.

  pip install playwright && playwright install chromium
  python3 shoot.py --url https://bird.onethreenine.net \
      --title "onethreenine birds" --subtitle "heard today" --out frame.png
"""
from __future__ import annotations

import argparse
import base64
import http.server
import json
import os
import re
import socketserver
import sys
import threading
import urllib.parse

# `_metrics` rather than `metrics`, because shoot() takes a `metrics` argument
# and a module shadowed by a parameter is a bug waiting for the one code path
# that reaches for the module inside the function.
import metrics as _metrics


# --bird-weather resolves cutouts from the local clone first, then falls back to
# the repo's raw GitHub URLs: a fresh install needs no illustration redeploy,
# upstream additions arrive with a git pull, and cutouts you generate and copy
# into the clone render even before they reach GitHub.
RAW_ILLUSTRATIONS = ("https://raw.githubusercontent.com/Twarner491/AvianVisitors/"
                     "avian-visitors/avian/assets/illustrations/")

# Hide the controls and the other views, freeze animations. Titles + collage
# stay. Injected before first paint.
HIDE_CSS = """
  .top, .slider, .return-to-atlas, .menu-shell, #menu-dd, #detail-modal, #about-modal,
  .admin-screen, #collageTip, .modal-backdrop, #v1, #v2 { display: none !important; }
  .views { transform: none !important; }
  *, *::before, *::after { animation: none !important; transition: none !important; }
  html, body { background: var(--paper, #efece0) !important; }
"""


def _frame_css(headline_px, eyebrow_px, lowercase, pad_top, pad_side, pad_bottom, collage_vh):
    css = (
        f".stage {{ padding: {pad_top}px {pad_side}px {pad_bottom}px !important;"
        f" box-sizing: border-box !important; justify-content: center !important; }}"
        f".views {{ flex: 0 0 auto !important; height: {collage_vh}vh !important; }}"
        f".view#v0 {{ height: 100% !important; flex: 1 1 100% !important; padding: 6px 0 !important; }}"
        f".gcollage {{ max-width: none !important; }}"
        # 40px, not the site's own 14. display.py splits the title off the
        # collage at the first 60-device-pixel band of clear paper between them,
        # and at dsf=2 this padding is the only part of that band it can count
        # on. At the default collage height the packed cluster usually leaves
        # slack of its own to borrow; raise shoot_collage_vh for a bare panel
        # and it stops doing so, and a missed split drops the frame back to
        # placing the whole crop as one block at the wrong size.
        f".static-head {{ padding: 0 8px 40px !important; }}"
        f".static-head .pre {{ font-size: {eyebrow_px}px !important; }}"
        f".static-head h1 {{ font-size: {headline_px}px !important; }}"
        ".gtile-label text { fill: #000 !important; filter: none !important;"
        " font-weight: 400 !important; }"
        # The still-singing outline is pinned to solid black rather than left
        # to the page's own variable, so the panel lays it down in one of its
        # six inks and the dither never mottles it into a broken line.
        " .gtile-fresh path { stroke: #000 !important; }"
        ".empty-nest .empty { font-size: 18px !important; font-weight: 650 !important;"
        " letter-spacing: 0.12em !important; color: #242424 !important; }"
    )
    if lowercase:
        css += ".static-head h1 { text-transform: none !important; }"
    return css


def _safe_continue(route):
    try:
        route.continue_()
    except Exception:
        pass


def _frame_url(url, bird_names, fresh_minutes=0, fade="0"):
    """Set the frame's label, fresh-outline and fade preferences without
    disturbing other URL state. fresh_minutes = 0 turns the outline off;
    fade = "0" turns the fade off, otherwise it is "<start>-<end>" in hours."""
    parts = urllib.parse.urlsplit(url)
    query = [(key, value) for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if key not in ("labels", "fresh", "fade", "frame")]
    # Tell the page it is being photographed rather than read. It then draws
    # the collage once and holds still: no 30-second poll re-rendering the
    # plate underneath the screenshot, and no atlas built for a view the
    # frame crops away. A page predating the flag ignores it, as before.
    query.append(("frame", "1"))
    query.append(("labels", "1" if bird_names else "0"))
    query.append(("fresh", str(max(0, int(fresh_minutes or 0)))))
    query.append(("fade", str(fade or "0")))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def _make_api_handler(floor_frac, window_hours, auth, species=None):
    """Re-window action=recent (to preview busy days) and floor the rarest
    counts so the packer draws them a little larger. With `species` set
    (--bird-weather), serve that list for recent and an empty body for the
    other views, which have no backend in that mode."""
    def handler(route):
        req = route.request
        if "action=recent" not in req.url:
            if species is not None:
                return route.fulfill(status=200, content_type="application/json", body="{}")
            return route.continue_()
        try:
            if species is not None:
                data = {"hours": int(window_hours or 24), "species": species, "as_of": ""}
            else:
                url = re.sub(r"hours=\d+", f"hours={int(window_hours)}", req.url) if window_hours else req.url
                kw = {"url": url}
                if auth:
                    kw["headers"] = {**req.headers, "authorization": auth}
                data = route.fetch(**kw).json()
            sp = data.get("species", [])
            if sp and floor_frac > 0:
                floor = max((s.get("n") or 1) for s in sp) * floor_frac
                for s in sp:
                    if (s.get("n") or 1) < floor:
                        s["n"] = max(1, round(floor))
            route.fulfill(status=200, content_type="application/json", body=json.dumps(data))
        except Exception as e:
            print(f"recent-API rewrite skipped: {e}", file=sys.stderr)
            _safe_continue(route)
    return handler


def _serve_frontend(directory):
    """Serve the static collage frontend on a free localhost port (daemon thread)."""
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    def make(*args, **kwargs):
        return Quiet(*args, directory=directory, **kwargs)

    httpd = socketserver.TCPServer(("127.0.0.1", 0), make)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _make_cutout_handler(base, local_dir=None):
    """Resolve each cutout.php lookup to the bird's illustration. Serve a local
    file first when `local_dir` has it - that is how cutouts you generate and copy
    into the clone render before they reach GitHub - otherwise 302 to the raw
    GitHub copy. Trusts species_for_zip to pre-filter to drawable slugs, so the
    GitHub fallback only lands on a missing file if the repo is mid-update."""
    def handler(route):
        try:
            params = urllib.parse.parse_qs(urllib.parse.urlparse(route.request.url).query)
            slug = re.sub(r"[^a-z0-9]+", "-", (params.get("sci") or [""])[0].lower()).strip("-")
            if (params.get("pose") or ["1"])[0] == "2":
                slug += "-2"
            if local_dir:
                local = os.path.join(local_dir, slug + ".png")
                if os.path.isfile(local):
                    return route.fulfill(path=local)
            route.fulfill(status=302, headers={"location": base + slug + ".png"})
        except Exception:
            _safe_continue(route)
    return handler


# Every collage tunable the frame rewrites inside the page's apt.js at capture
# time, as (pattern, replacement template). Named and at module level because
# three things need the same list and none of them may drift from the others:
# the capture path rewrites them, install.sh checks a station's frontend against
# them before pointing a frame at that station, and a test checks this repo's
# own. A frame is only as tunable as the apt.js it screenshots, and a station
# serving an older collage than the frame was built against is not a theoretical
# worry - it is what a BirdNET-Pi install and a frame install on one box give
# you by default, since they are separate clones. Finding that out at render
# time costs a wasted render on a Pi that can barely afford one; finding it out
# at install time costs a line of output.
JS_TUNABLES = (
    (r"var xBias = narrow \? 1 : T\.ellipseAspectBias;", "var xBias = {xbias};"),
    (r"var yBias = narrow \? 1\.7 : 1;", "var yBias = {ybias};"),
    (r"countExp:\s*[\d.]+,", "countExp: {count_exp},"),
    (r"var pad = narrow \? Math\.max\(1, COLLAGE_PAD - 1\) : COLLAGE_PAD;", "var pad = {pad};"),
    (r"var LABEL_MIN_PX = \d+;", "var LABEL_MIN_PX = {label_min_px};"),
    (r"var MARK_SCALE = [\d.]+;", "var MARK_SCALE = {label_scale};"),
)


def missing_tunables(js):
    """Which JS_TUNABLES a given apt.js does not carry, as a list of patterns.

    Empty means the frame can drive that frontend. This is the whole contract
    between the two: everything else the collage does is its own business."""
    return [pat for pat, _ in JS_TUNABLES if not re.search(pat, js)]


def _make_js_handler(xbias, ybias, count_exp, pad, label_min_px, label_scale, auth, misses):
    """Rewrite the collage tunables inside the page's apt.js at capture time."""
    values = {"xbias": xbias, "ybias": ybias, "count_exp": count_exp, "pad": pad,
              "label_min_px": int(label_min_px), "label_scale": label_scale}

    def handler(route):
        try:
            kw = {"headers": {**route.request.headers, "authorization": auth}} if auth else {}
            js = route.fetch(**kw).text()
            for pat, repl in JS_TUNABLES:
                js, n = re.subn(pat, repl.format(**values), js)
                if not n:
                    misses.append(pat)
            route.fulfill(status=200, content_type="application/javascript; charset=utf-8", body=js)
        except Exception as e:
            print(f"apt.js rewrite skipped: {e}", file=sys.stderr)
            _safe_continue(route)
    return handler


# What the browser is told, and why each one is worth a line.
#
# The frame renders one static page and photographs it. Chromium is built to
# keep many tabs of live content responsive, and most of what it spends memory
# on serves that and not this. On a Pi 3 A+ the render's own slice measured
# 220-240MB against a 415MB machine that is already holding a BirdNET station,
# so the overflow goes to zram and then to an SD card - which is the difference
# between a render that takes a minute and one that takes twenty.
#
# srgb            the panel's dither expects sRGB, not the display's profile.
# dev-shm-usage   /dev/shm is tiny in a container and on a Pi; without this
#                 chromium tries to use it for shared memory and falls over.
# gpu +           there is no GPU. Playwright's default turns on SwiftShader, a
# software-        software GL rasteriser, which costs a process and its memory
# rasterizer      to emulate hardware that would not be used.
# renderer-limit  one page, so one renderer. The default pool is sized for tabs.
# max-old-space   caps V8's heap. The collage's own working set is a few MB of
#                 masks and tiles; left uncapped V8 sizes its heap against the
#                 machine and grows into memory the station needs.
# extensions,     none are installed and none are wanted; each is startup work
# background-*    and resident memory for a page that lives for one screenshot.
CHROMIUM_ARGS = [
    "--force-color-profile=srgb",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--renderer-process-limit=1",
    "--js-flags=--max-old-space-size=96",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
]


def _record_resources(page, m):
    """Copy the browser's resource-timing buffer into the metrics log.

    Guards itself rather than making the caller do it, so shoot() keeps one
    straight line through the capture and gains no branch for a feature that is
    normally off."""
    if not m.enabled:
        return
    try:
        entries = page.evaluate(
            "() => performance.getEntriesByType('resource').map(e => ({"
            " name: e.name, kind: e.initiatorType,"
            " ms: Math.round(e.duration), bytes: e.transferSize || 0 }))")
    except Exception as e:                      # never fail a render to measure it
        m.event("resources.unavailable", error=str(e))
        return
    total = 0
    for e in entries:
        total += e.get("bytes") or 0
        m.event("resource", name=e.get("name"), kind=e.get("kind"),
                ms=e.get("ms"), bytes=e.get("bytes"))
    # The collage fires eight recent-API fetches per refresh, so this is the
    # count of full re-renders the page did to itself during the capture.
    api = sum(1 for e in entries if "birdnet-api.php" in (e.get("name") or ""))
    m.event("page.summary", requests=len(entries), bytes=total,
            api_requests=api, refresh_passes=round(api / 8.0, 2))

    # How much bigger each illustration is than the box it is drawn in. An
    # asset fetched at 614x598 to be painted at 150x150 costs its bytes, its
    # decode, and a full-size RGBA bitmap resident in a renderer that has 415MB
    # to live in - and the last of those is the one that does not show up in a
    # byte count. devicePixelRatio is in here because the frame captures at
    # dsf=2, so the honest target is CSS box times that, not the CSS box.
    try:
        tiles = page.evaluate(
            "() => { const d = window.devicePixelRatio || 1;"
            " return [...document.querySelectorAll('.gtile img')].map(i => ({"
            "  sci: i.closest('.gtile')?.getAttribute('data-sci') || '?',"
            "  natural: [i.naturalWidth, i.naturalHeight],"
            "  drawn: [Math.round(i.clientWidth * d), Math.round(i.clientHeight * d)]"
            " })); }")
    except Exception:
        return
    for t in tiles:
        nw, nh = t.get("natural") or (0, 0)
        dw, dh = t.get("drawn") or (0, 0)
        ratio = round((nw * nh) / (dw * dh), 1) if dw and dh else None
        m.event("tile.image", sci=t.get("sci"), natural=f"{nw}x{nh}",
                drawn=f"{dw}x{dh}", pixel_ratio=ratio)


def shoot(url, out, *, title=None, subtitle=None, vw=600, vh=800, dsf=2,
          headline_px=42, eyebrow_px=18, lowercase=False,
          mat=0.04, collage_vh=52, cluster_xbias=1.0, cluster_ybias=1.2,
          count_exp=0.4, cluster_pad=1, label_min_px=11, small_floor=0.04, window_hours=None,
          timeout_ms=45000, user=None, password=None, species=None, cutout_base=None,
          cutout_local=None, empty_text="listening for birds…", bird_names=True,
          fresh_minutes=30, fade="0", label_scale=1.0, metrics=None):
    pad_side, pad_top, pad_bottom = int(vw * mat), int(vh * mat * 0.92), int(vh * mat)
    auth = "Basic " + base64.b64encode(f"{user}:{password or ''}".encode()).decode() if user else None
    # Marks name the work BEFORE them, so the first one closes out the browser
    # launch. Off by default: `metrics` is the no-op unless a run asked for it.
    m = metrics if metrics is not None else _metrics.OFF
    # display.py's marks wrap this whole function, so the two streams nest and
    # must not be added together. Tagging the scope is what lets the report
    # total them separately instead of reporting 20s for a 12s render.
    mark = m.marks(scope="shoot")

    # Imported here rather than at the top so this module can be imported
    # without a browser installed. --check-frontend answers a question about a
    # file, and install.sh asks it while deciding what to hand a station; a test
    # asks it of this repo's own collage. Neither should need playwright, and an
    # `image`-mode frame does not install it at all.
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        mark("playwright.driver")
        browser = p.chromium.launch(args=CHROMIUM_ARGS)
        mark("browser.launch")
        try:
            ctx_kw = {
                "viewport": {"width": vw, "height": vh},
                "device_scale_factor": dsf,
                "color_scheme": "light",
            }
            if user:
                ctx_kw["http_credentials"] = {"username": user, "password": password or ""}
            page = browser.new_context(**ctx_kw).new_page()
            misses = []
            page.route("**/birdnet-api.php**", _make_api_handler(small_floor, window_hours, auth, species))
            # The floor moves with the type: it exists so the handwriting stays
            # readable, and at a smaller scale the same physical size is fewer
            # css pixels. 4 is where the browser's own metrics stop being worth
            # trusting, so it does not go below that however far the scale does.
            page.route("**/apt.js*", _make_js_handler(
                cluster_xbias, cluster_ybias, count_exp, cluster_pad,
                max(4, round(label_min_px * label_scale)), round(label_scale, 4),
                auth, misses))
            if bird_names:
                hand_font = os.path.realpath(os.path.join(
                    os.path.dirname(__file__), "..", "avian", "frontend", "fonts", "Caveat.ttf"))
                if not os.path.isfile(hand_font):
                    raise RuntimeError("collage label font is missing")
                page.route("**/avian/frontend/fonts/Caveat.ttf*",
                           lambda route: route.fulfill(path=hand_font))
            if cutout_base:
                page.route("**/cutout.php*", _make_cutout_handler(cutout_base, cutout_local))
            mark("context.and.routes")

            css = HIDE_CSS + _frame_css(headline_px, eyebrow_px, lowercase, pad_top, pad_side, pad_bottom, collage_vh)
            page.add_init_script(
                "document.addEventListener('DOMContentLoaded',function(){"
                "var s=document.createElement('style');s.textContent=" + json.dumps(css) +
                ";document.head.appendChild(s);});")

            resp = page.goto(_frame_url(url, bird_names, fresh_minutes, fade),
                             wait_until="domcontentloaded", timeout=timeout_ms)
            mark("goto")
            if resp is None or not resp.ok:
                raise RuntimeError(f"site returned {resp.status if resp else 'no response'}")
            if bird_names:
                font_loaded = page.evaluate(
                    "async () => { const f = await document.fonts.load('600 16px Hand');"
                    " await document.fonts.ready;"
                    " return f.length > 0 && document.fonts.check('600 16px Hand'); }")
                mark("font.gate")
                if not font_loaded:
                    raise RuntimeError("collage label font did not load")
            # Wait for the collage, or for the empty-state element the page shows
            # when the mic has heard nothing yet, so a birdless frame renders a
            # clean title card fast instead of hanging until the timeout. A page
            # with neither still times out here and stays fatal (keep last frame).
            # [data-collage] appears when renderCollage has finished and says
            # which way it went. Waiting for ".gtile, .empty" instead looks
            # equivalent and is not: the page paints the empty nest first on any
            # load where the species list has not arrived, so .empty won that
            # race every time and the frame took the birdless branch, then
            # photographed whatever turned up during a 250ms sleep.
            #
            # Falls back to the old selector so a station serving an older
            # collage - the two are separate clones and one can lag - still
            # renders instead of timing out. That path keeps the old race; it is
            # a compatibility shim, not the intended route.
            try:
                page.wait_for_selector("[data-collage]", state="attached", timeout=timeout_ms)
                settled = page.get_attribute(".gcollage[data-collage], [data-collage]",
                                             "data-collage")
            except PWTimeout:
                page.wait_for_selector(".gtile, .empty", state="attached", timeout=timeout_ms)
                settled = None
            mark("wait.collage", settled=settled or "unknown")
            if settled == "birds" or page.query_selector(".gtile") is not None:
                try:
                    page.wait_for_function(
                        "() => { const t=[...document.querySelectorAll('.gtile img')];"
                        " return t.length>0 && t.every(i=>i.complete && i.naturalWidth>0); }",
                        timeout=timeout_ms)
                except PWTimeout:
                    print("some illustrations did not finish loading; capturing anyway", file=sys.stderr)
                mark("wait.illustrations")
                if bird_names:
                    missing_labels = page.evaluate(
                        "() => [...document.querySelectorAll('.gtile')]"
                        ".filter(t => !t.querySelector('.gtile-label text'))"
                        ".map(t => t.getAttribute('data-sci') || '?')")
                    # A warning, not a failure. This check was written when names
                    # were off by default, so refusing to ship a half-named frame
                    # cost nothing; now that every frame draws them, the same
                    # refusal would leave the wall showing yesterday's picture
                    # indefinitely over one bird the planner could not fit. The
                    # journal still records exactly which bird, which is what the
                    # check was for.
                    if missing_labels:
                        print("frame labels missing for: " + ", ".join(missing_labels),
                              file=sys.stderr)
            elif page.query_selector(".nest-img") is not None:
                # Birdless empty state: wait for the nest illustration to load so
                # the frame never captures a blank collage area.
                try:
                    page.wait_for_function(
                        "() => { const n=document.querySelector('.nest-img');"
                        " return n && n.complete && n.naturalWidth>0; }",
                        timeout=timeout_ms)
                except PWTimeout:
                    print("nest illustration did not finish loading; capturing anyway", file=sys.stderr)
                mark("wait.nest")
            if misses:
                raise RuntimeError(f"apt.js tunables not found ({len(misses)}); refusing to ship a half-tuned frame")

            if title is not None:
                page.evaluate("t=>{const e=document.querySelector('.static-head .pre'); if(e)e.textContent=t;}", title)
            if subtitle is not None:
                page.evaluate("s=>{const e=document.querySelector('.static-head h1'); if(e)e.textContent=s;}", subtitle)
            # Set the empty-state line for a birdless frame (the mic hasn't heard
            # anything yet, or BirdWeather has no recent detections nearby) and
            # darken it so it survives the e-ink dither and the matting step's ink
            # detection (a no-op once there are birds). empty_text=None hides the
            # line entirely: the gen 3 frame shows the bare nest, no words.
            if empty_text is None:
                page.evaluate("() => { const e = document.querySelector('.empty'); if (e) e.style.display = 'none'; }")
            else:
                page.evaluate("(t) => { const e = document.querySelector('.empty'); if (e) e.textContent = t; }", empty_text)
            page.wait_for_timeout(250)
            mark("titles.and.settle")
            # What the page actually fetched, straight out of the browser's own
            # timing buffer. One call, and it answers two questions at once: what
            # every asset weighed, and - by counting the recent-API entries, which
            # the collage requests eight at a time - how many times the page
            # re-rendered itself while we were photographing it.
            _record_resources(page, m)
            page.screenshot(path=out, clip={"x": 0, "y": 0, "width": vw, "height": vh})
            mark("screenshot")
        finally:
            browser.close()
            mark("browser.close")
    return out


def shoot_birdweather(out, species, *, title=None, subtitle=None, timeout_ms=45000,
                      metrics=None, **look):
    """Render `species` ([{sci,com,n}]) as the BirdWeather collage into `out`.

    The mic path screenshots a live site; this builds the same page from a
    species list instead. It serves the bundled frontend on localhost, feeds it
    the species, and routes cutouts to the local clone first then GitHub, so the
    --bird-weather CLI and display.py's inline render share one setup. An empty
    list renders the page's empty-state card, the same as the mic mode. `look`
    overrides any shoot() tunable (the CLI passes its flags through)."""
    if species is None:
        raise RuntimeError("shoot_birdweather needs a species list")
    here = os.path.dirname(os.path.abspath(__file__))
    _httpd, port = _serve_frontend(os.path.join(here, "..", "avian", "frontend"))
    cutout_local = os.path.join(here, "..", "avian", "assets", "illustrations")
    # BirdWeather's flat 7-day counts need a steeper exponent for the same hero
    # hierarchy; the slightly smaller titles match the mic frame's optical weight.
    # A birdless BirdWeather frame says "no recent detections nearby", not "listening".
    # BirdWeather reports no per-species last_seen, so there is nothing to
    # judge a fresh outline against; asking for one would only cost the page a
    # query parameter it can do nothing with.
    for k, v in (("count_exp", 1.0), ("headline_px", 39), ("eyebrow_px", 17),
                 ("fresh_minutes", 0), ("fade", "0"),
                 ("empty_text", "no recent detections nearby")):
        look.setdefault(k, v)
    return shoot(f"http://127.0.0.1:{port}/", out,
                 title=title or "Avian Visitors", subtitle=subtitle or "Heard Today",
                 species=species, cutout_base=RAW_ILLUSTRATIONS, cutout_local=cutout_local,
                 timeout_ms=timeout_ms, metrics=metrics, **look)


def main():
    ap = argparse.ArgumentParser(description="Screenshot the AvianVisitors collage for the e-ink frame.")
    ap.add_argument("--url", default="http://birdnet.local")
    ap.add_argument("--out", default="frame.png")
    ap.add_argument("--title")
    ap.add_argument("--subtitle")
    ap.add_argument("--lowercase", action="store_true")
    ap.add_argument("--headline-px", type=int, default=None,
                    help="headline font px; default 42 for the mic, 39 for --bird-weather")
    ap.add_argument("--eyebrow-px", type=int, default=None,
                    help="eyebrow font px; default 18 for the mic, 17 for --bird-weather")
    ap.add_argument("--mat", type=float, default=0.04)
    ap.add_argument("--collage-vh", type=float, default=52,
                    help="share of the viewport height the collage gets; more source "
                         "pixels for a full-panel opening, less for a small mat")
    ap.add_argument("--cluster-xbias", type=float, default=1.0)
    ap.add_argument("--cluster-ybias", type=float, default=1.2)
    ap.add_argument("--count-exp", type=float, default=None,
                    help="count-to-size exponent; default 0.4 for the mic, 1.0 for --bird-weather")
    ap.add_argument("--cluster-pad", type=int, default=1)
    ap.add_argument("--small-floor", type=float, default=0.04)
    ap.add_argument("--window-hours", type=int)
    ap.add_argument("--bird-weather", action="store_true",
                    help="render from BirdWeather data for --zip instead of a local mic")
    ap.add_argument("--zip", help="ZIP / postal code, required with --bird-weather")
    ap.add_argument("--bw-days", type=int, default=7, help="--bird-weather lookback window in days")
    ap.add_argument("--bw-country", default="us", help="--bird-weather geocoder country code")
    ap.add_argument("--width", type=int, default=600)
    ap.add_argument("--height", type=int, default=800)
    ap.add_argument("--dsf", type=int, default=2)
    ap.add_argument("--user")
    ap.add_argument("--password")
    ap.add_argument("--bird-names", dest="bird_names", action="store_true", default=True,
                    help="show common names along the birds (the default)")
    ap.add_argument("--no-bird-names", dest="bird_names", action="store_false",
                    help="draw the collage without names")
    ap.add_argument("--fresh-minutes", type=int, default=30,
                    help="outline birds heard this recently; 0 turns the mark off")
    ap.add_argument("--label-scale", type=float, default=1.0,
                    help="multiply the bird-name type size (the frame derives this "
                         "from its opening so names stay one physical size)")
    ap.add_argument("--fade", default="0",
                    help='drain colour from birds over "<start>-<end>" hours of '
                         'silence; 0 turns fading off')
    ap.add_argument("--timeout", type=int, default=45000)
    # Answers "could a frame drive this frontend?" without rendering anything,
    # so install.sh can check a station's collage before it points a frame at
    # it. Takes the apt.js itself rather than a URL: at install time the file
    # is on disk and the web server may not be up yet.
    ap.add_argument("--check-frontend", metavar="APT_JS",
                    help="report which collage tunables an apt.js is missing, then exit")
    a = ap.parse_args()
    if a.check_frontend:
        with open(a.check_frontend, encoding="utf-8", errors="replace") as f:
            gone = missing_tunables(f.read())
        for pat in gone:
            print(f"missing: {pat}", file=sys.stderr)
        sys.exit(1 if gone else 0)
    if a.bird_weather:
        if not a.zip:
            print("--bird-weather needs --zip", file=sys.stderr)
            sys.exit(2)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import birdweather
        species = birdweather.species_for_zip(a.zip, country=a.bw_country, days=a.bw_days)
        if not species:
            print(f"no drawable birds near {a.zip}; nothing to render", file=sys.stderr)
            sys.exit(3)
        # Pass the CLI's look flags through; shoot_birdweather fills the bird-weather
        # defaults (steeper count exponent, smaller titles) for anything left unset.
        look = {k: v for k, v in (("count_exp", a.count_exp), ("headline_px", a.headline_px),
                                  ("eyebrow_px", a.eyebrow_px)) if v is not None}
        look.update(vw=a.width, vh=a.height, dsf=a.dsf, mat=a.mat, collage_vh=a.collage_vh,
                    cluster_xbias=a.cluster_xbias, cluster_ybias=a.cluster_ybias,
                    cluster_pad=a.cluster_pad, small_floor=a.small_floor, lowercase=a.lowercase,
                    window_hours=a.window_hours, user=a.user, password=a.password,
                    bird_names=a.bird_names)
        try:
            shoot_birdweather(a.out, species, title=a.title, subtitle=a.subtitle,
                              timeout_ms=a.timeout, **look)
        except Exception as e:
            print(f"shoot failed: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"wrote {a.out}")
        return
    # Mic path: screenshot the live AvianVisitors site at --url.
    count_exp = a.count_exp if a.count_exp is not None else 0.4
    headline_px = a.headline_px if a.headline_px is not None else 42
    eyebrow_px = a.eyebrow_px if a.eyebrow_px is not None else 18
    try:
        shoot(a.url, a.out, title=a.title, subtitle=a.subtitle, vw=a.width, vh=a.height, dsf=a.dsf,
              headline_px=headline_px, eyebrow_px=eyebrow_px, lowercase=a.lowercase,
              mat=a.mat, collage_vh=a.collage_vh, cluster_xbias=a.cluster_xbias,
              cluster_ybias=a.cluster_ybias, count_exp=count_exp, cluster_pad=a.cluster_pad,
              small_floor=a.small_floor,
              window_hours=a.window_hours, timeout_ms=a.timeout, user=a.user, password=a.password,
              bird_names=a.bird_names, fresh_minutes=a.fresh_minutes, fade=a.fade,
              label_scale=a.label_scale)
    except Exception as e:
        print(f"shoot failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
