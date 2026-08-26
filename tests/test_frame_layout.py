"""The frame's panel geometry and its change signature.

Neither needs a panel, a browser, or a network: opening_size and mat_and_center
are pure arithmetic over a PIL image, and the signature is pure arithmetic over
a species list. Between them they are what decides how large the type comes out
and whether the panel spends twelve seconds redrawing, which is exactly the
pair that has no other way of being checked before the frame is on a wall.
"""
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

Image = pytest.importorskip("PIL.Image", reason="Pillow is not installed")
ImageChops = pytest.importorskip("PIL.ImageChops", reason="Pillow is not installed")

FRAME = Path(__file__).resolve().parents[1] / "frame"


def load_display():
    """Import frame/display.py by path. It is a script beside its siblings, not
    an installed package, and it imports Inky only inside push_panel, so it
    loads fine on a machine with no panel attached."""
    spec = importlib.util.spec_from_file_location("birdframe_display", FRAME / "display.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


display = load_display()


# --- the opening ------------------------------------------------------------
def test_default_opening_is_the_a5_mat():
    """An unmodified kit has the A4 frame's matboard in front of the panel, so
    that is what a default install has to draw into - anything larger prints
    under the mat. The bare panel is opt-in."""
    w, h = display.opening_size(display.DEFAULTS["opening"], display.DEFAULTS["opening_aspect"])
    assert h == pytest.approx(display.PANEL_H * 0.7071)
    assert w / h == pytest.approx(1 / 1.41421, rel=1e-4)


def test_default_install_never_fades():
    """`hours` and `fade_hours` both sit at 24, so there is no tail past the
    fade point and nothing dims. Widening the window is what switches it on -
    it must not be on by default, because 48h in the A5 opening halves the
    birds."""
    assert display.DEFAULTS["hours"] == display.DEFAULTS["fade_hours"]
    assert display.fade_param(display.DEFAULTS) == "0"
    assert display.fade_steps(aged(("Melospiza melodia", 40)), ANCHOR_S,
                              display.DEFAULTS["fade_hours"], display.DEFAULTS["hours"]) == {}


def test_bare_panel_is_reachable_and_much_larger():
    """The opt-in the docs describe has to actually do what they claim."""
    a5_w, a5_h = display.opening_size(0.7071, 0.7071)
    bare_w, bare_h = display.opening_size(0.97, 0.75)
    assert bare_w / a5_w > 1.4 and bare_h / a5_h > 1.3
    assert (bare_w * bare_h) / (display.PANEL_W * display.PANEL_H) > 0.9


def test_opening_never_runs_off_the_glass():
    """A wide aspect asks for more width than the panel has; the box is pulled
    back to fit instead of letting content print past the edge."""
    w, h = display.opening_size(1.0, 2.0)
    assert w == display.PANEL_W
    assert h == pytest.approx(display.PANEL_W / 2.0)
    assert h <= display.PANEL_H


@pytest.mark.parametrize("opening,aspect", [
    (0, 0.75), (-0.1, 0.75), (1.2, 0.75), (True, 0.75), ("wide", 0.75),
    (0.9, 0), (0.9, -1), (0.9, None),
])
def test_bad_opening_values_are_refused(opening, aspect):
    with pytest.raises(ValueError):
        display.opening_size(opening, aspect)


def test_layout_of_reads_config_and_validates():
    cfg = dict(display.DEFAULTS)
    lay = display.layout_of(cfg)
    assert lay["opening"] == cfg["opening"]
    assert lay["aspect"] == cfg["opening_aspect"]
    assert (lay["title"], lay["collage"], lay["gap"]) == (
        cfg["title_frac"], cfg["collage_frac"], cfg["gap_frac"])
    for bad in ({"collage_frac": 0}, {"title_frac": 1.5}, {"gap_frac": -1},
                {"opening": 0}, {"opening_aspect": 0}, {"opening": "wide"}):
        with pytest.raises(ValueError):
            display.layout_of({**cfg, **bad})


# --- matting ----------------------------------------------------------------
PAPER = (236, 234, 223)


def fake_shot(title_h=180, gap=200, collage_h=900, collage_w=1000):
    """A stand-in for the browser's 1200x1600: a title band, a clear band wide
    enough to trip the splitter's 60px rule, then a collage block."""
    img = Image.new("RGB", (display.PANEL_W, display.PANEL_H), PAPER)
    ink = Image.new("RGB", (600, title_h), (20, 20, 20))
    img.paste(ink, ((display.PANEL_W - 600) // 2, 100))
    block = Image.new("RGB", (collage_w, collage_h), (40, 40, 40))
    img.paste(block, ((display.PANEL_W - collage_w) // 2, 100 + title_h + gap))
    return img


def ink_bbox(img):
    diff = ImageChops.difference(img, Image.new("RGB", img.size, display._paper(img)))
    return diff.convert("L").point(lambda p: 255 if p > 34 else 0).getbbox()


def test_full_panel_layout_draws_larger_than_the_a5_mat():
    """The same shot, matted both ways. Full-panel has to come out
    substantially bigger in both directions - if it does not, enlarging the
    opening did nothing and the text on the wall is the size it always was."""
    shot = fake_shot()
    big = display.mat_and_center(shot, 0.0, 0.97, 0.75, 0.10, 0.98, 0.05)
    a5 = display.mat_and_center(shot, 0.0, 0.7071, 0.7071, 0.065, 0.66, 0.1)
    bw = ink_bbox(big)
    aw = ink_bbox(a5)
    assert (bw[2] - bw[0]) > 1.8 * (aw[2] - aw[0])
    assert (bw[3] - bw[1]) > 1.5 * (aw[3] - aw[1])


def test_matted_content_stays_inside_the_panel():
    out = display.mat_and_center(fake_shot(), 0.0, 0.97, 0.75, 0.10, 0.98, 0.05)
    assert out.size == (display.PANEL_W, display.PANEL_H)
    box = ink_bbox(out)
    assert box[0] >= 0 and box[1] >= 0
    assert box[2] <= display.PANEL_W and box[3] <= display.PANEL_H


def test_unsplittable_shot_still_fits_the_opening():
    """No clear band means no title/collage split, so the whole crop is placed
    as one block. It still has to land inside the opening."""
    solid = Image.new("RGB", (display.PANEL_W, display.PANEL_H), PAPER)
    solid.paste(Image.new("RGB", (900, 1200), (30, 30, 30)), (150, 200))
    out = display.mat_and_center(solid, 0.0, 0.97, 0.75)
    box = ink_bbox(out)
    ow, oh = display.opening_size(0.97, 0.75)
    assert (box[2] - box[0]) <= ow + 1
    assert (box[3] - box[1]) <= oh + 1


# --- freshness --------------------------------------------------------------
def stamp(anchor, minutes_ago):
    return (anchor - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")


ANCHOR = datetime(2026, 8, 25, 6, 42, 0)
ANCHOR_S = ANCHOR.strftime("%Y-%m-%d %H:%M:%S")


def species(*rows):
    return [{"sci": sci, "com": sci, "n": n, "last_seen": seen} for sci, n, seen in rows]


def test_fresh_slugs_uses_the_station_anchor_not_this_clock():
    """The anchor is hours away from any plausible test-runner clock, so a
    result that matches it proves the comparison is not using datetime.now()."""
    sp = species(
        ("Melospiza melodia", 40, stamp(ANCHOR, 5)),
        ("Corvus corax", 3, stamp(ANCHOR, 90)),
    )
    assert display.fresh_slugs(sp, ANCHOR_S, 30) == frozenset({"melospiza-melodia"})


def test_fresh_window_boundary_is_inclusive():
    sp = species(("Melospiza melodia", 1, stamp(ANCHOR, 30)))
    assert display.fresh_slugs(sp, ANCHOR_S, 30) == frozenset({"melospiza-melodia"})
    assert display.fresh_slugs(sp, ANCHOR_S, 29) == frozenset()


def test_fresh_window_off_marks_nothing():
    sp = species(("Melospiza melodia", 1, stamp(ANCHOR, 1)))
    assert display.fresh_slugs(sp, ANCHOR_S, 0) == frozenset()


def test_missing_last_seen_is_never_fresh():
    """BirdWeather mode: a species list with no timestamps at all."""
    sp = [{"sci": "Corvus corax", "com": "Common Raven", "n": 9}]
    assert display.fresh_slugs(sp, None, 30) == frozenset()


def test_signature_changes_when_a_bird_stops_singing():
    """The panel has no partial refresh, so a bird crossing the fresh window is
    the only thing about the passage of time that may move a pixel - and it has
    to move the signature, or the outline would never be taken off."""
    sp = species(
        ("Melospiza melodia", 40, stamp(ANCHOR, 5)),
        ("Corvus corax", 3, stamp(ANCHOR, 90)),
    )
    singing = display.signature(sp, display.fresh_slugs(sp, ANCHOR_S, 30))
    later = ANCHOR + timedelta(hours=1)
    quiet = display.signature(sp, display.fresh_slugs(
        sp, later.strftime("%Y-%m-%d %H:%M:%S"), 30))
    assert singing != quiet


def test_signature_ignores_a_timestamp_that_changes_nothing():
    """A detection three minutes newer, still inside the window, is not worth
    twelve seconds of panel. Only the fresh SET is in the signature, never the
    clock itself."""
    a = species(("Melospiza melodia", 40, stamp(ANCHOR, 5)))
    b = species(("Melospiza melodia", 40, stamp(ANCHOR, 2)))
    assert (display.signature(a, display.fresh_slugs(a, ANCHOR_S, 30))
            == display.signature(b, display.fresh_slugs(b, ANCHOR_S, 30)))


def test_signature_still_tracks_species_and_counts():
    sp = species(("Melospiza melodia", 40, stamp(ANCHOR, 5)))
    more = species(("Melospiza melodia", 400, stamp(ANCHOR, 5)))
    plus = sp + species(("Corvus corax", 1, stamp(ANCHOR, 200)))
    base = display.signature(sp)
    assert base != display.signature(more)
    assert base != display.signature(plus)


@pytest.mark.parametrize("text,expected", [
    ("2026-08-25 06:42:00", datetime(2026, 8, 25, 6, 42)),
    ("2026-08-25T06:42:00", datetime(2026, 8, 25, 6, 42)),
    ("2026-08-25 06:42", datetime(2026, 8, 25, 6, 42)),
    ("", None), (None, None), ("not a date", None),
])
def test_parse_station_ts(text, expected):
    assert display.parse_station_ts(text) == expected


# --- the URL the renderer is pointed at -------------------------------------
def test_frame_url_carries_names_and_the_fresh_window():
    url = display.frame_url("https://example.test/frame.png?k=abc", True, 30)
    assert "k=abc" in url and "labels=1" in url and "fresh=30" in url


def test_frame_url_replaces_rather_than_appends():
    url = display.frame_url("https://example.test/?labels=1&fresh=5", False, 30)
    assert url.count("labels=") == 1 and url.count("fresh=") == 1
    assert "labels=0" in url and "fresh=30" in url


def test_frame_url_leaves_fresh_alone_when_unset():
    url = display.frame_url("https://example.test/?fresh=5", True)
    assert "fresh=5" in url and "labels=1" in url


# --- going quiet ------------------------------------------------------------
def aged(*rows):
    """species heard `hours_ago` before the anchor."""
    return [{"sci": sci, "com": sci, "n": 5,
             "last_seen": (ANCHOR - timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")}
            for sci, h in rows]


def test_nothing_fades_inside_the_full_strength_window():
    sp = aged(("Melospiza melodia", 1), ("Corvus corax", 23.9))
    assert display.fade_steps(sp, ANCHOR_S, 24, 48) == {}


def test_fade_ramps_in_whole_steps_to_the_window_edge():
    sp = aged(("Melospiza melodia", 25), ("Corvus corax", 30),
              ("Junco hyemalis", 36), ("Calypte anna", 42), ("Sitta carolinensis", 48))
    steps = display.fade_steps(sp, ANCHOR_S, 24, 48)
    assert steps == {"melospiza-melodia": 1, "corvus-corax": 2, "junco-hyemalis": 3,
                     "calypte-anna": 4, "sitta-carolinensis": 5}


def test_fade_never_exceeds_the_last_step():
    """A bird past the window edge is on its way out of the payload entirely,
    but until it goes it must not ask for a step the stylesheet has no rule for."""
    sp = aged(("Melospiza melodia", 500))
    assert display.fade_steps(sp, ANCHOR_S, 24, 48) == {"melospiza-melodia": display.FADE_STEPS}


@pytest.mark.parametrize("fade_hours,window", [(0, 48), (None, 48), (24, 24), (48, 24), (24, 0)])
def test_fade_off_when_there_is_no_tail_to_fade_over(fade_hours, window):
    assert display.fade_steps(aged(("Melospiza melodia", 40)), ANCHOR_S, fade_hours, window) == {}


def test_species_without_a_timestamp_never_fades():
    sp = [{"sci": "Corvus corax", "com": "Common Raven", "n": 9}]
    assert display.fade_steps(sp, None, 24, 48) == {}


def test_signature_changes_on_a_fade_step_but_not_within_one():
    sp = aged(("Melospiza melodia", 30))
    same = aged(("Melospiza melodia", 31))       # still step 2
    later = aged(("Melospiza melodia", 36))      # now step 3

    def sig(rows):
        return display.signature(rows, frozenset(), display.fade_steps(rows, ANCHOR_S, 24, 48))
    assert sig(sp) == sig(same)
    assert sig(sp) != sig(later)


@pytest.mark.parametrize("cfg,expected", [
    ({"fade_hours": 24, "hours": 48}, "24-48"),
    ({"fade_hours": 12, "hours": 72}, "12-72"),
    ({"fade_hours": 0, "hours": 48}, "0"),
    ({"fade_hours": 48, "hours": 48}, "0"),
    ({"fade_hours": 48, "hours": 24}, "0"),
    ({}, "0"),
])
def test_fade_param(cfg, expected):
    assert display.fade_param(cfg) == expected


def test_frame_url_carries_the_fade_window():
    url = display.frame_url("https://example.test/f.png?fade=1-2", True, 30, "24-48")
    assert url.count("fade=") == 1 and "fade=24-48" in url


# --- the two halves have to agree -------------------------------------------
# display.py decides whether the panel is redrawn; apt.js decides what is drawn.
# Where they both compute the same quantity they must use the same numbers, or
# the frame refreshes for a change the renderer does not make (or, worse, fails
# to refresh for one it does). These read the constants straight out of apt.js.
APT_JS = (FRAME.parent / "avian" / "frontend" / "apt.js").read_text(encoding="utf-8")


def js_const(name):
    import re
    m = re.search(r"var " + name + r" = ([^;]+);", APT_JS)
    assert m, f"{name} is gone from apt.js"
    return m.group(1).strip()


def test_fade_steps_match_the_renderer():
    assert js_const("FADE_STEPS") == str(display.FADE_STEPS)


def test_every_fade_step_has_a_stylesheet_rule():
    css = (FRAME.parent / "avian" / "frontend" / "styles.css").read_text(encoding="utf-8")
    for step in range(1, display.FADE_STEPS + 1):
        assert f'.gtile[data-fade="{step}"]' in css, f"no rule for fade step {step}"


def test_tile_size_brackets_match_the_signature_brackets():
    """The renderer sizes tiles off these and the signature buckets counts off
    them. If they diverge, a count change can resize a bird without earning a
    refresh - or earn one without changing anything."""
    import ast
    js = ast.literal_eval(js_const("COUNT_BRACKETS"))
    # _bucket's own edges, recovered by probing it either side of each boundary.
    edges = []
    for n in range(1, 1200):
        if display._bucket(n) != display._bucket(n + 1):
            edges.append(n)
    assert js == edges, f"apt.js {js} vs display.py {edges}"


def test_the_bare_panel_opt_in_switches_the_fade_on():
    """Widening `hours` is the single thing that turns fading on: fade_hours is
    already sitting at 24 waiting for a tail to run down."""
    widened = {**display.DEFAULTS, "hours": 48}
    assert display.fade_param(widened) == "24-48"
    assert display.fade_steps(aged(("Melospiza melodia", 40)), ANCHOR_S, 24, 48) != {}


def test_the_collage_draws_nothing_from_an_unseeded_clock_or_coin():
    """The frame launches a fresh browser for every render, so anything the
    collage decides by Math.random() is re-decided each time - and a re-decided
    pose is a different silhouette, which repacks the whole plate and makes the
    birds jump. maskPack's own PRNG is seeded and stays."""
    import re
    code = re.sub(r"//[^\n]*", "", APT_JS)          # strip line comments
    assert "Math.random(" not in code
    assert "var seed = 0x9E3779B9;" in APT_JS       # the seeded one is still there


def test_a_count_change_inside_one_bracket_moves_nothing():
    """The renderer sizes tiles off the same brackets, so a count change the
    signature ignores is also a change that cannot resize a bird - and therefore
    cannot repack the plate. This is the invariant that stops the birds jumping."""
    a = species(("Melospiza melodia", 41, stamp(ANCHOR, 200)))
    b = species(("Melospiza melodia", 99, stamp(ANCHOR, 200)))
    assert display.signature(a) == display.signature(b)
    crossed = species(("Melospiza melodia", 101, stamp(ANCHOR, 200)))
    assert display.signature(a) != display.signature(crossed)


# --- per-run overrides ------------------------------------------------------
@pytest.mark.parametrize("pair,key,expected", [
    ("fresh_minutes=0", "fresh_minutes", 0),
    ("opening=0.7071", "opening", 0.7071),
    ("bird_names=false", "bird_names", False),
    ("bird_names=true", "bird_names", True),
    ('panel="el133uf1"', "panel", "el133uf1"),
    ("panel=el133uf1", "panel", "el133uf1"),      # bare string, no TOML quotes
    ("hours = 24", "hours", 24),                   # spaces around the =
])
def test_override_parses_the_value_as_written(pair, key, expected):
    cfg = display.apply_overrides(dict(display.DEFAULTS), [pair])
    assert cfg[key] == expected
    assert type(cfg[key]) is type(expected)


def test_overrides_are_repeatable_and_leave_the_rest_alone():
    cfg = display.apply_overrides(dict(display.DEFAULTS),
                                  ["fresh_minutes=0", "fade_hours=1", "hours=2"])
    assert (cfg["fresh_minutes"], cfg["fade_hours"], cfg["hours"]) == (0, 1, 2)
    assert cfg["opening"] == display.DEFAULTS["opening"]


@pytest.mark.parametrize("bad", ["nonsense", "", "=5", "  =5"])
def test_malformed_override_is_refused(bad):
    with pytest.raises(ValueError):
        display.apply_overrides(dict(display.DEFAULTS), [bad])


def test_unknown_key_is_refused_rather_than_ignored():
    """A typo that silently changes nothing is the worst outcome when the whole
    point of the flag is checking whether a setting did anything."""
    with pytest.raises(ValueError):
        display.apply_overrides(dict(display.DEFAULTS), ["fresh_minute=0"])


# --- text is a physical size, not a share of the opening --------------------
def title_height(opening, aspect, title_frac):
    """What mat_and_center will scale the title band to."""
    _, oh = display.opening_size(opening, aspect)
    return min(display.PANEL_H * title_frac, oh * 0.30)


def test_one_title_fraction_holds_the_title_still_across_openings():
    """A title is legible from across the room or it is not, and that has
    nothing to do with how much glass the mat leaves showing. The same number
    has to give the same physical title on both shipped openings."""
    tf = display.DEFAULTS["title_frac"]
    a5 = title_height(0.7071, 0.7071, tf)
    bare = title_height(0.97, 0.75, tf)
    assert a5 == pytest.approx(bare, rel=0.01)
    # and it is still the height the A5 mat has always produced
    assert a5 == pytest.approx(display.PANEL_H * 0.7071 * 0.065, rel=0.02)


def test_a_tiny_opening_cannot_be_swallowed_by_its_own_title():
    _, oh = display.opening_size(0.2, 0.7071)
    assert title_height(0.2, 0.7071, display.DEFAULTS["title_frac"]) <= oh * 0.30


def test_the_larger_opening_gives_its_extra_room_to_the_birds():
    """The point of the bare panel: title and gap are unchanged in absolute px,
    so everything the opening gained lands in the collage."""
    def collage_area(opening, aspect, collage_frac):
        ow, oh = display.opening_size(opening, aspect)
        tf, gf = display.DEFAULTS["title_frac"], display.DEFAULTS["gap_frac"]
        return ow * collage_frac * (oh - min(display.PANEL_H * tf, oh * 0.30)
                                    - min(display.PANEL_H * gf, oh * 0.20))
    assert collage_area(0.97, 0.75, 0.98) / collage_area(0.7071, 0.7071, 0.66) > 3.0


# --- the names hold their size too ------------------------------------------
def test_label_scale_is_one_for_the_shipped_default():
    """The A5 mat is the reference, so nothing is rescaled there."""
    assert display.label_scale(dict(display.DEFAULTS)) == pytest.approx(1.0, rel=0.02)


def test_label_scale_cancels_the_bare_panels_scale_up():
    bare = {**display.DEFAULTS, "opening": 0.97, "opening_aspect": 0.75, "collage_frac": 0.98}
    scale = display.label_scale(bare)
    # the collage is scaled up by 1/scale on its way to the panel, so the type
    # has to come down by exactly that much to end up the same size
    assert scale == pytest.approx(display.LABEL_REFERENCE_SCALE / display.collage_scale(bare))
    assert 0.4 < scale < 0.55
    # net effect: a name is the same physical size on both openings
    a5 = display.label_scale(dict(display.DEFAULTS)) * display.collage_scale(dict(display.DEFAULTS))
    assert scale * display.collage_scale(bare) == pytest.approx(a5, rel=0.02)


def test_label_scale_never_enlarges():
    """A smaller-than-A5 opening would want type bigger than the tile can carry;
    apt.js's own cap is the better judge of that than a ratio is."""
    small = {**display.DEFAULTS, "opening": 0.4}
    assert display.label_scale(small) == 1.0


def test_label_scale_can_be_overridden():
    assert display.label_scale({**display.DEFAULTS, "label_scale": 1.5}) == 1.5


# --- shoot's apt.js rewrites must still find their targets ------------------
def test_every_apt_js_rewrite_still_matches():
    """shoot.py refuses to ship a half-tuned frame: if any of these patterns
    stops matching it raises and the panel keeps yesterday's picture. They are
    regexes against someone else's source file, so they need a guard."""
    import ast
    import re as _re
    src = (FRAME / "shoot.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_make_js_handler")
    patterns = [pair.elts[0].value for node in ast.walk(fn)
                if isinstance(node, ast.Tuple)
                for pair in node.elts
                if isinstance(pair, ast.Tuple) and len(pair.elts) == 2
                and isinstance(pair.elts[0], ast.Constant) and isinstance(pair.elts[0].value, str)]
    assert len(patterns) >= 6, f"only found {len(patterns)} rewrite patterns"
    for pat in patterns:
        assert _re.search(pat, APT_JS), f"apt.js no longer matches {pat!r}"
