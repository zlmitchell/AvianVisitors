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

from PIL import Image, ImageChops

sys.path.insert(0, os.environ.get("FRAME_DIR", "/repo/frame"))
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
    last = None
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
            if sig != last or not os.path.exists(OUT):
                # Only remember the signature once a plate for it is actually
                # published, so a refused render is retried rather than counted
                # as done and skipped until the birds change again.
                if render_once(cfg, "changed" if last else "first run"):
                    last = sig
        except Exception:
            traceback.print_exc()
        time.sleep(EVERY)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=OUT_DIR, **k)

    def log_message(self, fmt, *a):
        print("http: " + fmt % a, flush=True)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.isfile(CONFIG):
        print(f"no frame config at {CONFIG} - mount the Pi's ~/.birdframe/config.toml",
              file=sys.stderr)
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
