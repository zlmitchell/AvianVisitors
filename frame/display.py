#!/usr/bin/env python3
"""Frame-Pi client: turn a collage screenshot into Inky panel pixels.

Runs on the frame Pi (a 3 A+ or Zero 2 W) on a systemd timer. Each run it decides whether a
refresh is worth it (the species set or call-count brackets changed, and it
is not quiet hours), then crops the title and collage from the screenshot,
centres and mats them, and pushes the result to the Inky Impression 13.3".
``--preview out.png`` writes an approximate 6-ink dither instead, so the
look can be checked on any machine without the panel.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import inspect
import io
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timedelta

from PIL import Image, ImageChops, ImageDraw

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

PANEL_W, PANEL_H = 1200, 1600  # portrait; the panel itself is 1600x1200

# Approximate Spectra-6 inks, used only for --preview. On hardware the Inky
# library maps to the panel's real palette.
SPECTRA6 = [(236, 234, 223), (26, 26, 28), (165, 60, 56),
            (198, 176, 74), (49, 71, 130), (58, 110, 72)]

DEFAULTS = {
    "base_url": "http://birdnet.local",
    "species_source": "",   # "" = the recent API at base_url; "birdweather" = BirdWeather near a ZIP
    "zip": "",              # BirdWeather ZIP / postal code (with species_source = "birdweather")
    "bw_days": 7,           # BirdWeather lookback window, in days
    "bw_country": "us",     # geocoder country for the ZIP
    # The window shown on the frame, and the thing the fade below is drawn
    # over: a bird is at full strength for `fade_hours` and then drains until
    # this drops it. At 24 there is no tail, so nothing fades - which is the
    # default, because widening the window is not free. Tile areas are shares of
    # one budget, so twice the birds is roughly half the area each: measured on
    # a plausible day's counts, 48h in the A5 opening takes the median bird from
    # 96px to 54px and pins most of the plate on the minimum-tile floor. Widen
    # this only together with the opening - see the bare-panel block in
    # config.example.toml, which moves both.
    "hours": 24,
    "image": "",            # local PNG written by the shooter
    "image_url": "",        # or a published screenshot URL
    "shoot": False,         # or capture inline (needs a browser; the 3 A+ and Zero 2 W both handle it)
    "shoot_title": None, "shoot_subtitle": None,
    "shoot_headline_px": 42, "shoot_eyebrow_px": 18, "shoot_lowercase": False,
    "shoot_mat": 0.04, "shoot_small_floor": 0.04, "shoot_count_exp": 0.65,
    # How much of the browser viewport the collage gets. The shot is a fixed
    # 1200x1600 whatever this is, so it decides how many source pixels the birds
    # (and the names written along them) are drawn with before the matting step
    # resamples them onto the panel. 52 was sized for the A5 opening, which threw
    # over half of them away; a full-panel opening wants the collage drawn close
    # to panel size in the first place. The rest of the viewport has to hold the
    # title and the stage padding, so this cannot go much past 76.
    "shoot_collage_vh": 52,
    "bird_names": True,
    "fresh_minutes": 30,    # outline birds heard this recently; 0 turns the mark off
    # Hours of silence before a bird starts losing its colour. It finishes at
    # `hours`, where the window drops it, so the ramp is fade_hours -> hours.
    # 0 turns fading off. This sits at 24 with `hours` also at 24, which means
    # no ramp and nothing faded: raise `hours` and the fade appears on its own,
    # with no second setting to remember.
    "fade_hours": 24,
    "mat": 0.0,             # extra global shrink of the content inside the opening
    # The opening is the rectangle the content is fitted into. `opening` is its
    # height as a fraction of the panel and `opening_aspect` is its width over
    # its height. The defaults are the A5 window in the A4 frame from the BOM,
    # which is what an unmodified kit has in front of the panel. Taking the
    # matboard out is opening = 0.97, opening_aspect = 0.75 - the panel's own
    # 1200x1600 - and wants the four numbers below moved with it.
    "opening": 0.7071,
    "opening_aspect": 0.7071,
    # How the title and collage divide the opening.
    #
    # title_frac and gap_frac are fractions of the PANEL, not of the opening.
    # Type is a physical size - a title is legible from across the room or it
    # is not, and that has nothing to do with how much of the glass the mat
    # leaves showing. Measured against the opening, a bigger opening enlarged
    # the title with it, which is exactly the room a bigger opening was opened
    # up to give the birds. Against the panel, one number holds the title at
    # 74px whatever is cut in front of it, and the whole of the extra opening
    # goes to the collage. 0.046 and 0.071 are what the A5 mat always produced.
    #
    # collage_frac is a fraction of the opening's WIDTH, and stays that way:
    # the collage is the thing that is supposed to grow with the opening.
    "title_frac": 0.046,
    "collage_frac": 0.66,
    "gap_frac": 0.071,
    # Type size for the bird names, as a multiplier on what the tile asks for.
    # 0 holds the handwriting at the physical size the A5 mat gives it, whatever
    # the opening, by cancelling out the collage's own scale-up - see
    # label_scale(). A positive value overrides that outright.
    "label_scale": 0,
    "rotate": 90,           # 90 or 270 if the frame hangs the other way up
    "saturation": 0.6,
    "panel": "",            # "el133uf1" forces the 13.3" driver if auto() fails
    "quiet_start": 0, "quiet_end": 0,    # 0/0 = no quiet hours
    "heal_hours": 24,
    "state": "~/.birdframe/state.json",
    "cache": "~/.birdframe",
    "timeout": 180,      # seconds; a Zero 2 W needs ~70-120s to shoot the collage
    "basic_user": None, "basic_pass": None,
}


def _auth(cfg):
    if not cfg.get("basic_user"):
        return None
    raw = f"{cfg['basic_user']}:{cfg.get('basic_pass') or ''}".encode()
    return "Basic " + base64.b64encode(raw).decode()


# --- change detection -------------------------------------------------------
def slugify(sci):
    return re.sub(r"[^a-z0-9]+", "-", sci.lower()).strip("-")


def _bucket(n):
    for i, edge in enumerate((1, 2, 5, 15, 40, 100, 300, 1000)):
        if n <= edge:
            return i
    return 8


def fetch_recent(base, hours, timeout, auth=None):
    """The recent-window payload, whole. The species list is what gets drawn,
    and `anchor` is the station's local clock at the moment it answered, which
    is the only clock `last_seen` can be compared against."""
    url = f"{base.rstrip('/')}/avian/api/birdnet-api.php?action=recent&hours={hours}"
    req = urllib.request.Request(url, headers={"User-Agent": "AvianVisitors-frame/1.0"})
    if auth:
        req.add_header("Authorization", auth)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read(2_000_000))
    return payload.get("species", []) or [], payload.get("anchor")


def parse_station_ts(value):
    """A station timestamp ("YYYY-MM-DD HH:MM:SS", no zone) as a naive
    datetime, or None. Naive on purpose: BirdNET-Pi writes Date and Time in
    the station's local clock and `anchor` comes from the same clock, so both
    sides of the comparison are in it and neither needs a zone."""
    if not value:
        return None
    text = str(value).strip().replace("T", " ")
    for width, fmt in ((19, "%Y-%m-%d %H:%M:%S"), (16, "%Y-%m-%d %H:%M")):
        try:
            return datetime.strptime(text[:width], fmt)
        except ValueError:
            continue
    return None


def fresh_slugs(species, anchor, minutes):
    """The slugs the collage will outline: heard within `minutes` of the API's
    own anchor. This mirrors apt.js's fresh window exactly - same anchor, same
    last_seen, same arithmetic - so the signature changes on precisely the
    renders that would draw a different set of outlines.

    Falls back to this Pi's clock only when the payload carries no anchor
    (BirdWeather mode, or an older BirdNET-Pi), and returns nothing at all when
    the window is off or no species carries a last_seen."""
    if not minutes or minutes <= 0:
        return frozenset()
    now = parse_station_ts(anchor) or datetime.now()
    cutoff = now - timedelta(minutes=minutes)
    out = set()
    for s in species:
        seen = parse_station_ts(s.get("last_seen"))
        if seen is not None and seen >= cutoff:
            out.add(slugify(s["sci"]))
    return frozenset(out)


FADE_STEPS = 5  # must match FADE_STEPS in apt.js and the .gtile[data-fade=N] rules


def fade_steps(species, anchor, fade_hours, window_hours, steps=FADE_STEPS):
    """slug -> how far into the fade the collage will draw it, 1..steps. Mirrors
    apt.js's fadeStep exactly, on the same anchor and the same last_seen, so
    what this counts is what the renderer will actually put on the panel.

    Birds still at full strength are simply absent from the result, as are
    species with no last_seen at all (BirdWeather), and the whole thing is empty
    when there is no tail to fade over."""
    if not fade_hours or fade_hours <= 0 or not window_hours or window_hours <= fade_hours:
        return {}
    now = parse_station_ts(anchor) or datetime.now()
    out = {}
    for s in species:
        seen = parse_station_ts(s.get("last_seen"))
        if seen is None:
            continue
        quiet = (now - seen).total_seconds() / 3600.0
        if quiet <= fade_hours:
            continue
        through = min(1.0, (quiet - fade_hours) / (window_hours - fade_hours))
        step = min(steps, math.ceil(through * steps))
        if step > 0:
            out[slugify(s["sci"])] = step
    return out


def signature(species, fresh=frozenset(), fade=None):
    """What has to change before the panel is worth its twelve seconds: the
    species on it, their count bracket, whether each is wearing the fresh
    outline, and how faded each is.

    Both time-based terms are in here rather than on clocks of their own
    because the panel has no partial refresh. A bird crossing the fresh window
    or stepping down the fade ramp is the only thing about the passage of time
    that changes a pixel; a detection that moves neither is not worth a redraw.
    The count is bracketed for the same reason, and the renderer sizes its tiles
    off the same brackets, so a refresh driven by a mark redraws the identical
    plate with only that mark changed - no bird moves."""
    fade = fade or {}
    items = []
    for s in species:
        slug = slugify(s["sci"])
        items.append((slug, _bucket(int(s.get("n") or 1)), slug in fresh, fade.get(slug, 0)))
    return hashlib.sha256(json.dumps(sorted(items)).encode()).hexdigest()[:16]


def fetch_species(cfg, auth=None):
    """(species, anchor) for the signature and the render: the BirdNET-Pi
    recent API by default, or BirdWeather's recent detections near a ZIP when
    species_source = "birdweather". BirdWeather reports no per-species
    last_seen, so it has no anchor and no bird is ever outlined there."""
    if cfg.get("species_source") == "birdweather":
        import birdweather
        return birdweather.species_for_zip(cfg["zip"], country=cfg["bw_country"], days=cfg["bw_days"]), None
    return fetch_recent(cfg["base_url"], cfg["hours"], cfg["timeout"], auth)


# --- image ------------------------------------------------------------------
def get_image(src, timeout, auth=None):
    if re.match(r"^https?://", src):
        req = urllib.request.Request(src, headers={"User-Agent": "AvianVisitors-frame/1.0"})
        if auth:
            req.add_header("Authorization", auth)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Image.open(io.BytesIO(r.read(20_000_000))).convert("RGB")
    return Image.open(os.path.expanduser(src)).convert("RGB")


def fit_panel(img):
    if img.size != (PANEL_W, PANEL_H):
        img = img.resize((PANEL_W, PANEL_H), Image.LANCZOS)
    return img


def _paper(img):
    """Median of the four corners, robust to a stray inked corner."""
    w, h = img.size
    px = (img.getpixel(p) for p in ((4, 4), (w - 5, 4), (4, h - 5), (w - 5, h - 5)))
    return tuple(int(statistics.median(c)) for c in zip(*px))


def _frac(value, name, hi=1.0):
    """A config fraction, validated. Rejects bool explicitly: True is a float
    of 1.0 to Python and would silently mean "the whole panel"."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be greater than 0 and at most {hi:g}")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be greater than 0 and at most {hi:g}") from exc
    if not 0 < value <= hi:
        raise ValueError(f"{name} must be greater than 0 and at most {hi:g}")
    return value


# The opening is the rectangle the content is fitted into, centred in the
# panel. `opening` is its height as a fraction of the panel and `aspect` is its
# width over its height, so the two together describe a bare panel (0.97 and
# the panel's own 0.75) and any mat cut for one (an A5 window is 0.7071 and
# 0.7071) without the caller having to know which is which. A tall aspect can
# ask for more width than the panel has, so the result is pulled back to fit
# rather than letting content run off the glass.
def opening_size(opening, aspect=0.75):
    opening = _frac(opening, "opening")
    aspect = _frac(aspect, "opening_aspect", hi=10.0)
    h = PANEL_H * opening
    w = h * aspect
    if w > PANEL_W:
        h *= PANEL_W / w
        w = PANEL_W
    return w, h


def _place(content, paper, mat, opening, aspect=0.75):
    box_w, box_h = opening_size(opening, aspect)
    s = min(box_w * (1 - mat) / content.width, box_h * (1 - mat) / content.height)
    nw, nh = max(1, round(content.width * s)), max(1, round(content.height * s))
    content = content.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (PANEL_W, PANEL_H), paper)
    canvas.paste(content, ((PANEL_W - nw) // 2, (PANEL_H - nh) // 2))
    return canvas


def _region_bbox(img, paper, y0, y1):
    region = img.crop((0, y0, img.width, y1))
    diff = ImageChops.difference(region, Image.new("RGB", region.size, paper))
    bb = diff.convert("L").point(lambda p: 255 if p > 34 else 0).getbbox()
    return None if not bb else (bb[0], y0 + bb[1], bb[2], y0 + bb[3])


def _scale_w(img, target_w):
    s = target_w / img.width
    return img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)


def _scale_h(img, target_h):
    s = target_h / img.height
    return img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)


def _centroid_x(img, paper):
    """Horizontal centre of ink weight (what the eye reads as centred)."""
    m = ImageChops.difference(img, Image.new("RGB", img.size, paper)).convert("L")
    cols = list(m.resize((img.width, 1), Image.BOX).tobytes())
    total = sum(cols) or 1
    return sum(x * v for x, v in enumerate(cols)) / total


# Content layout inside the opening: the title and collage are sized
# independently (title as a fraction of the opening height, collage of its
# width), so tuning one leaves the other untouched. gap is a fraction of the
# opening height. These are the shipped defaults; every one is a config key,
# because a bare panel and an A5 mat want very different numbers and the whole
# point of enlarging the opening is that the content grows with it.
TITLE_H_FRAC, COLLAGE_FRAC, GAP_FRAC = 0.046, 0.66, 0.071

# The collage scale factor the A5 mat produces. That is the layout apt.js's
# handwriting sizes were chosen against, so it is what "the size the names have
# always been" means. See label_scale().
LABEL_REFERENCE_SCALE = 0.44


def layout_of(cfg):
    """Everything mat_and_center needs, pulled out of config. Every fraction is
    validated here rather than deep in the resize, so a typo in config.toml
    fails loudly instead of quietly mis-sizing the panel."""
    opening = _frac(cfg.get("opening", 0.7071), "opening")
    aspect = _frac(cfg.get("opening_aspect", 0.7071), "opening_aspect", hi=10.0)
    return {
        "mat": cfg.get("mat", 0.0) or 0.0,
        "opening": opening,
        "aspect": aspect,
        "title": _frac(cfg.get("title_frac", TITLE_H_FRAC), "title_frac"),
        "collage": _frac(cfg.get("collage_frac", COLLAGE_FRAC), "collage_frac"),
        "gap": _frac(cfg.get("gap_frac", GAP_FRAC), "gap_frac"),
    }


def collage_scale(cfg):
    """How much the collage is shrunk on its way to the panel, in the
    width-bound case that decides it in practice. The shot is rendered at panel
    width, so the source collage is PANEL_W across and this is just the share of
    that width the opening hands back to it."""
    lay = layout_of(cfg)
    ow, _ = opening_size(lay["opening"], lay["aspect"])
    return ow * (1 - lay["mat"]) * lay["collage"] / PANEL_W


def label_scale(cfg):
    """What to multiply the bird names' type size by, so that a name is the same
    physical size on the panel whatever the opening.

    The names are sized from their tile inside the browser, and the whole
    collage is then scaled onto the panel - so opening the mat up, which scales
    the collage less, silently enlarged the type along with the birds. That is
    the opposite of the point: a name is legible or it is not, and the room a
    larger opening buys belongs to the drawings. Cancelling the collage's own
    scale-up holds the type still and hands that room over.

    Only ever shrinks. An opening smaller than the A5 reference would want type
    larger than the tile can carry, and apt.js's own cap is the better judge of
    that than a ratio is."""
    override = cfg.get("label_scale") or 0
    if override > 0:
        return float(override)
    scale = collage_scale(cfg)
    return min(1.0, LABEL_REFERENCE_SCALE / scale) if scale > 0 else 1.0


def mat_and_center(img, mat, opening, aspect=0.75,
                   title_frac=TITLE_H_FRAC, collage_frac=COLLAGE_FRAC, gap_frac=GAP_FRAC):
    """Crop the title and collage, size each to a fraction of the opening,
    stack with a gap, and centre on the panel."""
    img = img.convert("RGB")
    paper = _paper(img)
    mask = ImageChops.difference(img, Image.new("RGB", img.size, paper))
    mask = mask.convert("L").point(lambda p: 255 if p > 34 else 0)
    full = mask.getbbox()
    if not full:
        return img
    levels = list(mask.resize((1, img.height), Image.BOX).tobytes())  # per-row content
    top, bot = full[1], full[3]
    split, run = None, 0
    for y in range(top, bot):
        if levels[y] <= 2:
            run += 1
            if run >= 60:  # split below the headline; a 60px band clears the ~30px eyebrow/headline gap so the title stays whole
                cy = y
                while cy < bot and levels[cy] <= 2:
                    cy += 1
                split = (y - run + 1, cy)
                break
        else:
            run = 0
    tb = _region_bbox(img, paper, top, split[0]) if split else None
    cb = _region_bbox(img, paper, split[1], bot + 1) if split else None
    ow, oh = opening_size(opening, aspect)
    box_w, box_h = ow * (1 - mat), oh * (1 - mat)
    if not (tb and cb):
        return _place(img.crop(full), paper, mat, opening, aspect)
    # Both measured against the panel, so the title and the space under it are
    # the same physical size whatever the opening. Clamped against the opening
    # all the same: a small enough mat could otherwise ask for a title taller
    # than the window it has to sit in.
    title = _scale_h(img.crop(tb), min(PANEL_H * title_frac, box_h * 0.30))
    gap = round(min(PANEL_H * gap_frac, box_h * 0.20))
    # Size the collage to fill the room left under the fixed-size title,
    # binding on whichever of width or remaining height runs out first, so the
    # title stays a consistent size whether the collage is tall or compact
    # instead of ballooning when the collage happens to be short.
    coll = img.crop(cb)
    cs = min(box_w * collage_frac / coll.width, (box_h - title.height - gap) / coll.height)
    collage = coll.resize((max(1, round(coll.width * cs)), max(1, round(coll.height * cs))), Image.LANCZOS)
    ccx = _centroid_x(collage, paper)  # centre the collage by ink weight, not bbox
    half = max(ccx, collage.width - ccx)
    # A wildly off-centre collage can push the centroid-mirrored width (2*half)
    # past the opening; shrink only the collage, never the fixed-size title,
    # so nothing spills under the physical mat (or off the glass on a bare panel).
    if 2 * half > box_w:
        s = box_w / (2 * half)
        collage = collage.resize((max(1, round(collage.width * s)), max(1, round(collage.height * s))), Image.LANCZOS)
        ccx = round(ccx * s)
        half = max(ccx, collage.width - ccx)
    cw = round(max(title.width, 2 * half))
    comp = Image.new("RGB", (cw, title.height + gap + collage.height), paper)
    comp.paste(title, ((cw - title.width) // 2, 0))
    comp.paste(collage, (round(cw / 2 - ccx), title.height + gap))
    canvas = Image.new("RGB", (PANEL_W, PANEL_H), paper)
    canvas.paste(comp, ((PANEL_W - comp.width) // 2, (PANEL_H - comp.height) // 2))
    return canvas


def quantize_spectra6(img):
    pal = Image.new("P", (1, 1))
    flat = [c for ink in SPECTRA6 for c in ink]
    flat += list(SPECTRA6[0]) * ((768 - len(flat)) // 3)  # pad the 256-entry palette with paper
    pal.putpalette(flat[:768])
    return img.convert("RGB").quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG).convert("RGB")


def _draw_mat_box(img, opening, aspect=0.75):
    """Dev aid: outline the configured mat opening."""
    ow, oh = opening_size(opening, aspect)
    x0, y0 = round((PANEL_W - ow) / 2), round((PANEL_H - oh) / 2)
    ImageDraw.Draw(img).rectangle((x0, y0, PANEL_W - x0 - 1, PANEL_H - y0 - 1),
                                  outline=(170, 60, 56), width=2)


# --- hardware ---------------------------------------------------------------
def push_panel(img, rotate, saturation, panel=""):
    """Rotate to the panel's landscape buffer and push. Lazy import so this
    module still loads on a machine without the Inky library."""
    if rotate not in (90, 270):
        print(f"rotate must be 90 or 270, not {rotate}; using 90", file=sys.stderr)
        rotate = 90
    if panel == "el133uf1":
        from inky.inky_el133uf1 import Inky
        dev = Inky(resolution=(1600, 1200))
    else:
        from inky.auto import auto
        dev = auto()
    buf = img.rotate(rotate, expand=True)
    if buf.size != (dev.width, dev.height):
        buf = buf.resize((dev.width, dev.height), Image.LANCZOS)
    kw = {"saturation": saturation} if "saturation" in inspect.signature(dev.set_image).parameters else {}
    dev.set_image(buf, **kw)
    dev.show()


# --- state ------------------------------------------------------------------
def load_state(path):
    try:
        with open(os.path.expanduser(path)) as f:
            return json.load(f)
    except Exception:
        return {"signature": None, "last_refresh": 0}


def save_state(path, sig, when):
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"signature": sig, "last_refresh": when}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # atomic: a power cut can't leave a half-written file


def in_quiet_hours(cfg, hour):
    s, e = cfg["quiet_start"], cfg["quiet_end"]
    if s == e:
        return False
    return s <= hour < e if s < e else hour >= s or hour < e


def fade_param(cfg):
    """The renderer's ?fade= value: "<start>-<end>" in hours, or "0" when there
    is no tail to fade over (fading off, or the window no wider than the point
    the fade would start at)."""
    start = cfg.get("fade_hours") or 0
    window = cfg.get("hours") or 0
    return f"{int(start)}-{int(window)}" if 0 < start < window else "0"


def frame_url(url, bird_names, fresh_minutes=None, fade=None):
    """Set the frame's label, fresh-outline and fade preferences without
    disturbing other URL state. A None leaves the source's own setting alone."""
    import urllib.parse
    parts = urllib.parse.urlsplit(url)
    drop = {"labels"}
    if fresh_minutes is not None:
        drop.add("fresh")
    if fade is not None:
        drop.add("fade")
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if k not in drop]
    query.append(("labels", "1" if bird_names else "0"))
    if fresh_minutes is not None:
        query.append(("fresh", str(max(0, int(fresh_minutes)))))
    if fade is not None:
        query.append(("fade", fade))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


# --- run --------------------------------------------------------------------
def obtain_image(cfg, species=None):
    if cfg.get("species_source") == "birdweather":
        from shoot import shoot_birdweather
        if species is None:  # gate skipped (--no-signature): fetch the list to render
            species, _ = fetch_species(cfg, _auth(cfg))
        out = os.path.join(os.path.expanduser(cfg["cache"]), "frame.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shoot_birdweather(out, species, title=cfg["shoot_title"], subtitle=cfg["shoot_subtitle"],
                          timeout_ms=cfg["timeout"] * 1000, bird_names=cfg["bird_names"],
                          collage_vh=cfg["shoot_collage_vh"], label_scale=label_scale(cfg))
        return Image.open(out).convert("RGB")
    if cfg["shoot"]:
        from shoot import shoot
        out = os.path.join(os.path.expanduser(cfg["cache"]), "shot.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shoot(cfg["base_url"], out, title=cfg["shoot_title"], subtitle=cfg["shoot_subtitle"],
              headline_px=cfg["shoot_headline_px"], eyebrow_px=cfg["shoot_eyebrow_px"],
              lowercase=cfg["shoot_lowercase"], mat=cfg["shoot_mat"],
              small_floor=cfg["shoot_small_floor"], count_exp=cfg["shoot_count_exp"], timeout_ms=cfg["timeout"] * 1000,
              user=cfg["basic_user"], password=cfg["basic_pass"], window_hours=cfg["hours"],
              bird_names=cfg["bird_names"], fresh_minutes=cfg["fresh_minutes"],
              fade=fade_param(cfg), collage_vh=cfg["shoot_collage_vh"],
              label_scale=label_scale(cfg))
        return Image.open(out).convert("RGB")
    src = cfg["image_url"] or cfg["image"]
    if not src:
        raise ValueError("set image, image_url, or shoot in config")
    # A pre-rendered frame is still someone's render, so ask it for names and
    # the fresh window the same way this Pi asks its own browser. A source that
    # does not know the parameters ignores them and sends what it always sent,
    # so this is safe against anything. URLs only: a local file path has no
    # query string.
    if cfg["image_url"]:
        src = frame_url(src, cfg["bird_names"], cfg["fresh_minutes"], fade_param(cfg))
    return get_image(src, cfg["timeout"], _auth(cfg))


def run(cfg, preview=None, force=False, use_signature=True, mat_box=False):
    now = time.time()
    state = load_state(cfg["state"])
    sig = None
    species = None
    if use_signature:
        try:
            species, anchor = fetch_species(cfg, _auth(cfg))
            fresh = fresh_slugs(species, anchor, cfg["fresh_minutes"])
            fading = fade_steps(species, anchor, cfg["fade_hours"], cfg["hours"])
            sig = signature(species, fresh, fading)
            # What the renderer is about to draw, in the journal. Without this
            # the only way to tell an outline that is off from one that simply
            # has no bird to sit on is to go and look at the panel.
            print(f"{len(species)} species, {len(fresh)} singing, {len(fading)} fading")
        except Exception as e:
            print(f"signature fetch failed: {e}", file=sys.stderr)  # treat as no change
    heal_due = now - state.get("last_refresh", 0) >= cfg["heal_hours"] * 3600
    changed = (not use_signature) or (sig is not None and sig != state.get("signature"))
    if not force and not preview:
        if in_quiet_hours(cfg, datetime.now().hour):
            print("quiet hours; skip")
            return
        if not changed and not heal_due:
            print("no change; skip")
            return
        print("refresh:", "changed" if changed else "heal")

    try:
        img = fit_panel(obtain_image(cfg, species))
    except Exception as e:
        print(f"could not get image: {e}", file=sys.stderr)  # keep last panel image
        return
    lay = layout_of(cfg)
    img = mat_and_center(img, lay["mat"], lay["opening"], lay["aspect"],
                         lay["title"], lay["collage"], lay["gap"])
    if preview:
        out = quantize_spectra6(img)
        if mat_box:
            _draw_mat_box(out, lay["opening"], lay["aspect"])
        out.save(preview)
        print(f"wrote preview {preview}")
        return
    try:
        push_panel(img, cfg["rotate"], cfg["saturation"], cfg.get("panel", ""))
    except Exception as e:
        print(f"panel push failed: {e}", file=sys.stderr)
        return
    save_state(cfg["state"], sig if sig is not None else state.get("signature"), now)
    print("panel updated")


def _toml_error(path, exc):
    """tomllib names neither the file nor the text that broke it - on a headless
    frame that means a journal full of parser traceback and a walk over to the
    Pi to find out which line it meant. Quote the line and point at the column."""
    out = [f"{path} is not valid TOML: {exc}"]
    where = re.search(r"at line (\d+), column (\d+)", str(exc))
    if where:
        line_no, col = int(where.group(1)), int(where.group(2))
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            lines = []
        if 0 < line_no <= len(lines):
            prefix = f"  {line_no} | "
            out.append(prefix + lines[line_no - 1])
            out.append(" " * (len(prefix) + col - 1) + "^")
    out.append('  Text needs "double quotes"; true/false and numbers go bare. '
               "A # starts a comment.")
    if where and 0 < int(where.group(1)) <= len(lines):
        bad = lines[int(where.group(1)) - 1]
        if re.search(r"=\s*(on|off|yes|no)\s*$", bad, re.I):
            out.append("  TOML has no on/off - write true or false.")
        if re.match(r"\s*birdframe-\w+\s*=", bad):
            out.append("  birdframe-names is a command you run in a shell, not a setting. "
                       "Delete this line; names are on by default.")
    return "\n".join(out)


def load_config(path):
    cfg = dict(DEFAULTS)
    if not path:
        return cfg
    path = os.path.expanduser(path)
    try:
        with open(path, "rb") as f:
            loaded = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(_toml_error(path, exc)) from exc
    # A misspelled key parses perfectly well and then does nothing at all, which
    # from the outside is indistinguishable from the setting having no effect.
    # Name them rather than let someone wonder why nothing changed.
    unknown = sorted(k for k in loaded if k not in DEFAULTS)
    if unknown:
        print(f"{path}: ignoring unknown setting(s): {', '.join(unknown)}", file=sys.stderr)
    cfg.update(loaded)
    return cfg


def apply_overrides(cfg, pairs):
    """`-o key=value`, for trying a setting on the panel before committing it to
    config.toml. Nothing is written back.

    Values are read as TOML, so 0.97, 30, true and "el133uf1" all mean what they
    look like, and anything TOML cannot parse is taken as a bare string. An
    unknown key is an error rather than a no-op: a typo that silently changes
    nothing is the worst possible outcome when the thing you are checking is
    whether a setting had any effect."""
    for pair in pairs or ():
        key, sep, raw = pair.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ValueError(f"--set wants KEY=VALUE, got {pair!r}")
        if key not in DEFAULTS:
            raise ValueError(f"unknown config key {key!r}")
        try:
            cfg[key] = tomllib.loads(f"v = {raw.strip()}")["v"]
        except Exception:
            cfg[key] = raw.strip()
    return cfg


def main():
    ap = argparse.ArgumentParser(description="Push the collage screenshot to the Inky panel.")
    ap.add_argument("--config")
    ap.add_argument("--base-url")
    ap.add_argument("--image")
    ap.add_argument("--image-url")
    ap.add_argument("--preview", help="write a 6-ink preview PNG instead of pushing")
    ap.add_argument("--rotate", type=int)
    ap.add_argument("--force", action="store_true", help="refresh even if unchanged")
    ap.add_argument("--no-signature", action="store_true", help="skip change detection")
    ap.add_argument("--mat-box", action="store_true", help="dev: outline the mat window on the preview")
    ap.add_argument("-o", "--set", dest="overrides", action="append", metavar="KEY=VALUE",
                    help="override one config value for this run only, repeatable: "
                         "-o fresh_minutes=0 -o opening=0.7071. Not written to config.toml.")
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
    except ValueError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    for key in ("base_url", "image", "image_url"):
        val = getattr(args, key)
        if val:
            cfg[key] = val
    if args.rotate is not None:
        cfg["rotate"] = args.rotate
    try:
        apply_overrides(cfg, args.overrides)
    except ValueError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    # One render at a time. A manual --force colliding with the timer's run
    # pushes two refreshes into the panel mid-cycle; on the 13.3" (two
    # half-panel controllers) that shows a split image and can wedge one
    # controller until a full power cycle. The lock lives in the cache dir
    # and is dropped automatically on exit.
    #
    # fcntl is imported here rather than at the top because it is Unix-only and
    # this module is also meant to load on a laptop, where --preview is the
    # whole point and there is no panel to serialize access to. Where it is
    # missing there is nothing to protect, so the run simply goes unlocked.
    lock_path = os.path.join(os.path.expanduser(cfg["cache"]), ".render.lock")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    lock = open(lock_path, "w")
    try:
        import fcntl
    except ModuleNotFoundError:
        fcntl = None
    if fcntl is not None:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("another render is in progress; skipping")
            return
    run(cfg, preview=args.preview, force=args.force, use_signature=not args.no_signature, mat_box=args.mat_box)


if __name__ == "__main__":
    main()
