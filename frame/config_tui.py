#!/usr/bin/env python3
"""`birdframe config`: an editor for ~/.birdframe/config.toml.

Every key in display.DEFAULTS is reachable here, grouped the way
config.example.toml groups them; a key added to DEFAULTS shows up on its own,
under "Other" until it is placed in SECTIONS. The point of a screen over hand
editing is not the typing - it is the three things the file cannot do for you:

  * the interlocked layout keys move together as one preset, instead of five
    edits that are only correct as a set,
  * the source can be switched after install, which install.sh refuses to do,
  * and the panel is refreshed once, at the end, and only if something changed.

The file is rewritten a line at a time and never re-serialised from parsed
TOML. The comments in it are the documentation for the frame - losing them to
a round trip would cost far more than this editor is worth - so an existing
line keeps its place and its trailing comment, a commented-out line is
uncommented where it stands, and only a genuinely new key is appended.

That same writer is what install.sh uses to lay the config down in the first
place (`--init MODE`): it copies config.example.toml and sets the two or three
values the mode decides. install.sh used to carry a config template per mode,
which meant four descriptions of every default - here, the reference file, and
DEFAULTS - and they had already drifted apart from each other.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse

FRAME = os.path.dirname(os.path.abspath(__file__))


def _load_display():
    """display.py is a script beside us, not an installed module. It imports
    Inky only inside push_panel, so this loads with no panel attached."""
    spec = importlib.util.spec_from_file_location("birdframe_display",
                                                  os.path.join(FRAME, "display.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


display = _load_display()
DEFAULTS = display.DEFAULTS

# --- schema -----------------------------------------------------------------
# Order and grouping follow config.example.toml, so the screen and the file
# read the same way round. Anything in DEFAULTS and not named here still
# appears, under "Other" - a new setting is never silently unreachable.
SECTIONS = [
    ("Where the collage comes from", [
        "species_source", "base_url", "shoot", "image", "image_url",
        "zip", "bw_days", "bw_country", "hours",
    ]),
    ("Titles and look", [
        "shoot_title", "shoot_subtitle", "bird_names", "shoot_lowercase",
        "shoot_headline_px", "shoot_eyebrow_px", "shoot_mat",
        "shoot_small_floor", "shoot_count_exp", "shoot_collage_vh",
    ]),
    ("Currently singing, and going quiet", ["fresh_minutes", "fade_hours"]),
    ("Panel and layout", [
        "rotate", "saturation", "panel", "mat", "opening", "opening_aspect",
        "title_frac", "title_position", "collage_frac", "gap_frac", "label_scale",
    ]),
    ("Refresh cadence", ["quiet_start", "quiet_end", "heal_hours"]),
    ("Paths and timeouts", ["state", "cache", "timeout"]),
    ("Basic auth - only if your whole site is behind it", ["basic_user", "basic_pass"]),
]

CHOICES = {
    "species_source": ["", "birdweather"],
    "rotate": [90, 270],
    "title_position": ["top", "bottom"],
    "panel": ["", "el133uf1"],
    "bw_country": ["us", "gb", "ca", "au", "de", "fr", "nl", "se"],
}

HELP = {
    "species_source": 'blank = the recent API at base_url; "birdweather" = top birds near a ZIP, no mic',
    "base_url": "the BirdNET-Pi this frame mirrors",
    "shoot": "render the collage on this Pi (needs the browser install.sh puts in)",
    "image": "read a PNG a sibling process drops here, instead of rendering",
    "image_url": "or fetch a ready-made frame PNG from here",
    "zip": "postal code BirdWeather mode reads its birds near",
    "bw_days": "BirdWeather lookback window, in days",
    "bw_country": "geocoder country for the ZIP",
    "hours": "detection window shown on the frame. Twice the birds is roughly half the area each, so widen this with the opening, not on its own",
    "shoot_title": "headline on the plate (blank leaves it out)",
    "shoot_subtitle": "eyebrow line above the headline",
    "bird_names": "write every bird's name along its own outline",
    "shoot_lowercase": "set the title in lower case",
    "shoot_headline_px": "headline type size in the render, before matting",
    "shoot_eyebrow_px": "eyebrow type size in the render",
    "shoot_mat": "padding inside the rendered collage stage",
    "shoot_small_floor": "smallest tile a bird can be given, as a share of the stage",
    "shoot_count_exp": "tile size against call count; higher lets the loudest birds dominate more",
    "shoot_collage_vh": "share of the render viewport the collage gets, so the source pixels the birds are drawn with. 52 suits the A5 mat, 74 a bare panel; much past 76 and the title runs out of viewport",
    "fresh_minutes": "outline a bird heard this recently, in the Spectra red. 0 turns the mark off",
    "fade_hours": "silence before a bird starts draining colour; it finishes at `hours`. Inert while the two are equal",
    "rotate": "90 or 270, depending which way up the frame hangs",
    "saturation": "Inky colour saturation, 0 to 1",
    "panel": "force a driver if auto-detect ever fails",
    "mat": "extra shrink of the content inside the opening (0 = none)",
    "opening": "the opening's height as a fraction of the panel. A5 mat 0.7071, bare panel 0.97",
    "opening_aspect": "the opening's width over its height. A5 mat 0.7071, bare panel 0.75",
    "title_frac": "title height as a fraction of the PANEL, so type holds one physical size whatever is cut in front of it",
    "title_position": "which end of the opening the title sits at",
    "collage_frac": "collage width as a fraction of the OPENING's width - this is the thing meant to grow with the opening",
    "gap_frac": "space under the title, as a fraction of the panel",
    "label_scale": "bird-name type size. 0 holds the handwriting at the size the A5 mat gives it, whatever the opening",
    "quiet_start": "start of the hours the panel stays quiet (0/0 = always refresh on change)",
    "quiet_end": "end of the quiet hours",
    "heal_hours": "force a refresh at least this often, even with nothing changed",
    "state": "where the change signature is kept between runs",
    "cache": "working directory for the shot and the panel lock",
    "timeout": "seconds for a fetch or a render; a Zero 2 W needs ~70-120s to shoot the collage",
    "basic_user": "basic-auth user, only if the whole BirdNET-Pi site is behind it",
    "basic_pass": "basic-auth password",
}

# The fractions display.py already validates, with the bounds it uses, so a
# value refused here is exactly one the frame would refuse to start on.
FRACTIONS = {"opening": 1.0, "opening_aspect": 10.0, "title_frac": 1.0,
             "collage_frac": 1.0, "gap_frac": 1.0}
# Everything else numeric: key -> (low, high), both inclusive.
RANGES = {
    "saturation": (0.0, 1.0), "mat": (0.0, 0.9), "shoot_mat": (0.0, 0.9),
    "shoot_small_floor": (0.0, 0.9), "shoot_count_exp": (0.0, 4.0),
    "shoot_collage_vh": (1, 90), "quiet_start": (0, 23), "quiet_end": (0, 23),
    "hours": (1, 24 * 30), "bw_days": (1, 365), "heal_hours": (1, 24 * 30),
    "timeout": (5, 3600), "fresh_minutes": (0, 24 * 60), "fade_hours": (0, 24 * 30),
    "label_scale": (0, 10), "shoot_headline_px": (1, 400), "shoot_eyebrow_px": (1, 400),
}

# The five keys that are only correct as a set: the opening, the share of it
# the collage fills, the source pixels the birds get at that size, and the
# second day of birds the extra room can now afford (which is also what
# switches the fade on). config.example.toml spends fifteen lines saying they
# move together; here they are one row.
PRESETS = [
    ("A5 mat (an unmodified kit)", {
        "opening": 0.7071, "opening_aspect": 0.7071, "collage_frac": 0.92,
        "shoot_collage_vh": 52, "hours": 24}),
    ("bare panel (matboard taken out)", {
        "opening": 0.97, "opening_aspect": 0.75, "collage_frac": 0.98,
        "shoot_collage_vh": 74, "hours": 48}),
]


def sections(defaults=None):
    """The grouped key list actually shown, with anything unplaced collected at
    the end so a new DEFAULTS key is never unreachable."""
    defaults = DEFAULTS if defaults is None else defaults
    placed = {k for _, keys in SECTIONS for k in keys}
    out = [(title, [k for k in keys if k in defaults]) for title, keys in SECTIONS]
    extra = sorted(k for k in defaults if k not in placed)
    if extra:
        out.append(("Other", extra))
    return [(title, keys) for title, keys in out if keys]


# --- values -----------------------------------------------------------------
def kind(key):
    """How a key is edited, taken from the type of its default. A None default
    is text that can also be unset - a title, a basic-auth user."""
    if key in CHOICES:
        return "choice"
    default = DEFAULTS.get(key)
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, (int, float)):
        return "number"
    return "text"


def parse_value(key, raw):
    """One typed-in value, as the type the key's default says it is. A number
    stays an int when the default is one and the text has no fraction, so
    `hours = 24` never becomes `24.0` in the file."""
    raw = raw.strip()
    shape = kind(key)
    if shape == "choice":
        for option in CHOICES[key]:
            if str(option) == raw:
                return option
        allowed = ", ".join(repr(c) for c in CHOICES[key])
        raise ValueError(f"{key} must be one of {allowed}")
    if shape == "bool":
        if raw.lower() in ("true", "on", "yes", "1"):
            return True
        if raw.lower() in ("false", "off", "no", "0"):
            return False
        raise ValueError(f"{key} is true or false")
    if shape == "number":
        try:
            number = float(raw)
        except ValueError:
            raise ValueError(f"{key} wants a number, got {raw!r}") from None
        default = DEFAULTS.get(key)
        if isinstance(default, int) and not isinstance(default, bool) and number.is_integer():
            return int(number)
        return number
    if raw == "":
        return "" if isinstance(DEFAULTS.get(key), str) else None
    return raw


def validate(key, value):
    """Refuse here whatever display.py would refuse at render time, so a bad
    value never reaches the file and the frame never fails to start on it."""
    if key in FRACTIONS:
        display._frac(value, key, hi=FRACTIONS[key])
        return value
    if key == "title_position":
        return display._title_position(value)
    if key in CHOICES and value not in CHOICES[key]:
        allowed = ", ".join(repr(c) for c in CHOICES[key])
        raise ValueError(f"{key} must be one of {allowed}")
    if key in RANGES and isinstance(value, (int, float)) and not isinstance(value, bool):
        low, high = RANGES[key]
        if not low <= value <= high:
            raise ValueError(f"{key} must be between {low:g} and {high:g}")
    return value


def render_value(value):
    """A Python value as the TOML text for it. None means "unset", which the
    writer turns into a commented-out line rather than into a null."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


# Shown as "set" rather than printed. `birdframe list` is the kind of thing
# people paste into an issue, and a basic-auth password does not need to be in
# the scrollback for someone to change the title.
SECRETS = {"basic_pass"}


def show_value(value, key=None):
    """A value for the screen: as the file has it, minus the quoting noise."""
    if value is None or value == "":
        return "-"
    if key in SECRETS:
        return "set"
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def edit_text(value):
    """The same value as something to type over. Not show_value: that renders
    an empty setting as a dash, and handing someone a dash to edit puts a
    literal one in the file."""
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _same_number(value, target):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return float(value) == float(target)


def preset_name(values):
    """Which layout preset the current values are, or None for a hand-tuned
    set. Compared numerically, so 52 and 52.0 are the same preset."""
    for name, preset in PRESETS:
        if all(_same_number(values.get(k), v) for k, v in preset.items()):
            return name
    return None


# --- the config install.sh lays down -----------------------------------------
# What a mode IS, and nothing else. Everything a mode does not care about -
# every default, and every paragraph explaining one - comes from
# config.example.toml, which install.sh used to restate in three heredocs that
# had already drifted apart from each other and from DEFAULTS.
def init_changes(mode, zip_code="", image_url=""):
    """The handful of settings a mode actually decides. `local` decides none:
    it is what the reference file already describes."""
    if mode == "local":
        return {}
    if mode == "birdweather":
        return {"species_source": "birdweather", "zip": zip_code}
    if mode == "image":
        parts = urllib.parse.urlsplit(image_url)
        return {"shoot": False, "image_url": image_url,
                "base_url": f"{parts.scheme}://{parts.netloc}"}
    raise ValueError(f"unknown mode {mode!r}")


def init_config(path, mode, zip_code="", image_url=""):
    """Write a fresh config for `mode`, from the reference file.

    The installed config becomes config.example.toml with that mode's values
    set, so the Pi carries the whole documented reference rather than a subset
    of it, and the modes cannot drift apart - there is one file to change."""
    reference = os.path.join(FRAME, "config.example.toml")
    with open(reference, encoding="utf-8") as f:
        text = f.read()
    # install.sh reads this marker back on a re-run to refuse a mode switch.
    text = f"# birdframe-mode: {mode}\n" + text
    changes = init_changes(mode, zip_code, image_url)
    for key, value in changes.items():
        validate(key, value)
    text = apply_edits(text, changes)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".config.toml.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return text


def mode_of(values):
    """The mode marker install.sh writes at the top of the config. Kept in step
    when the source changes here, so a later install.sh re-run does not refuse
    on a mode the file has since moved away from."""
    if values.get("species_source") == "birdweather":
        return "birdweather"
    if not values.get("shoot") and (values.get("image_url") or values.get("image")):
        return "image"
    return "local"


# --- reading and writing the file -------------------------------------------
MODE_LINE = re.compile(r"^(\s*)#\s*birdframe-mode:\s*(\S+)\s*$")
APPEND_HEADER = "# --- added by `birdframe config` ---"


def _match(key, line, commented):
    pattern = (r"^(\s*)#[ \t]*(" if commented else r"^(\s*)(") + re.escape(key) + r")([ \t]*=[ \t]*)(.*)$"
    return re.match(pattern, line)


def _find_line(lines, key):
    """Where a key lives, preferring a live line over a commented-out one. The
    installed config carries the whole bare-panel block commented out, and
    setting one of those keys should uncomment that line rather than append a
    second copy further down."""
    for commented in (False, True):
        for i, line in enumerate(lines):
            if _match(key, line, commented):
                return i, commented
    return None, False


def _split_comment(rest):
    """(value, suffix) for the text after `key = `, where suffix is the spacing
    and the trailing comment. A # inside a quoted value is part of the value -
    image_url and base_url both routinely carry one."""
    quote = None
    i = 0
    while i < len(rest):
        ch = rest[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            head = rest[:i]
            value = head.rstrip()
            return value, head[len(value):] + rest[i:]
        i += 1
    return rest.rstrip(), ""


def _rebuild(indent, key, rendered, suffix, width):
    """One rewritten line, holding the trailing comment at the column it was
    already in where the new value is short enough to allow it."""
    head = f"{indent}{key} = {rendered}"
    if not suffix.strip():
        return head
    comment = suffix.lstrip()
    if len(head) < width:
        return head + " " * (width - len(head)) + comment
    return head + "  " + comment


def apply_edits(text, changes, mode=None):
    """The config text with `changes` applied in place. Comments, ordering and
    every untouched line survive; this is the whole reason the editor does not
    round-trip through a TOML writer."""
    lines = text.splitlines()
    appended = []
    for key in sorted(changes):
        rendered = render_value(changes[key])
        index, commented = _find_line(lines, key)
        if index is None:
            if rendered is not None:
                appended.append(f"{key} = {rendered}")
            continue
        found = _match(key, lines[index], commented)
        indent, _, _, rest = found.groups()
        old_value, suffix = _split_comment(rest)
        column = len(lines[index]) - len(suffix.lstrip()) if suffix.strip() else 0
        if rendered is None:
            # Unset: comment the line out, keeping the old value in view rather
            # than writing a null TOML has no word for.
            if not commented:
                lines[index] = f"{indent}# {key} = {old_value}{suffix}"
            continue
        lines[index] = _rebuild(indent, key, rendered, suffix, column)
    if mode:
        for i, line in enumerate(lines):
            found = MODE_LINE.match(line)
            if found:
                lines[i] = f"{found.group(1)}# birdframe-mode: {mode}"
                break
    if appended:
        if APPEND_HEADER not in lines:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(APPEND_HEADER)
        lines.extend(appended)
    return "\n".join(lines) + "\n"


def write_config(path, changes, mode=None):
    """Rewrite the config atomically and 0600, the way birdframe-names does:
    the frame reads this file on a timer, so it must never see half a write,
    and basic_pass can live in it."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new = apply_edits(text, changes, mode=mode)
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".config.toml.")
    try:
        # newline="\n" so the frame's config keeps unix endings even when this
        # is run from a checkout on another platform - otherwise every line
        # comes back changed and the real edit is invisible in review.
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return new


def read_config(path):
    """(values, explicit) - every key with its effective value, and the set of
    keys the file actually names. The second is what tells a value someone
    chose from a default that merely has not been overridden yet."""
    values = display.load_config(path)
    explicit = set()
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        import tomli as tomllib
    with open(path, "rb") as f:
        explicit = set(tomllib.load(f))
    return values, explicit


# --- running display.py ------------------------------------------------------
def _display_cmd(config_path, *extra):
    return [sys.executable, os.path.join(FRAME, "display.py"),
            "--config", config_path, *extra]


def _overrides(changes):
    out = []
    for key in sorted(changes):
        rendered = render_value(changes[key])
        if rendered is None:      # unset, which -o has no word for either
            rendered = '""'
        out += ["-o", f"{key}={rendered}"]
    return out


def run_preview(config_path, changes, out_path):
    """Render the staged settings to a PNG. Nothing is written to the config
    and the panel is not touched - a panel refresh is a two-minute commitment
    on a Zero 2 W, and this is the cheap way to look first."""
    cmd = _display_cmd(config_path, "--preview", out_path, *_overrides(changes))
    return subprocess.call(cmd)


def run_refresh(config_path):
    """Force one refresh. Without it the panel keeps the picture it has until a
    bird changes, which from across the room is indistinguishable from the
    settings not having taken."""
    return subprocess.call(_display_cmd(config_path, "--force"))


# --- the screen --------------------------------------------------------------
def build_rows():
    """Flat row list for the screen: section headers, the preset row at the top
    of the layout section, then one row per key."""
    rows = []
    for title, keys in sections():
        rows.append(("header", title))
        if title.startswith("Panel"):
            rows.append(("preset", "layout"))
        rows.extend(("field", key) for key in keys)
    return rows


class Editor:
    """The staged edit. Nothing here reaches the file until save."""

    def __init__(self, path):
        self.path = path
        self.original, self.explicit = read_config(path)
        self.values = dict(self.original)
        self.rows = build_rows()
        self.index = next(i for i, row in enumerate(self.rows) if row[0] != "header")
        self.top = 0
        self.message = ""

    @property
    def changes(self):
        return {k: v for k, v in self.values.items() if v != self.original.get(k)}

    def cycle(self, key, step):
        shape = kind(key)
        if shape == "bool":
            self.values[key] = not self.values.get(key)
            return True
        if shape == "choice":
            options = CHOICES[key]
            current = self.values.get(key)
            try:
                position = options.index(current)
            except ValueError:
                position = -1
            self.values[key] = options[(position + step) % len(options)]
            return True
        return False

    def cycle_preset(self, step):
        names = [name for name, _ in PRESETS]
        current = preset_name(self.values)
        position = names.index(current) if current in names else -1
        name, preset = PRESETS[(position + step) % len(PRESETS)]
        self.values.update(preset)
        self.message = f"layout: {name}"

    def set_key(self, key, raw):
        value = validate(key, parse_value(key, raw))
        self.values[key] = value

    def reset(self, key):
        self.values[key] = DEFAULTS[key]


def _draw(stdscr, editor, palette):
    import curses

    height, width = stdscr.getmaxyx()
    # Rows run from y=1 to y=height-4; the last three lines are the help line,
    # the message and the key bar.
    body = max(1, height - 4)
    last = max(1, width - 1)   # never write the bottom-right cell: curses errors
    if editor.index < editor.top:
        editor.top = editor.index
    if editor.index >= editor.top + body:
        editor.top = editor.index - body + 1
    stdscr.erase()
    changes = editor.changes
    count = len(changes)
    header = f" birdframe config  {editor.path}"
    tail = f"{count} unsaved change{'' if count == 1 else 's'} " if count else "no changes "
    stdscr.attron(palette["header"])
    stdscr.addnstr(0, 0, header.ljust(width)[:width], width)
    if width > len(tail):
        stdscr.addnstr(0, width - len(tail) - 1, tail, len(tail))
    stdscr.attroff(palette["header"])

    for offset in range(body):
        position = editor.top + offset
        if position >= len(editor.rows):
            break
        shape, key = editor.rows[position]
        y = offset + 1
        if shape == "header":
            stdscr.attron(palette["section"])
            stdscr.addnstr(y, 1, key[:width - 2], width - 2)
            stdscr.attroff(palette["section"])
            continue
        selected = position == editor.index
        if shape == "preset":
            name = preset_name(editor.values) or "custom"
            label, text = "layout preset", name
            mark = " "
        else:
            label = key
            text = show_value(editor.values.get(key), key)
            mark = "*" if key in changes else " "
        attr = palette["selected"] if selected else (
            palette["changed"] if mark == "*" else curses.A_NORMAL)
        line = f" {mark} {label:<22} {text}"
        stdscr.attron(attr)
        stdscr.addnstr(y, 0, line.ljust(width)[:width], width)
        stdscr.attroff(attr)

    shape, key = editor.rows[editor.index]
    if shape == "preset":
        note = "opening, aspect, collage, render height and window, moved as one set"
    else:
        note = HELP.get(key, "")
        if key in DEFAULTS and key not in editor.explicit and key not in editor.changes:
            note = f"(default) {note}"
    stdscr.addnstr(height - 3, 1, note[:width - 2], width - 2)
    if editor.message:
        stdscr.attron(palette["changed"])
        stdscr.addnstr(height - 2, 1, editor.message[:width - 2], width - 2)
        stdscr.attroff(palette["changed"])
    keys = " up/down move   left/right or space change   enter type   d default   p preview   s save   q quit"
    stdscr.attron(palette["header"])
    stdscr.addnstr(height - 1, 0, keys.ljust(last)[:last], last)
    stdscr.attroff(palette["header"])
    stdscr.refresh()


def _prompt(stdscr, label, initial):
    """A one-line editor on the bottom row. Written out rather than using
    curses' own getstr so escape cancels and the old value can be edited
    instead of retyped."""
    import curses

    text = str(initial)
    while True:
        height, width = stdscr.getmaxyx()
        last = max(1, width - 1)
        line = f" {label} = {text}"
        stdscr.attron(curses.A_REVERSE)
        stdscr.addnstr(height - 1, 0, line.ljust(last)[:last], last)
        stdscr.attroff(curses.A_REVERSE)
        stdscr.move(height - 1, min(len(line), last - 1))
        _cursor(1)
        stdscr.refresh()
        ch = stdscr.getch()
        _cursor(0)
        if ch in (27,):                      # escape
            return None
        if ch in (10, 13, curses.KEY_ENTER):
            return text
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            text = text[:-1]
        elif ch == 21:                       # ctrl-u
            text = ""
        elif 32 <= ch < 127:
            text += chr(ch)


def _cursor(visible):
    """Not every terminal that reaches a Pi over ssh can hide its cursor, and
    the ones that cannot raise rather than shrug."""
    import curses

    try:
        curses.curs_set(visible)
    except curses.error:
        pass


def _confirm(stdscr, question):
    import curses

    height, width = stdscr.getmaxyx()
    last = max(1, width - 1)
    stdscr.attron(curses.A_REVERSE)
    stdscr.addnstr(height - 1, 0, f" {question} (y/n)".ljust(last)[:last], last)
    stdscr.attroff(curses.A_REVERSE)
    stdscr.refresh()
    return stdscr.getch() in (ord("y"), ord("Y"))


def _palette():
    import curses

    try:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(2, curses.COLOR_CYAN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        return {"header": curses.color_pair(1), "section": curses.color_pair(2) | curses.A_BOLD,
                "changed": curses.color_pair(3), "selected": curses.A_REVERSE}
    except curses.error:
        return {"header": curses.A_REVERSE, "section": curses.A_BOLD,
                "changed": curses.A_BOLD, "selected": curses.A_REVERSE}


def _move(editor, step):
    position = editor.index
    while True:
        position += step
        if not 0 <= position < len(editor.rows):
            return
        if editor.rows[position][0] != "header":
            editor.index = position
            return


def run_screen(stdscr, editor):
    """One pass of the screen. Returns the action to take outside curses, so
    that a preview or a refresh prints where its output can be read."""
    import curses

    _cursor(0)
    palette = _palette()
    while True:
        _draw(stdscr, editor, palette)
        try:
            ch = stdscr.getch()
        except KeyboardInterrupt:
            return "quit"
        editor.message = ""
        shape, key = editor.rows[editor.index]
        if ch in (curses.KEY_DOWN, ord("j")):
            _move(editor, 1)
        elif ch in (curses.KEY_UP, ord("k")):
            _move(editor, -1)
        elif ch == curses.KEY_NPAGE:
            for _ in range(10):
                _move(editor, 1)
        elif ch == curses.KEY_PPAGE:
            for _ in range(10):
                _move(editor, -1)
        elif ch in (curses.KEY_RIGHT, curses.KEY_LEFT, ord(" "), 10, 13, curses.KEY_ENTER):
            step = -1 if ch == curses.KEY_LEFT else 1
            if shape == "preset":
                editor.cycle_preset(step)
            elif not editor.cycle(key, step):
                if ch in (curses.KEY_RIGHT, curses.KEY_LEFT):
                    continue
                raw = _prompt(stdscr, key, edit_text(editor.values.get(key)))
                if raw is None:
                    continue
                try:
                    editor.set_key(key, raw)
                except ValueError as exc:
                    editor.message = str(exc)
        elif ch == ord("d") and shape == "field":
            editor.reset(key)
        elif ch == ord("p"):
            return "preview"
        elif ch == ord("s"):
            return "save"
        elif ch in (ord("q"), 27):
            if not editor.changes or _confirm(stdscr, "discard unsaved changes?"):
                return "quit"
        elif ch == curses.KEY_RESIZE:
            continue


def tui(path):
    import curses

    editor = Editor(path)
    preview_path = os.path.join(tempfile.gettempdir(), "birdframe-preview.png")
    while True:
        action = curses.wrapper(run_screen, editor)
        if action == "preview":
            print(f"Rendering a preview with the staged settings to {preview_path} ...")
            if run_preview(path, editor.changes, preview_path) == 0:
                print(f"Wrote {preview_path}. The panel was not touched.")
            input("Press enter to go back. ")
            continue
        if action == "save":
            changes = editor.changes
            if not changes:
                print("No changes.")
                return 0
            mode = mode_of(editor.values)
            write_config(path, changes, mode=mode if mode != mode_of(editor.original) else None)
            print(f"Wrote {len(changes)} change(s) to {path}:")
            for key in sorted(changes):
                print(f"  {key} = {show_value(changes[key], key)}")
            print("Refreshing the panel (a Zero 2 W takes 1-2 min)...")
            return run_refresh(path)
        return 0


# --- command line ------------------------------------------------------------
def list_settings(path):
    values, explicit = read_config(path)
    for title, keys in sections():
        print(f"\n{title}")
        for key in keys:
            mark = " " if key in explicit else "."
            print(f" {mark} {key:<22} {show_value(values.get(key), key)}")
    name = preset_name(values)
    print(f"\nlayout preset: {name or 'custom'}    mode: {mode_of(values)}")
    print("(a . marks a key the config file does not set, so it is at its default)")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="birdframe config",
        description="Edit ~/.birdframe/config.toml and refresh the frame.")
    ap.add_argument("--config", default=os.environ.get("BIRDFRAME_CONFIG",
                                                       "~/.birdframe/config.toml"))
    ap.add_argument("--list", action="store_true", help="print every setting and exit")
    ap.add_argument("-o", "--set", dest="assignments", action="append", metavar="KEY=VALUE",
                    help="set one value without the screen, repeatable")
    ap.add_argument("--preview", metavar="PNG",
                    help="render to a PNG instead of the panel (with --set, without writing)")
    ap.add_argument("--no-refresh", action="store_true",
                    help="write the config but leave the panel alone")
    ap.add_argument("--init", metavar="MODE", choices=("local", "image", "birdweather"),
                    help="write a fresh config for MODE, for install.sh")
    ap.add_argument("--zip", dest="zip_code", default="", help="with --init birdweather")
    ap.add_argument("--image-url", default="", help="with --init image")
    ap.add_argument("--get", metavar="KEY",
                    help="print what the file sets KEY to, or nothing if it does not")
    args = ap.parse_args(argv)

    path = os.path.expanduser(args.config)

    if args.init:
        if os.path.exists(path):
            print(f"{path} already exists", file=sys.stderr)
            return 1
        try:
            init_config(path, args.init, args.zip_code, args.image_url)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        return 0

    if not os.path.exists(path):
        print(f"bird frame is not installed: {path} is missing", file=sys.stderr)
        return 1

    if args.get:
        if args.get not in DEFAULTS:
            print(f"unknown setting {args.get!r}", file=sys.stderr)
            return 2
        values, explicit = read_config(path)
        # Silence means "the file does not say", which is not the same as the
        # default - birdframe-names relies on telling those two apart.
        if args.get in explicit:
            value = values[args.get]
            print("true" if value is True else "false" if value is False else value)
        return 0

    if args.list:
        list_settings(path)
        return 0

    if args.assignments:
        values, explicit = read_config(path)
        changes = {}
        for pair in args.assignments:
            key, sep, raw = pair.partition("=")
            key = key.strip()
            if not sep or key not in DEFAULTS:
                print(f"unknown setting {key!r}" if sep else f"--set wants KEY=VALUE, got {pair!r}",
                      file=sys.stderr)
                return 2
            try:
                changes[key] = validate(key, parse_value(key, raw))
            except ValueError as exc:
                print(exc, file=sys.stderr)
                return 2
        # "Would the file change", not "would the rendered frame change". A key
        # the file does not mention is unset, not already-at-this-value: asking
        # for it explicitly has to write the line. birdframe-names depends on
        # this - a config from before names existed says nothing about them,
        # and `birdframe-names on` has to put a line in it and redraw.
        changes = {k: v for k, v in changes.items()
                   if k not in explicit or v != values.get(k)}
        if args.preview:
            return run_preview(path, changes, args.preview)
        if not changes:
            print("No changes.")
            return 0
        merged = dict(values, **changes)
        mode = mode_of(merged)
        write_config(path, changes, mode=mode if mode != mode_of(values) else None)
        for key in sorted(changes):
            print(f"{key} = {show_value(changes[key], key)}")
        return 0 if args.no_refresh else run_refresh(path)

    if args.preview:
        return run_preview(path, {}, args.preview)

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("birdframe config needs a terminal. Use --list to see the settings, "
              "or --set key=value to change one.", file=sys.stderr)
        return 1
    try:
        import curses  # noqa: F401
    except ImportError:
        print("python3 has no curses here, so the screen cannot open. "
              "Use --list and --set key=value instead.", file=sys.stderr)
        return 1
    return tui(path)


if __name__ == "__main__":
    sys.exit(main())
