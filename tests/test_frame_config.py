"""The frame's settings editor, minus the screen.

The screen itself is curses and needs a terminal, but nothing interesting
happens there - the parts worth pinning down are the ones that touch the
config file, because that file is hand-written prose as much as it is TOML.
An editor that round-tripped it through a parser would return it stripped of
every comment explaining what the numbers mean, and nobody would notice until
they next went looking for the explanation.

So: values keep their types, an existing line keeps its place and its trailing
comment, a commented-out line is uncommented where it stands rather than
duplicated at the bottom, and every key display.py knows about is reachable.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("PIL.Image", reason="Pillow is not installed")

FRAME = Path(__file__).resolve().parents[1] / "frame"


def load(name, filename):
    """Import a script from frame/ by path, the way test_frame_layout does."""
    spec = importlib.util.spec_from_file_location(name, FRAME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tui = load("birdframe_config_tui", "config_tui.py")
display = tui.display


# A config in the shape install.sh actually writes: annotated, with the
# bare-panel block sitting there commented out waiting to be switched on.
INSTALLED = '''# birdframe-mode: local
# AvianVisitors frame, local mode: mirrors the BirdNET-Pi on your network.
base_url = "http://birdnet.local"
shoot = true
shoot_title = "Avian Visitors"
bird_names = true
fresh_minutes = 30   # outline a bird heard this recently; 0 turns the mark off
# Taken the matboard out? These five hand the whole extra opening to the birds:
# opening = 0.97
# opening_aspect = 0.75
# collage_frac = 0.98
# shoot_collage_vh = 74
# hours = 48
rotate = 90          # flip to 270 if the frame hangs the other way up
saturation = 0.6
'''


def written(tmp_path, changes, text=INSTALLED, mode=None):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    tui.write_config(str(path), changes, mode=mode)
    return path.read_text(encoding="utf-8")


def parsed(tmp_path, changes, text=INSTALLED):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    tui.write_config(str(path), changes)
    return tui.display.load_config(str(path))


# --- every setting is reachable ---------------------------------------------
def test_every_default_appears_on_the_screen():
    """The grouping is hand-written and DEFAULTS is not. A key added to
    display.py and forgotten here would be invisible in the editor, which is
    the one failure mode of driving a screen off a list someone maintains."""
    shown = {key for _, keys in tui.sections() for key in keys}
    assert shown == set(display.DEFAULTS)


def test_an_unplaced_key_still_shows_up():
    """Even before anyone puts it in a section."""
    defaults = dict(display.DEFAULTS, brand_new_setting=1)
    shown = {key for _, keys in tui.sections(defaults) for key in keys}
    assert "brand_new_setting" in shown
    assert any(title == "Other" for title, _ in tui.sections(defaults))


def test_every_setting_has_a_line_of_help():
    """The screen is the only documentation someone reads at 11pm on a Pi."""
    for _, keys in tui.sections():
        for key in keys:
            assert tui.HELP.get(key), f"{key} has no help text"


# --- the file survives being edited -----------------------------------------
def test_a_commented_out_key_is_uncommented_where_it_stands(tmp_path):
    """The bare-panel block ships commented out inside the paragraph that
    explains it. Appending a second `opening` at the bottom would work and
    would also strand the explanation above a line that no longer does
    anything."""
    out = written(tmp_path, {"opening": 0.97})
    assert "\nopening = 0.97\n" in out
    assert out.count("opening = 0.97") == 1
    assert "# Taken the matboard out?" in out
    assert out.index("Taken the matboard out") < out.index("\nopening = 0.97")


def test_a_trailing_comment_survives_an_edit(tmp_path):
    out = written(tmp_path, {"rotate": 270})
    assert "rotate = 270         # flip to 270 if the frame hangs the other way up" in out


def test_untouched_lines_are_byte_identical(tmp_path):
    """The whole promise of editing by line rather than re-serialising."""
    out = written(tmp_path, {"rotate": 270})
    before, after = INSTALLED.splitlines(), out.splitlines()
    assert len(before) == len(after)
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(changed) == 1
    assert after[changed[0]].startswith("rotate = 270")


def test_a_hash_inside_a_quoted_value_is_not_a_comment(tmp_path):
    """image_url routinely carries a query string, and a fragment in one would
    otherwise be read as the start of a comment and thrown away."""
    text = 'image_url = "https://bird.example/f.png?k=1#frag"   # the worker\n'
    out = written(tmp_path, {"rotate": 270}, text=text + "rotate = 90\n")
    assert '"https://bird.example/f.png?k=1#frag"   # the worker' in out


def test_a_new_key_is_appended_once_and_then_edited_in_place(tmp_path):
    """Appending twice would leave two lines for one setting, and TOML takes
    the last - so the editor would appear to work while the file grew a
    contradiction every time."""
    path = tmp_path / "config.toml"
    path.write_text(INSTALLED, encoding="utf-8")
    tui.write_config(str(path), {"heal_hours": 12})
    tui.write_config(str(path), {"heal_hours": 6})
    out = path.read_text(encoding="utf-8")
    assert out.count("heal_hours") == 1
    assert "heal_hours = 6" in out
    assert display.load_config(str(path))["heal_hours"] == 6


def test_clearing_a_value_comments_the_line_out(tmp_path):
    """TOML has no null. Commenting the line keeps the old value in view for
    whoever wants it back, which a deleted line would not."""
    out = written(tmp_path, {"shoot_title": None})
    assert '# shoot_title = "Avian Visitors"' in out
    assert display.load_config(str(tmp_path / "config.toml")).get("shoot_title") is None


def test_the_result_is_still_valid_toml(tmp_path):
    cfg = parsed(tmp_path, {"opening": 0.97, "rotate": 270, "shoot_title": "Birds"})
    assert cfg["opening"] == 0.97
    assert cfg["rotate"] == 270
    assert cfg["shoot_title"] == "Birds"


def test_a_quote_in_a_title_does_not_break_the_file(tmp_path):
    cfg = parsed(tmp_path, {"shoot_title": 'The "Loud" Ones'})
    assert cfg["shoot_title"] == 'The "Loud" Ones'


def test_the_file_keeps_unix_line_endings(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(INSTALLED, encoding="utf-8")
    tui.write_config(str(path), {"rotate": 270})
    assert b"\r\n" not in path.read_bytes()


def test_the_mode_marker_follows_the_source(tmp_path):
    """install.sh refuses to re-run against a config whose mode marker
    disagrees with it. Switching source here and leaving the marker behind
    would turn the next upgrade into an error nobody could explain."""
    out = written(tmp_path, {"species_source": "birdweather", "zip": "94107"},
                  mode="birdweather")
    assert "# birdframe-mode: birdweather" in out
    assert "# birdframe-mode: local" not in out


def test_the_mode_marker_is_left_alone_otherwise(tmp_path):
    out = written(tmp_path, {"rotate": 270})
    assert "# birdframe-mode: local" in out


# --- values keep their types ------------------------------------------------
@pytest.mark.parametrize("key, raw, expected", [
    ("hours", "48", 48),                 # int default, int in the file
    ("opening", "0.97", 0.97),
    ("label_scale", "1.5", 1.5),         # int default, but a real multiplier
    ("bird_names", "off", False),
    ("rotate", "270", 270),              # a choice that is a number
    ("shoot_title", "Avian Visitors", "Avian Visitors"),
])
def test_typed_in_values_come_back_as_the_right_type(key, raw, expected):
    assert tui.parse_value(key, raw) == expected


def test_a_whole_number_is_not_written_as_a_float():
    """`hours = 24.0` parses, and then reads as a mistake to the next person."""
    assert tui.render_value(tui.parse_value("hours", "24")) == "24"


def test_clearing_a_text_setting_means_unset_not_empty():
    assert tui.parse_value("shoot_title", "") is None      # default is None
    assert tui.parse_value("zip", "") == ""                # default is ""


# --- bad values are refused before they reach the file ----------------------
@pytest.mark.parametrize("key, value", [
    ("opening", 0), ("opening", 1.5), ("collage_frac", 0),
    ("saturation", 1.4), ("quiet_start", 24), ("rotate", 180),
    ("title_position", "middle"), ("timeout", 1),
])
def test_a_value_the_frame_would_refuse_is_refused_here(key, value):
    with pytest.raises(ValueError):
        tui.validate(key, value)


def test_the_bounds_are_the_ones_display_py_enforces():
    """Not a second opinion about what a good opening is - the same call the
    renderer makes, so the two can never drift apart."""
    for key, high in tui.FRACTIONS.items():
        with pytest.raises(ValueError):
            tui.validate(key, high + 0.01)
        assert tui.validate(key, high) == high


# --- the layout preset ------------------------------------------------------
def test_the_preset_names_the_shipped_default():
    assert tui.preset_name(display.DEFAULTS) == tui.PRESETS[0][0]


def test_the_bare_panel_preset_round_trips():
    values = dict(display.DEFAULTS)
    values.update(dict(tui.PRESETS[1][1]))
    assert tui.preset_name(values) == tui.PRESETS[1][0]


def test_the_bare_panel_preset_actually_opens_the_frame_up():
    """The five keys are a set because the point of them is one outcome. If a
    preset ever stopped enlarging the collage it would still look plausible on
    the screen and do nothing on the wall."""
    bare = dict(display.DEFAULTS, **tui.PRESETS[1][1])
    matted_w, matted_h = display.opening_size(display.DEFAULTS["opening"],
                                              display.DEFAULTS["opening_aspect"])
    bare_w, bare_h = display.opening_size(bare["opening"], bare["opening_aspect"])
    # Roughly the 47% -> 94% of the glass the README quotes.
    assert (bare_w * bare_h) / (matted_w * matted_h) > 1.9
    # And the birds, not just the window, are what gets the room.
    assert display.collage_scale(bare) > display.collage_scale(display.DEFAULTS) * 1.5


def test_a_hand_tuned_layout_is_not_claimed_as_a_preset():
    values = dict(display.DEFAULTS, opening=0.85)
    assert tui.preset_name(values) is None


# --- the screen, driven through a stub terminal ------------------------------
# curses needs a real terminal and the frame's is on the other side of an ssh
# session, so the screen is driven here against a fake one. It is not about
# what the pixels look like - it is that navigating, toggling and the preset
# row still do what the file-level tests assume they do.
class FakeScreen:
    def __init__(self, keys):
        self.keys = list(keys)
        self.lines = []

    def getmaxyx(self):
        return 24, 100

    def erase(self):
        self.lines = []

    def addnstr(self, y, x, text, n):
        assert len(text) <= n, "addnstr was handed more than it was told to write"
        assert 0 <= y < 24 and 0 <= x < 100
        assert not (y == 23 and x + len(text) >= 100), "wrote the bottom-right cell"
        self.lines.append((y, text))

    def attron(self, attr):
        pass

    attroff = attron

    def move(self, y, x):
        assert 0 <= y < 24 and 0 <= x < 100

    def refresh(self):
        pass

    def getch(self):
        if not self.keys:
            raise AssertionError("the screen asked for a key it was not given")
        return self.keys.pop(0)


def fake_curses(keys):
    import types

    module = types.ModuleType("curses")
    module.error = type("error", (Exception,), {})
    names = ["KEY_DOWN", "KEY_UP", "KEY_LEFT", "KEY_RIGHT", "KEY_NPAGE", "KEY_PPAGE",
             "KEY_ENTER", "KEY_RESIZE", "KEY_BACKSPACE"]
    for number, name in enumerate(names, start=1000):
        setattr(module, name, number)
    module.A_NORMAL, module.A_BOLD, module.A_REVERSE, module.A_DIM = 0, 1, 2, 4
    module.COLOR_BLACK, module.COLOR_CYAN, module.COLOR_YELLOW = 0, 6, 3
    module.curs_set = lambda visible: None
    module.start_color = lambda: None
    module.use_default_colors = lambda: None
    module.init_pair = lambda *a: None
    module.color_pair = lambda n: n
    module._screen = FakeScreen(keys)
    return module


def drive(monkeypatch, editor, keys, at=None):
    module = fake_curses(keys)
    monkeypatch.setitem(sys.modules, "curses", module)
    if at is not None:
        editor.index = at
    return tui.run_screen(module._screen, editor), module._screen


def row_of(editor, key):
    return next(i for i, (shape, name) in enumerate(editor.rows) if name == key)


@pytest.fixture
def editor(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(INSTALLED, encoding="utf-8")
    return tui.Editor(str(path))


def test_the_screen_opens_on_a_setting_not_a_heading(editor):
    assert editor.rows[editor.index][0] != "header"


def test_space_toggles_a_switch(monkeypatch, editor):
    before = editor.values["bird_names"]
    # The trailing y is the confirm: an edit was made, so quitting asks.
    action, _ = drive(monkeypatch, editor, [ord(" "), ord("q"), ord("y")],
                      at=row_of(editor, "bird_names"))
    assert action == "quit"
    assert editor.values["bird_names"] is not before
    assert "bird_names" in editor.changes


def test_arrows_walk_a_choice_round(monkeypatch, editor):
    keys = fake_curses([])
    drive(monkeypatch, editor, [keys.KEY_RIGHT, ord("q"), ord("y")], at=row_of(editor, "rotate"))
    assert editor.values["rotate"] == 270


def test_the_preset_row_moves_all_five_keys(monkeypatch, editor):
    """The whole reason the row exists: one keypress, five settings, and a
    layout that is coherent at the end of it."""
    keys = fake_curses([])
    drive(monkeypatch, editor, [keys.KEY_RIGHT, ord("q"), ord("y")], at=row_of(editor, "layout"))
    assert tui.preset_name(editor.values) == tui.PRESETS[1][0]
    assert set(editor.changes) == {"opening", "opening_aspect", "collage_frac",
                                   "shoot_collage_vh", "hours"}


def test_d_puts_a_setting_back_to_its_default(monkeypatch, editor):
    keys = fake_curses([])
    drive(monkeypatch, editor, [keys.KEY_RIGHT, ord("d"), ord("q")],
          at=row_of(editor, "rotate"))   # no confirm: 'd' put it back, so nothing changed
    assert editor.values["rotate"] == display.DEFAULTS["rotate"]
    assert "rotate" not in editor.changes


def test_headings_are_stepped_over(monkeypatch, editor):
    keys = fake_curses([])
    start = editor.index
    drive(monkeypatch, editor, [keys.KEY_DOWN] * 12 + [ord("q")])
    assert editor.index > start
    assert editor.rows[editor.index][0] != "header"


def test_navigation_stops_at_the_ends(monkeypatch, editor):
    keys = fake_curses([])
    drive(monkeypatch, editor, [keys.KEY_UP] * 5 + [keys.KEY_PPAGE, ord("q")])
    assert editor.rows[editor.index][0] != "header"
    drive(monkeypatch, editor, [keys.KEY_NPAGE] * 20 + [ord("q")])
    assert editor.rows[editor.index][0] != "header"


def test_quitting_with_changes_asks_first(monkeypatch, editor):
    """A discarded edit is cheap to redo and expensive to notice, so the only
    way out with unsaved changes is through the question."""
    action, _ = drive(monkeypatch, editor, [ord(" "), ord("q"), ord("n"), ord("q"), ord("y")],
                      at=row_of(editor, "bird_names"))
    assert action == "quit"


def test_quitting_clean_does_not_ask(monkeypatch, editor):
    action, _ = drive(monkeypatch, editor, [ord("q")])
    assert action == "quit"


def test_s_and_p_hand_back_to_the_caller(monkeypatch, editor):
    """Both leave curses before they run: a preview prints a path and a save
    prints what it wrote and then spends two minutes on the panel, and neither
    is readable underneath a full-screen editor."""
    assert drive(monkeypatch, editor, [ord("s")])[0] == "save"
    assert drive(monkeypatch, editor, [ord("p")])[0] == "preview"


def test_a_narrow_terminal_does_not_run_off_the_edge(monkeypatch, editor):
    """A frame is usually configured from a phone over ssh."""
    module = fake_curses([ord("q")])
    module._screen.getmaxyx = lambda: (8, 30)
    monkeypatch.setitem(sys.modules, "curses", module)
    assert tui.run_screen(module._screen, editor) == "quit"


# --- against the file people actually have -----------------------------------
def test_keys_that_are_prefixes_of_other_keys_do_not_collide(tmp_path):
    """`mat` and `shoot_mat`, `hours` and `fade_hours`, `opening` and
    `opening_aspect`. A loose match here would edit the wrong line and the
    wrong setting, and both files would still parse - so nothing would say so.
    Run against the shipped reference, which has every one of these pairs."""
    reference = (FRAME / "config.example.toml").read_text(encoding="utf-8")
    path = tmp_path / "config.toml"
    path.write_text(reference, encoding="utf-8")
    edits = {"mat": 0.05, "hours": 48, "opening": 0.97, "label_scale": 1.5,
             "panel": "el133uf1", "shoot_mat": 0.06}
    tui.write_config(str(path), edits)
    out = path.read_text(encoding="utf-8")

    before, after = reference.splitlines(), out.splitlines()
    assert len(before) == len(after), "the reference config gained or lost a line"
    changed = {after[i].split("=")[0].strip()
               for i, (a, b) in enumerate(zip(before, after)) if a != b}
    assert changed == set(edits), f"edited the wrong lines: {changed}"

    cfg = display.load_config(str(path))
    for key, value in edits.items():
        assert cfg[key] == value
    # And the neighbours the loose match would have hit are untouched.
    assert cfg["opening_aspect"] == 0.7071
    assert cfg["fade_hours"] == 24
    assert cfg["heal_hours"] == 24


def test_the_reference_config_only_names_settings_that_exist(tmp_path):
    """Including the commented-out ones, which the editor will uncomment: a
    line for a key display.py dropped would be uncommented into a warning."""
    for line in (FRAME / "config.example.toml").read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip("# ").strip()
        if "=" not in stripped:
            continue
        key = stripped.split("=")[0].strip()
        if key.replace("_", "").isalnum() and key.islower() and " " not in key:
            if key in ("birdframe-mode",):
                continue
            assert key in display.DEFAULTS or key not in tui.HELP, \
                f"config.example.toml names {key}, which display.py does not know"


def test_the_basic_auth_password_is_not_printed(tmp_path):
    """`birdframe list` gets pasted into issues."""
    path = tmp_path / "config.toml"
    path.write_text(INSTALLED + 'basic_pass = "hunter2"\n', encoding="utf-8")
    values, _ = tui.read_config(str(path))
    assert tui.show_value(values["basic_pass"], "basic_pass") == "set"
    assert "hunter2" not in tui.show_value(values["basic_pass"], "basic_pass")
    # But it is still editable, so whoever is at the screen can see what they type.
    assert tui.edit_text(values["basic_pass"]) == "hunter2"


# --- one description of the settings, not four -------------------------------
# install.sh used to carry three config templates of its own, each restating a
# subset of the defaults and the prose around them - and they had already
# drifted (only the local one mentioned fresh_minutes, fade_hours or timeout).
# They are gone: install.sh asks for a mode and Python writes the reference
# file with that mode's values set.
@pytest.mark.parametrize("mode, kwargs, expected", [
    ("local", {}, {"shoot": True, "species_source": "", "image_url": ""}),
    ("birdweather", {"zip_code": "94107"},
     {"species_source": "birdweather", "zip": "94107", "shoot": True}),
    ("image", {"image_url": "https://bird.example/frame.png?k=abc"},
     {"shoot": False, "image_url": "https://bird.example/frame.png?k=abc",
      "base_url": "https://bird.example"}),
])
def test_each_mode_installs_a_working_config(tmp_path, mode, kwargs, expected):
    path = tmp_path / "config.toml"
    tui.init_config(str(path), mode, **kwargs)
    cfg = display.load_config(str(path))
    for key, value in expected.items():
        assert cfg[key] == value, key
    assert tui.mode_of(cfg) == mode


@pytest.mark.parametrize("mode, kwargs", [
    ("local", {}), ("birdweather", {"zip_code": "94107"}),
    ("image", {"image_url": "https://bird.example/f.png"}),
])
def test_an_installed_config_is_the_reference_file(tmp_path, mode, kwargs):
    """Every mode gets the whole documented reference, not a subset of it, so
    the three cannot drift apart and the Pi carries the explanations."""
    path = tmp_path / "config.toml"
    tui.init_config(str(path), mode, **kwargs)
    installed = path.read_text(encoding="utf-8").splitlines()
    reference = (FRAME / "config.example.toml").read_text(encoding="utf-8").splitlines()
    assert installed[0] == f"# birdframe-mode: {mode}"
    assert len(installed) == len(reference) + 1
    # Only the keys the mode actually decides differ from the reference.
    differing = {installed[i + 1].lstrip("# ").split("=")[0].strip()
                 for i, line in enumerate(reference) if line != installed[i + 1]}
    assert differing <= set(tui.init_changes(mode, **{
        "zip_code": kwargs.get("zip_code", ""),
        "image_url": kwargs.get("image_url", "")}))


def test_local_mode_changes_nothing_at_all(tmp_path):
    """The reference file already describes the default install."""
    assert tui.init_changes("local") == {}
    path = tmp_path / "config.toml"
    tui.init_config(str(path), "local")
    reference = (FRAME / "config.example.toml").read_text(encoding="utf-8")
    assert path.read_text(encoding="utf-8") == "# birdframe-mode: local\n" + reference


def test_init_refuses_a_mode_it_does_not_know():
    with pytest.raises(ValueError):
        tui.init_changes("teletype")


def test_an_installed_config_is_private(tmp_path):
    """It can hold basic_pass."""
    import stat

    path = tmp_path / "config.toml"
    tui.init_config(str(path), "local")
    mode = stat.S_IMODE(path.stat().st_mode)
    assert not mode & 0o077 or sys.platform == "win32"


# --- setting a key the file never mentioned ----------------------------------
def test_setting_a_key_the_file_omits_writes_it(tmp_path):
    """`birdframe-names on` against a config from before names existed. The
    file says nothing, which is not the same as already being at this value -
    if this were treated as a no-op the panel would never redraw."""
    path = tmp_path / "config.toml"
    path.write_text('# birdframe-mode: local\nshoot = true\n', encoding="utf-8")
    assert tui.main(["--config", str(path), "--set", "bird_names=true",
                     "--no-refresh"]) == 0
    assert "bird_names = true" in path.read_text(encoding="utf-8")
    # And then it is a no-op, so nothing refreshes twice.
    values, explicit = tui.read_config(str(path))
    assert "bird_names" in explicit


def test_get_tells_unset_apart_from_default(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text('# birdframe-mode: local\nshoot = true\n', encoding="utf-8")
    tui.main(["--config", str(path), "--get", "bird_names"])
    assert capsys.readouterr().out == ""          # the file does not say
    tui.main(["--config", str(path), "--get", "shoot"])
    assert capsys.readouterr().out.strip() == "true"


def test_get_refuses_a_key_that_does_not_exist(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("shoot = true\n", encoding="utf-8")
    assert tui.main(["--config", str(path), "--get", "shoot_titel"]) == 2


# --- the bash does not restate any of this -----------------------------------
def test_install_sh_no_longer_carries_config_templates():
    """Four descriptions of one setting is how the three modes drifted apart."""
    text = (FRAME / "install.sh").read_text(encoding="utf-8")
    for marker in ("shoot_title =", "saturation =", "fresh_minutes =",
                   "opening_aspect ="):
        assert marker not in text, f"install.sh is writing config again: {marker}"
    assert "--init" in text, "install.sh should ask config_tui.py for the config"


def test_birdframe_names_no_longer_rewrites_toml_itself():
    """One implementation of "edit a line of the config", not two."""
    text = (FRAME / "birdframe-names").read_text(encoding="utf-8")
    assert "awk" not in text
    assert "--get bird_names" in text and "--set" in text


# --- said in the terms someone would say it in -------------------------------
def test_every_setting_has_a_human_label():
    """`bird_names` is not what someone scanning for the bird names is reading
    for. The key still leads the help line, where it is useful for editing the
    file or scripting; it is just not what the row is called."""
    for _, keys in tui.sections():
        for key in keys:
            assert key in tui.LABELS, f"{key} has no human label"
            assert tui.label_of(key) != key


def test_the_matboard_choice_says_mat_and_no_mat():
    names = " ".join(name for name, _ in tui.PRESETS).lower()
    assert "with mat" in names and "no mat" in names


@pytest.mark.parametrize("key, value, expected", [
    ("collage_frac", 0.92, "92% of the opening"),
    ("opening", 0.97, "97% of the panel height"),
    ("shoot_collage_vh", 74, "74% of the render"),   # already 0-100, not scaled
    ("opening_aspect", 0.75, "0.75 wide to 1 tall"),  # a ratio, not a percentage
    ("saturation", 0.6, "60%"),
    ("hours", 48, "48 h"),
    ("timeout", 180, "180 s"),
    ("rotate", 270, "270 degrees"),
    ("fresh_minutes", 0, "off"),        # the zeroes that mean a word
    ("mat", 0, "none"),
    ("label_scale", 0, "as the mat gives it"),
    ("bird_names", True, "on"),
])
def test_values_read_as_english(key, value, expected):
    assert tui.show_value(value, key) == expected


def test_a_summary_still_reads_as_a_config_line():
    """"birds fill 92% of the opening" is right on the screen and wrong in the
    list of what was just written to the file."""
    assert tui.written_value("collage_frac", 0.98) == "0.98"
    assert tui.written_value("bird_names", True) == "true"
    assert tui.written_value("shoot_title", None) == "unset"
    assert tui.written_value("basic_pass", "hunter2") == "set"


def test_the_two_deciding_rows_lead_the_screen(editor):
    """The matboard was buried in the panel section, off the bottom of an 80x24
    ssh window with nothing saying so - indistinguishable from not existing.
    Both it and the source decide other rows, so both come first."""
    fields = [i for i, (shape, _) in enumerate(editor.rows) if shape != "header"]
    assert [editor.rows[i] for i in fields[:2]] == [("source", "source"), ("preset", "layout")]
    assert editor.index == fields[0]


def test_a_short_terminal_says_how_many_settings_there_are(monkeypatch, editor):
    """The screen scrolls. Without the count, a window that shows twenty rows
    looks exactly like a frame that only has twenty settings."""
    module = fake_curses([ord("q")])
    screen = module._screen
    written = []
    screen.addnstr = lambda y, x, text, n: written.append((y, text))
    monkeypatch.setitem(sys.modules, "curses", module)
    tui.run_screen(screen, editor)
    total = sum(len(keys) for _, keys in tui.sections()) + 2   # + source and matboard
    assert any(f"1/{total}" in text for _, text in written)


@pytest.mark.parametrize("key, typed, expected", [
    ("collage_frac", "98", 0.98),      # a percentage, as the row reads it out
    ("collage_frac", "0.98", 0.98),    # or the fraction the file holds
    ("opening", "97", 0.97),
    ("saturation", "60", 0.6),
    ("shoot_collage_vh", "74", 74),    # already a percentage - not scaled again
    ("hours", "48", 48),               # not a percentage at all
])
def test_a_percentage_can_be_typed_back_in(key, typed, expected):
    """The row says "92% of the opening". Handing that person a prompt that
    only takes 0.92 is asking them to do arithmetic to change what they were
    just shown. Everything shown as a percentage is capped at 1.0, so a value
    over 1 is unambiguous."""
    assert tui.parse_value(key, typed) == expected


@pytest.mark.parametrize("key, value", [
    ("collage_frac", 0.92), ("opening", 0.7071), ("saturation", 0.6),
    ("shoot_collage_vh", 52), ("hours", 24), ("rotate", 90),
])
def test_what_is_shown_is_what_comes_back_to_edit(key, value):
    """Round trip: the number offered in the prompt parses back to the value
    the row was showing."""
    assert tui.parse_value(key, tui.edit_text(value, key)) == value


# --- a setting that is quietly overridden ------------------------------------
# obtain_image checks species_source, then shoot, then image_url, returning at
# the first match. So image_url set while species_source is "birdweather" does
# nothing whatsoever - and the screen used to show the two as unrelated rows.
def test_the_source_is_one_choice_not_three_rows(editor):
    rows = [row for row in editor.rows if row[0] in ("source", "preset")]
    assert rows[0] == ("source", "source"), "the source should lead the screen"
    first_field = next(i for i, (shape, _) in enumerate(editor.rows) if shape != "header")
    assert editor.rows[first_field][0] == "source"


@pytest.mark.parametrize("mode, expected", [
    ("local", {"species_source": "", "shoot": True}),
    ("birdweather", {"species_source": "birdweather", "shoot": True}),
    ("image", {"species_source": "", "shoot": False}),
])
def test_each_source_sets_both_keys_that_decide_it(mode, expected):
    changes = next(c for name, _, c in tui.SOURCES if name == mode)
    assert changes == expected
    assert tui.mode_of(dict(display.DEFAULTS, **changes,
                            image_url="https://x/f.png")) == mode


def test_cycling_the_source_row_moves_species_source_and_shoot(monkeypatch, editor):
    keys = fake_curses([])
    start = tui.mode_of(editor.values)
    drive(monkeypatch, editor, [keys.KEY_RIGHT, ord("q"), ord("y")],
          at=next(i for i, (s, _) in enumerate(editor.rows) if s == "source"))
    assert tui.mode_of(editor.values) != start
    assert set(editor.changes) <= {"species_source", "shoot"}


def test_image_url_is_flagged_while_birdweather_wins():
    """The exact trap: you set the URL, the panel does not change, and nothing
    anywhere tells you the birds are still coming from BirdWeather."""
    values = dict(display.DEFAULTS, species_source="birdweather", zip="13037")
    assert "BirdWeather" in tui.shadow_reason("image_url", values)
    assert "BirdWeather" in tui.shadow_reason("base_url", values)


def test_the_outline_and_fade_are_flagged_as_impossible_on_birdweather():
    """BirdWeather carries no per-species last_seen, so fresh_slugs and
    fade_steps both come back empty - the settings cannot do anything."""
    values = dict(display.DEFAULTS, species_source="birdweather", zip="13037")
    for key in ("fresh_minutes", "fade_hours"):
        assert tui.shadow_reason(key, values), f"{key} should be flagged"
    # And the renderer agrees: no anchor, so nothing is ever outlined.
    assert display.fresh_slugs([{"sci": "Turdus migratorius"}], None, 30) == frozenset()


def test_a_fade_with_no_ramp_is_flagged():
    """fade_hours == hours is the shipped default and means nothing fades.
    display.fade_param returns "0" for it, which is invisible from the screen."""
    flat = dict(display.DEFAULTS, species_source="", fade_hours=24, hours=24)
    assert tui.shadow_reason("fade_hours", flat)
    assert display.fade_param(flat) == "0"

    ramped = dict(flat, fade_hours=12)
    assert tui.shadow_reason("fade_hours", ramped) is None
    assert display.fade_param(ramped) == "12-24"


def test_settings_that_are_doing_something_are_not_flagged():
    """A warning on every row is the same as no warnings."""
    live = dict(display.DEFAULTS, species_source="", shoot=True, fade_hours=12, hours=24)
    for key in ("base_url", "hours", "bird_names", "rotate", "shoot_title", "fresh_minutes"):
        assert tui.shadow_reason(key, live) is None, key
