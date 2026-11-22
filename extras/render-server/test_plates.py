"""Plates (plates.py): settings, the shot each one asks for, and the finished picture."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "frame"))
sys.path.insert(0, str(HERE))
import display  # noqa: E402
import plates  # noqa: E402

PAPER = (252, 252, 251)


def frame_cfg(**over):
    cfg = display.load_config(None)
    cfg.update(over)
    return cfg


class CheckTest(unittest.TestCase):
    def test_fills_in_from_the_frame(self):
        plate = plates.check("e1002", {"width": 800, "height": 480},
                             frame_cfg(bird_names=False, saturation=0.4))
        self.assertEqual(plate["name"], "e1002")
        self.assertFalse(plate["names"])
        self.assertEqual(plate["saturation"], 0.4)
        self.assertTrue(plate["title"] and plate["fade"] and plate["fresh"])
        self.assertEqual(plate["dither"], "none")

    def test_rejects_what_it_does_not_know(self):
        cfg = frame_cfg()
        for name, raw, word in (
                ("e1002", {"width": 800, "height": 480, "nmaes": False}, "nmaes"),
                ("e1002", {"width": 800}, "height"),
                ("e1002", {"width": 800, "height": 480, "dither": "floyd"}, "dither"),
                ("e1002", {"width": 800, "height": 480, "fade": "no"}, "fade"),
                ("e1002", {"width": 800, "height": 480, "saturation": 2}, "saturation"),
                ("frame", {"width": 800, "height": 480}, "frame"),
                ("Big Panel", {"width": 800, "height": 480}, "name")):
            with self.subTest(name=name, raw=raw):
                with self.assertRaises(ValueError) as e:
                    plates.check(name, raw, cfg)
                self.assertIn(word, str(e.exception))

    def test_load_reads_tables_in_order_and_tolerates_no_file(self):
        self.assertEqual(plates.load("/nonexistent/plates.toml", frame_cfg()), [])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "plates.toml")
            with open(path, "w") as f:
                f.write('[e1002]\nwidth = 800\nheight = 480\nnames = false\ndither = "spectra6"\n'
                        '[tall]\nwidth = 480\nheight = 800\n')
            found = plates.load(path, frame_cfg())
        self.assertEqual([p["name"] for p in found], ["e1002", "tall"])
        self.assertEqual(found[0]["dither"], "spectra6")

    def test_preview_query(self):
        plate = plates.from_query({"width": ["800"], "height": ["480"], "names": ["0"],
                                   "fade": ["false"], "dither": ["spectra6"]}, frame_cfg())
        self.assertEqual(plate["name"], "preview")
        self.assertFalse(plate["names"] or plate["fade"])
        self.assertTrue(plate["title"])
        with self.assertRaises(ValueError):
            plates.from_query({"width": ["wide"], "height": ["480"]}, frame_cfg())


class ShootArgsTest(unittest.TestCase):
    def test_landscape_without_text_or_fade(self):
        cfg = frame_cfg(fade_hours=12, hours=48, fresh_minutes=30)
        plate = plates.check("e1002", {"width": 800, "height": 480, "names": False,
                                       "title": False, "fade": False}, cfg)
        args = plates.shoot_args(plate, cfg)
        self.assertEqual((args["vw"], args["vh"]), (800, 480))
        self.assertEqual((args["title"], args["subtitle"]), ("", ""))
        self.assertFalse(args["bird_names"])
        self.assertEqual(args["fade"], "0")
        self.assertEqual(args["fresh_minutes"], 30)
        self.assertEqual((args["cluster_xbias"], args["cluster_ybias"]), (1.67, 1.0))

    def test_defaults_follow_the_frame(self):
        cfg = frame_cfg(fade_hours=12, hours=48, shoot_title="Avian Visitors",
                        shoot_cluster_ybias=1.2)
        plate = plates.check("tall", {"width": 480, "height": 800, "fresh": False}, cfg)
        args = plates.shoot_args(plate, cfg)
        self.assertEqual(args["title"], "Avian Visitors")
        self.assertEqual(args["fade"], "12-48")
        self.assertEqual(args["fresh_minutes"], 0)
        self.assertEqual((args["cluster_xbias"], args["cluster_ybias"]), (1.0, 1.2))


class SignatureTest(unittest.TestCase):
    def test_a_plate_without_fade_ignores_it(self):
        cfg = frame_cfg()
        plate = plates.check("e1002", {"width": 800, "height": 480, "fade": False}, cfg)
        with mock.patch.object(display, "fade_steps", side_effect=AssertionError), \
                mock.patch.object(display, "fresh_slugs", return_value=frozenset()), \
                mock.patch.object(display, "signature", return_value="sig") as sig:
            self.assertEqual(plates.signature(plate, [], None, cfg)[0], "sig")
        self.assertIsNone(sig.call_args.args[2])

    def test_settings_are_part_of_it(self):
        cfg = frame_cfg()
        a = plates.check("e1002", {"width": 800, "height": 480}, cfg)
        b = plates.check("e1002", {"width": 800, "height": 480, "margin": 4}, cfg)
        with mock.patch.object(display, "signature", return_value="sig"), \
                mock.patch.object(display, "fresh_slugs", return_value=frozenset()), \
                mock.patch.object(display, "fade_steps", return_value={}):
            self.assertNotEqual(plates.signature(a, [], None, cfg), plates.signature(b, [], None, cfg))


class FinishTest(unittest.TestCase):
    def shot(self):
        # A 2x shot with one "bird": a red-to-blue block off-centre on paper.
        img = Image.new("RGB", (1600, 960), PAPER)
        for x in range(500, 900):
            for y in range(400, 640):
                img.putpixel((x, y), (200 - (x - 500) // 2, 40, 60 + (x - 500) // 2))
        return img

    def test_crops_to_the_birds_and_fills_the_panel(self):
        plate = plates.check("e1002", {"width": 800, "height": 480, "margin": 8}, frame_cfg())
        img, ink = plates.finish(self.shot(), plate)
        self.assertEqual(img.size, (800, 480))
        self.assertGreater(ink, 0.05)
        box = Image.eval(img, lambda v: 0 if v > 240 else 255).convert("L").getbbox()
        # 400x240 of birds scaled to fit 784x464 -> 773x464, centred.
        self.assertEqual((box[1], box[3]), (8, 472))
        self.assertAlmostEqual((box[0] + box[2]) / 2, 400, delta=2)

    def test_spectra6_writes_only_pure_inks(self):
        plate = plates.check("e1002", {"width": 800, "height": 480, "dither": "spectra6"}, frame_cfg())
        img, _ = plates.finish(self.shot(), plate)
        colours = {c for _, c in img.getcolors(1 << 16)}
        self.assertLessEqual(colours, set(plates.PURE))
        self.assertIn((255, 0, 0), colours)

    def test_an_empty_shot_has_no_ink(self):
        plate = plates.check("e1002", {"width": 800, "height": 480}, frame_cfg())
        img, ink = plates.finish(Image.new("RGB", (1600, 960), PAPER), plate)
        self.assertEqual((img.size, ink), ((800, 480), 0.0))


if __name__ == "__main__":
    unittest.main()
