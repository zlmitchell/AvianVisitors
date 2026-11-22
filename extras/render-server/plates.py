"""Plates: finished pictures for panels that cannot finish one themselves.

frame.png is a screenshot. The 13.3" frame's Pi crops, mats and dithers it,
because the Pi has the CPU for that and the Inky library to do it with. An
ESP32 e-ink board has neither: it can fetch a PNG and put it on the glass, and
that is all. A plate is that PNG, made here instead.

Each [table] in plates.toml, next to config.toml, is one plate, served as
/<name>.png. It is drawn from the same birds and the same frame settings as
frame.png, but shot at the plate's own size, cropped to the birds, scaled to
fill the panel and, for a colour e-ink panel, dithered to its inks. Like
frame.png it is redrawn when its birds change, not on a timer.

    [e1002]               # -> /e1002.png, a reTerminal E1002 (7.3" Spectra 6)
    width = 800           # panel pixels
    height = 480
    names = false         # the bird names (default: the frame's bird_names)
    title = false         # the frame's title and subtitle (default: true)
    fade = false          # drain birds unheard for fade_hours (default: true)
    fresh = true          # outline birds heard in the last fresh_minutes (default: true)
    dither = "spectra6"   # "none" (default): the shot, scaled, in full colour
                          # "spectra6": every pixel one of the six pure inks
    saturation = 0.6      # spectra6 only (default: the frame's saturation)
    margin = 8            # paper kept round the birds, in panel pixels (default: 8)

Why dither here and not on the panel: ESPHome's Spectra 6 driver maps each
pixel to the nearest ink by thresholds and does not dither, so an illustration
arrives as flat posterised blocks. "spectra6" dithers the way the Inky library
does for the 13.3" frame (Floyd-Steinberg onto a blend of the measured inks and
the pure ones, `saturation` choosing how much of each), then writes the PURE
ink colours, which the driver maps exactly.
"""
from __future__ import annotations

import os
import re
import time

from PIL import Image, ImageChops

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

import display

# The Spectra 6 inks in panel order, as pure RGB (what the panel driver is
# sent) and as the Inky library measured them on the glass (SATURATED_PALETTE
# in inky_e673 / inky_el133uf1).
PURE = [(0, 0, 0), (255, 255, 255), (255, 255, 0), (255, 0, 0), (0, 0, 255), (0, 255, 0)]
MEASURED = [(0, 0, 0), (161, 164, 165), (208, 190, 71), (156, 72, 75), (61, 59, 94), (58, 91, 70)]

DITHERS = ("none", "spectra6")
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# Least share of the shot the birds may cover. A plate with one bird on it is
# several percent; a render whose illustrations never arrived is specks.
MIN_INK = 0.01
# Pixels this far from the paper colour count as birds when cropping.
INK_THRESHOLD = 24


def _defaults(cfg):
    return {"names": bool(cfg["bird_names"]), "title": True, "fade": True, "fresh": True,
            "dither": "none", "saturation": float(cfg["saturation"]), "margin": 8}


def check(name, raw, cfg):
    """One plate's settings, filled in from the frame's and checked. Raises
    ValueError naming the setting, so a typo says which one."""
    if not NAME.match(name) or name in ("frame", "preview"):
        raise ValueError(f"[{name}]: a plate name is lowercase letters, digits, - and _, "
                         f"and not 'frame' or 'preview' (those are taken)")
    plate = _defaults(cfg)
    unknown = sorted(set(raw) - set(plate) - {"width", "height"})
    if unknown:
        raise ValueError(f"[{name}]: unknown setting(s) {', '.join(unknown)}")
    plate.update(raw)
    plate["name"] = name
    for key in ("width", "height"):
        value = plate.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not 16 <= value <= 4000:
            raise ValueError(f"[{name}]: {key} must be a whole number of pixels, 16-4000")
    for key in ("names", "title", "fade", "fresh"):
        if not isinstance(plate[key], bool):
            raise ValueError(f"[{name}]: {key} must be true or false")
    if plate["dither"] not in DITHERS:
        raise ValueError(f"[{name}]: dither must be one of {', '.join(DITHERS)}")
    if not isinstance(plate["saturation"], (int, float)) or not 0 <= plate["saturation"] <= 1:
        raise ValueError(f"[{name}]: saturation must be between 0 and 1")
    if not isinstance(plate["margin"], int) or not 0 <= plate["margin"] < min(plate["width"], plate["height"]) // 2:
        raise ValueError(f"[{name}]: margin must be a whole number of pixels, under half the panel")
    return plate


def load(path, cfg):
    """The plates in plates.toml, in file order; none when there is no file."""
    if not path or not os.path.isfile(path):
        return []
    with open(path, "rb") as f:
        tables = tomllib.load(f)
    plates = []
    for name, raw in tables.items():
        if not isinstance(raw, dict):
            raise ValueError(f"{name} = ...: every setting belongs under a [plate] table")
        plates.append(check(name, raw, cfg))
    return plates


def from_query(query, cfg):
    """A plate from /preview.png's query string, for trying a size or a look
    before putting it in plates.toml. Same settings, same checks."""
    raw = {}
    for key, values in query.items():
        value = values[-1]
        if key in ("width", "height", "margin"):
            try:
                raw[key] = int(value)
            except ValueError:
                raise ValueError(f"{key} must be a whole number")
        elif key in ("names", "title", "fade", "fresh"):
            raw[key] = value.lower() not in ("0", "false", "no", "off", "")
        elif key == "saturation":
            try:
                raw[key] = float(value)
            except ValueError:
                raise ValueError("saturation must be a number")
        else:
            raw[key] = value
    # "preview" is a name plates.toml may not use, so check under another.
    try:
        plate = check("preview-plate", raw, cfg)
    except ValueError as e:
        raise ValueError(str(e).replace("[preview-plate]: ", "")) from None
    plate["name"] = "preview"
    return plate


def signature(plate, species, anchor, cfg):
    """What has to change before this plate is drawn again: its birds, as the
    frame's signature counts them, minus the fade or the outline when the plate
    leaves those off (a bird draining is no reason to redraw a plate that does
    not drain), plus the plate's own settings."""
    fresh = display.fresh_slugs(species, anchor, cfg["fresh_minutes"]) if plate["fresh"] else frozenset()
    fade = display.fade_steps(species, anchor, cfg["fade_hours"], cfg["hours"]) if plate["fade"] else None
    return (display.signature(species, fresh, fade), tuple(sorted(plate.items())))


def shoot_args(plate, cfg):
    """shoot()'s arguments for this plate: the frame's capture, at the plate's
    size and with its text, fade and outline settings."""
    w, h = plate["width"], plate["height"]
    args = dict(
        vw=w, vh=h, dsf=2, lowercase=cfg["shoot_lowercase"], mat=0.01,
        # xBias over yBias packs the flock wide; a landscape plate wants it as
        # wide as the panel. A portrait one keeps the frame's own shape.
        cluster_xbias=round(w / h, 2) if w > h else 1.0,
        cluster_ybias=1.0 if w > h else cfg["shoot_cluster_ybias"],
        small_floor=cfg["shoot_small_floor"], count_exp=cfg["shoot_count_exp"],
        timeout_ms=cfg["timeout"] * 1000, user=cfg["basic_user"], password=cfg["basic_pass"],
        window_hours=cfg["hours"], bird_names=plate["names"],
        fresh_minutes=cfg["fresh_minutes"] if plate["fresh"] else 0,
        fade=display.fade_param(cfg) if plate["fade"] else "0",
        label_scale=1.0)
    if plate["title"]:
        args.update(title=cfg["shoot_title"], subtitle=cfg["shoot_subtitle"],
                    headline_px=cfg["shoot_headline_px"], eyebrow_px=cfg["shoot_eyebrow_px"],
                    collage_vh=cfg["shoot_collage_vh"])
    else:
        args.update(title="", subtitle="", headline_px=1, eyebrow_px=1, collage_vh=92)
    return args


def dither_spectra6(img, saturation):
    """The Inky library's dither, written back as pure inks."""
    palette = []
    for measured, pure in zip(MEASURED, PURE):
        palette += [int(measured[i] * saturation + pure[i] * (1 - saturation)) for i in range(3)]
    p = Image.new("P", (1, 1))
    p.putpalette(palette + [0] * (768 - len(palette)))
    q = img.convert("RGB").quantize(6, palette=p, dither=Image.Dither.FLOYDSTEINBERG)
    q.putpalette([c for rgb in PURE for c in rgb] + [0] * (768 - 3 * len(PURE)))
    return q.convert("RGB")


def finish(shot, plate):
    """(the plate's picture, the share of the shot that is birds): the shot
    cropped to what is on it, scaled to fill the panel inside the margin,
    centred on paper, and dithered if the plate asks."""
    shot = shot.convert("RGB")
    paper = shot.getpixel((0, 0))
    mask = ImageChops.difference(shot, Image.new("RGB", shot.size, paper)).convert("L")
    mask = mask.point(lambda v: 255 if v > INK_THRESHOLD else 0)
    box = mask.getbbox()
    w, h, m = plate["width"], plate["height"], plate["margin"]
    canvas = Image.new("RGB", (w, h), paper)
    if not box:
        return canvas, 0.0
    ink = mask.histogram()[255] / (shot.width * shot.height)
    content = shot.crop(box)
    s = min((w - 2 * m) / content.width, (h - 2 * m) / content.height)
    content = content.resize((max(1, round(content.width * s)), max(1, round(content.height * s))),
                             Image.LANCZOS)
    canvas.paste(content, ((w - content.width) // 2, (h - content.height) // 2))
    if plate["dither"] == "spectra6":
        canvas = dither_spectra6(canvas, plate["saturation"])
    return canvas, ink


def render(plate, cfg, out, why):
    """Shoot, finish and publish one plate at `out`. True when a new picture
    was published; a render that fails the ink check keeps the old one."""
    from shoot import shoot
    base, _ = os.path.splitext(out)
    directory, filename = os.path.split(base)
    # Dotted, so a directory listing never offers a half-made plate, and still
    # .png, because playwright picks the screenshot format from the extension.
    shot_path = os.path.join(directory, f".{filename}-shot.png")
    partial = os.path.join(directory, f".{filename}-partial.png")
    started = time.time()
    try:
        shoot(cfg["base_url"], shot_path, **shoot_args(plate, cfg))
        with Image.open(shot_path) as shot:
            img, ink = finish(shot, plate)
    finally:
        if os.path.exists(shot_path):
            os.unlink(shot_path)
    if ink < MIN_INK:
        print(f"REFUSED {plate['name']} ({why}): the shot is {ink:.2%} birds - the "
              f"illustrations did not make it in. Keeping the previous one.", flush=True)
        return False
    img.save(partial, optimize=True)
    os.replace(partial, out)  # atomic: a panel never fetches half a file
    print(f"rendered {plate['name']} ({why}) -> {out} {os.path.getsize(out)} bytes, "
          f"{ink:.1%} birds, {time.time() - started:.0f}s", flush=True)
    return True
