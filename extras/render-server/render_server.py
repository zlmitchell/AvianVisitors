#!/usr/bin/env python3
"""Render the frame's plate somewhere that can afford a browser, and publish it.

A Pi 3 A+ running a BirdNET station cannot also run headless chromium. Measured
on one: 182 seconds to start the browser, and then Page.goto could not reach
domcontentloaded inside 180s on a page the same machine serves in under a
second. The same render from an ordinary desktop against the same station takes
27 seconds. Nothing about the collage is expensive - the browser is.

So this takes the browser off the Pi. It renders the SCREENSHOT, not the panel
image: matting, dithering and the push stay on the Pi, where they measured 0.55s
of a 9s render and need no browser at all. The Pi keeps its own change gate too,
so it still decides when the panel is worth twelve seconds of e-ink.

The Pi then wants, in ~/.birdframe/config.toml:

    shoot     = false
    image_url = "http://<this-host>:8080/frame.png"

If this server is down or the host is asleep, display.py's fetch fails, it logs
and keeps the last panel image - the frame goes stale rather than blank, which
is the right failure for a picture on a wall.
"""
import http.server
import os
import socketserver
import sys
import threading
import time
import traceback
import urllib.parse
from urllib.error import URLError

from PIL import Image, ImageChops

sys.path.insert(0, os.environ.get("FRAME_DIR", "/app/frame"))
import display                                    # noqa: E402
from shoot import shoot                           # noqa: E402

CONFIG = os.environ.get("FRAME_CONFIG", "/config/config.toml")
STATION = os.environ.get("STATION_URL", "")
OUT_DIR = os.environ.get("OUT_DIR", "/out")
PORT = int(os.environ.get("PORT", "8080"))
# How often to look. Cheap here, so look often: the Pi's own timer is every 15
# minutes and it can only push a picture that already exists, so rendering less
# often than it checks would show yesterday's birds for a cycle.
EVERY = int(os.environ.get("INTERVAL_SECONDS", "300"))
OUT = os.path.join(OUT_DIR, "frame.png")

# One render at a time, and one place that remembers what was last drawn. The
# poll loop and a ?force= request can both decide to render, and two chromiums
# racing to write the same file is not something to find out about later.
_render_lock = threading.Lock()
_state = {"last": None}
# Still a .png: playwright picks the screenshot format from the file extension,
# so a plain ".tmp" is rejected as an unsupported mime type. Dotted so a
# directory listing does not offer a half-written frame to anyone browsing it.
PARTIAL = os.path.join(OUT_DIR, ".frame-partial.png")


def load():
    cfg = display.load_config(CONFIG)
    if STATION:
        cfg = display.apply_overrides(cfg, [f"base_url={STATION}"])
    return cfg


# Least ink the collage half of a plate may carry and still be believable, as
# a percentage of that half. Measured: a plate with one bird carries 7.9%, and
# one where the illustrations never arrived carries 0.14% - the lettering, and
# nothing else. Anything between those is not a picture of a quiet day, it is a
# picture of a render that went wrong, and 1% sits an order of magnitude clear
# of both. A genuinely birdless station is not caught by this: the empty state
# draws a nest, which is an illustration like any other.
MIN_COLLAGE_INK = 1.0


def collage_ink(path):
    """Percent of the plate below the title band that is not paper."""
    im = Image.open(path).convert("RGB")
    corners = [im.getpixel(p) for p in
               ((0, 0), (im.width - 1, 0), (0, im.height - 1), (im.width - 1, im.height - 1))]
    paper = tuple(sorted(c[i] for c in corners)[len(corners) // 2] for i in range(3))
    diff = ImageChops.difference(im, Image.new("RGB", im.size, paper)).convert("L")
    lower = diff.crop((0, int(im.height * 0.25), im.width, im.height))
    on = sum(1 for v in lower.getdata() if v > 34)
    return on / (lower.width * lower.height) * 100


def render_once(cfg, why):
    tmp = PARTIAL
    # The same call display.py makes on its mic path, field for field - the
    # layout knobs matter here because label_scale is applied at capture time,
    # so a renderer that did not read the frame's config would size the
    # lettering for a different mat.
    shoot(cfg["base_url"], tmp,
          title=cfg["shoot_title"], subtitle=cfg["shoot_subtitle"],
          headline_px=cfg["shoot_headline_px"], eyebrow_px=cfg["shoot_eyebrow_px"],
          lowercase=cfg["shoot_lowercase"], mat=cfg["shoot_mat"],
          small_floor=cfg["shoot_small_floor"], count_exp=cfg["shoot_count_exp"],
          timeout_ms=cfg["timeout"] * 1000, user=cfg["basic_user"],
          password=cfg["basic_pass"], window_hours=cfg["hours"],
          bird_names=cfg["bird_names"], fresh_minutes=cfg["fresh_minutes"],
          fade=display.fade_param(cfg), collage_vh=cfg["shoot_collage_vh"],
          label_scale=display.label_scale(cfg))
    # A render can succeed and still be wrong. The browser reports the
    # illustrations loaded, and if the screenshot is taken before they are
    # painted the plate comes out with its lettering and no birds - which the
    # Pi then mats by cropping to the only ink it can find, magnifying a bird
    # name until it fills the panel. That reached the wall once. Keep the last
    # good picture instead: a frame showing yesterday's birds is a far better
    # failure than one showing a giant caption.
    ink = collage_ink(tmp)
    if ink < MIN_COLLAGE_INK:
        os.unlink(tmp)
        print(f"REFUSED ({why}): collage is {ink:.2f}% ink, under {MIN_COLLAGE_INK}% - "
              f"the illustrations did not make it into the capture. Keeping the "
              f"previous frame.", flush=True)
        return False
    # Atomic, so the Pi can never fetch a half-written PNG.
    os.replace(tmp, OUT)
    print(f"rendered ({why}) -> {OUT} {os.path.getsize(OUT)} bytes, {ink:.1f}% ink",
          flush=True)
    return True


def loop():
    while True:
        try:
            cfg = load()
            species, anchor = display.fetch_species(cfg, display._auth(cfg))
            sig = display.signature(
                species,
                display.fresh_slugs(species, anchor, cfg["fresh_minutes"]),
                display.fade_steps(species, anchor, cfg["fade_hours"], cfg["hours"]))
            # Re-render on a change, and once at startup so there is always a
            # picture to serve. Not on a timer otherwise: the plate is a pure
            # function of the birds, so an unchanged signature means an
            # identical PNG and the Pi's gate would ignore it anyway.
            if sig != _state["last"] or not os.path.exists(OUT):
                # Only remember the signature once a plate for it is actually
                # published, so a refused render is retried rather than counted
                # as done and skipped until the birds change again.
                with _render_lock:
                    if render_once(cfg, "changed" if _state["last"] else "first run"):
                        _state["last"] = sig
        except Exception as e:
            # A .local name is mDNS, and a container has no mDNS resolver - the
            # station is reachable from the Pi and from a desktop and simply is
            # not a name in here. It is the single most likely way this fails on
            # a NAS, and urllib's traceback says only "Name or service not
            # known", so name it.
            host = urllib.parse.urlsplit(STATION or "").hostname or ""
            if isinstance(e, URLError) and host.endswith(".local"):
                print(f"cannot resolve {host}: .local is mDNS and this container "
                      f"has no resolver for it. Set STATION_URL to the station's "
                      f"IP address instead.", file=sys.stderr, flush=True)
            else:
                traceback.print_exc()
        time.sleep(EVERY)


def render_now(why):
    """Render synchronously. Returns True if a new plate was published.

    Never raises at the caller: a forced render that cannot reach the station,
    or whose plate fails the ink check, leaves the previous picture in place and
    the caller serves that. A frame showing the last good plate is a far better
    answer to a failed refresh than an error page the Pi would treat as a broken
    fetch and skip anyway.
    """
    with _render_lock:
        try:
            cfg = load()
            species, anchor = display.fetch_species(cfg, display._auth(cfg))
            sig = display.signature(
                species,
                display.fresh_slugs(species, anchor, cfg["fresh_minutes"]),
                display.fade_steps(species, anchor, cfg["fade_hours"], cfg["hours"]))
            if render_once(cfg, why):
                _state["last"] = sig
                return True
        except Exception:
            traceback.print_exc()
    return False


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=OUT_DIR, **k)

    def do_GET(self):
        # ?force=1 draws the plate before serving it, rather than handing over
        # whatever the poll loop last happened to make.
        #
        # This is what closes the gap between the two halves. The Pi decides for
        # itself when the birds have changed enough to be worth twelve seconds
        # of e-ink, and it only fetches the image on a run where it has already
        # decided to push - so asking for a fresh render at that moment costs
        # nothing on an idle tick and guarantees the picture matches the birds
        # the Pi just counted. Without it the two poll independently and the
        # frame can push a plate drawn before the bird that triggered it.
        #
        # It blocks for the length of a render, which is the point, and the
        # frame's own `timeout` (180s by default) is the bound on it.
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if query.get("force") and query["force"][0] not in ("0", "false", ""):
            render_now("forced by " + (self.client_address[0] if self.client_address
                                       else "?"))
        return super().do_GET()

    def log_message(self, fmt, *a):
        print("http: " + fmt % a, flush=True)


def explain_missing_config():
    """Say which of the several ways this goes wrong actually happened.

    "No config at /config/config.toml" is true and useless: the mount can be
    absent, present but empty, pointed at the wrong folder, or pointed at the
    file itself rather than the folder holding it. Each needs a different fix
    and they are indistinguishable from the message alone - so look, and say.
    """
    d = os.path.dirname(CONFIG) or "/"
    lines = [f"no frame config at {CONFIG}"]
    if os.path.isfile(d):
        lines += [f"  {d} is a FILE, not a directory.",
                  "  You mounted the config file onto the folder. Either mount the",
                  f"  folder that contains it at {d}, or mount the file itself at",
                  f"  {CONFIG}."]
    elif not os.path.isdir(d):
        lines += [f"  {d} does not exist - nothing is mounted there.",
                  "  Add a volume: <a folder holding config.toml>:/config"]
    else:
        try:
            found = sorted(os.listdir(d))
        except OSError as e:
            found = [f"<unreadable: {e}>"]
        if not found:
            lines += [f"  {d} is mounted but empty.",
                      "  The folder you mounted does not contain config.toml."]
        else:
            lines += [f"  {d} is mounted and contains: {', '.join(found[:10])}",
                      "  but no file called config.toml. Rename it, or point",
                      "  FRAME_CONFIG at the one you want."]
    lines += ["",
              "  The file is the frame's own config, from the Pi:",
              "    scp <pi>:~/.birdframe/config.toml <folder>/config.toml",
              "",
              "  It is needed, not optional: label_scale and shoot_collage_vh are",
              "  applied when the picture is captured, so a renderer that guessed",
              "  at them would size the lettering for a different mat."]
    return chr(10).join(lines)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.isfile(CONFIG):
        print(explain_missing_config(), file=sys.stderr)
        return 2
    # Everything the CAPTURE depends on, printed once, so the copy of the
    # config that lives here can be compared against the Pi's at a glance.
    #
    # The two are separate files and nothing keeps them in step: change the mat
    # on the Pi and this keeps sizing the lettering for the old one, silently,
    # because label_scale is derived from the opening and applied in the browser.
    # The rest of the frame's settings - rotate, saturation, the timestamp, the
    # panel driver - belong to the Pi alone and are not listed, because a
    # difference in those means nothing.
    try:
        cfg = load()
        print("capture settings from " + CONFIG + ":", flush=True)
        for key in ("shoot_title", "shoot_subtitle", "shoot_lowercase", "bird_names",
                    "hours", "fresh_minutes", "fade_hours", "shoot_collage_vh",
                    "shoot_cluster_ybias",
                    "shoot_mat", "shoot_count_exp", "shoot_small_floor",
                    "shoot_headline_px", "shoot_eyebrow_px",
                    "opening", "opening_aspect", "collage_frac", "title_frac",
                    "gap_frac"):
            print(f"    {key:<20} {cfg.get(key)!r}", flush=True)
        print(f"    {'-> label_scale':<20} {display.label_scale(cfg):.4f}"
              "   (the one the Pi has to agree with)", flush=True)
    except Exception as e:
        print(f"could not read {CONFIG}: {e}", file=sys.stderr, flush=True)
        return 2

    threading.Thread(target=loop, daemon=True).start()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), Handler) as srv:
        print(f"serving {OUT_DIR} on :{PORT}; station={STATION or 'from config'}; "
              f"checking every {EVERY}s", flush=True)
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
