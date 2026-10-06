#!/usr/bin/env python3
"""Unit tests for the underscan planner and xrandr parser."""

import importlib.machinery
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader("underscan", str(ROOT / "underscan"))
spec = importlib.util.spec_from_loader("underscan", loader)
underscan = importlib.util.module_from_spec(spec)
sys.modules["underscan"] = underscan
loader.exec_module(underscan)

INTEL = """\
Screen 0: minimum 320 x 200, current 1920 x 1080, maximum 16384 x 16384
HDMI-1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis) 698mm x 392mm
	EDID:
		00ffffffffffff00
	content type: No Data
		supported: No Data, Graphics, Photo, Cinema, Game
	Broadcast RGB: Automatic
		supported: Automatic, Full, Limited 16:235
   1920x1080     60.00*+  59.94    50.00
   1280x720      60.00    59.94
   1024x768      60.00
DP-1 disconnected (normal left inverted right x axis y axis)
"""

AMD = """\
Screen 0: minimum 320 x 200, current 1920 x 1080, maximum 16384 x 16384
HDMI-A-0 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis) 1600mm x 900mm
	underscan: off
		supported: off, on, auto
	underscan hborder: 0
		range: (0, 128)
	underscan vborder: 0
		range: (0, 128)
   1920x1080     60.00*+
"""

DUAL = """\
Screen 0: minimum 320 x 200, current 3840 x 1080, maximum 16384 x 16384
HDMI-1 connected 1920x1080+0+0 (normal left inverted right x axis y axis) 698mm x 392mm
   1920x1080     60.00*+
DP-1 connected primary 1920x1080+1920+0 (normal left inverted right x axis y axis) 530mm x 300mm
   1920x1080     60.00*+
"""

EMULATED = """\
Screen 0: minimum 16 x 16, current 1920 x 1080, maximum 32767 x 32767
HDMI-1 connected 1920x1080+0+0 (normal left inverted right x axis y axis) 600mm x 340mm
	RANDR Emulation: 1
   1920x1080     60.00*+
"""

VERBOSE = """\
HDMI-1 connected 1920x1080+0+0 (0x48) normal (normal left inverted right x axis y axis)
	Transform:  1.052632 0.000000 -50.526316
	            0.000000 1.052632 -28.421053
	            0.000000 0.000000 1.000000
	           filter: bilinear
DP-1 disconnected
	Transform:  1.000000 0.000000 0.000000
	            0.000000 1.000000 0.000000
	            0.000000 0.000000 1.000000
"""


class ParseTests(unittest.TestCase):
    def test_intel_output_mode_and_content_type(self):
        screen = underscan.parse_xrandr(INTEL)
        self.assertEqual((screen.width, screen.height), (1920, 1080))
        self.assertFalse(screen.emulated())
        output = screen.by_name("HDMI-1")
        self.assertTrue(output.primary)
        mode = output.current_mode()
        self.assertEqual((mode.width, mode.height, mode.rate), (1920, 1080, "60.00"))
        self.assertTrue(mode.preferred)
        self.assertEqual(output.props["content type"].value, "No Data")
        self.assertIn("Graphics", output.props["content type"].supported)
        self.assertIn("Limited 16:235", output.props["Broadcast RGB"].supported)
        self.assertFalse(screen.by_name("DP-1").connected)

    def test_amd_underscan_range(self):
        output = underscan.parse_xrandr(AMD).by_name("HDMI-A-0")
        self.assertEqual(output.props["underscan"].supported, ["off", "on", "auto"])
        self.assertEqual(output.props["underscan hborder"].maximum, 128)
        self.assertIsNone(underscan.hardware_possible(output, underscan.Margins(40, 40, 24, 24)))

    def test_emulation_flag(self):
        self.assertTrue(underscan.parse_xrandr(EMULATED).emulated())

    def test_transform_matrix(self):
        matrix = underscan.parse_transform(VERBOSE, "HDMI-1")
        self.assertAlmostEqual(matrix[0], 1.052632)
        self.assertAlmostEqual(matrix[2], -50.526316)
        self.assertAlmostEqual(matrix[4], 1.052632)
        other = underscan.parse_transform(VERBOSE, "DP-1")
        self.assertAlmostEqual(other[0], 1.0)


class MarginTests(unittest.TestCase):
    def test_percent_pixels_and_pairs(self):
        self.assertEqual(
            underscan.parse_amount("5%", 1920, 1080),
            underscan.Margins(96, 96, 54, 54),
        )
        self.assertEqual(underscan.parse_amount("48", 1920, 1080), underscan.Margins(48, 48, 48, 48))
        self.assertEqual(underscan.parse_amount("48x27", 1920, 1080), underscan.Margins(48, 48, 27, 27))
        self.assertEqual(
            underscan.parse_amount("10,12,14,16", 1920, 1080),
            underscan.Margins(10, 12, 14, 16),
        )

    def test_odd_content_becomes_even(self):
        margins = underscan.normalize_margins(1920, 1080, underscan.Margins(5, 4, 3, 4))
        self.assertEqual((margins.right, margins.bottom), (5, 5))
        self.assertEqual((1920 - margins.left - margins.right) % 2, 0)

    def test_rejects_huge_inset(self):
        with self.assertRaises(underscan.UnderscanError):
            underscan.normalize_margins(1920, 1080, underscan.Margins(600, 600, 10, 10))

    def test_scales_when_the_mode_changes(self):
        scaled = underscan.scale_margins(underscan.Margins(96, 96, 54, 54), 1920, 1080, 1280, 720)
        self.assertEqual(scaled, underscan.Margins(64, 64, 36, 36))


class PlanTests(unittest.TestCase):
    def test_intel_sharp_command_insets_without_scaling(self):
        screen = underscan.parse_xrandr(INTEL)
        output = screen.by_name("HDMI-1")
        margins = underscan.normalize_margins(1920, 1080, underscan.Margins(96, 96, 54, 54))
        attempts = underscan.plan_apply(screen, output, margins, "auto")
        command = attempts[0].commands[0]
        self.assertEqual(attempts[0].method, "sharp")
        self.assertIn("--fb", command)
        self.assertEqual(command[command.index("--fb") + 1], "1728x972")
        self.assertIn("1,0,-96,0,1,-54,0,0,1", command)
        self.assertIn("HDMI-1", command)
        self.assertIn("60.00", command)
        self.assertEqual(attempts[-1].method, "scale")

    def test_amd_prefers_hardware_underscan(self):
        screen = underscan.parse_xrandr(AMD)
        output = screen.by_name("HDMI-A-0")
        margins = underscan.Margins(40, 40, 24, 24)
        attempts = underscan.plan_apply(screen, output, margins, "auto")
        self.assertEqual(attempts[0].method, "hardware")
        command = attempts[0].commands[0]
        self.assertEqual(command[command.index("underscan hborder") + 1], "40")
        self.assertEqual(command[command.index("underscan vborder") + 1], "24")

    def test_asymmetric_amd_uses_the_transform(self):
        screen = underscan.parse_xrandr(AMD)
        output = screen.by_name("HDMI-A-0")
        margins = underscan.Margins(30, 40, 20, 20)
        normalized = underscan.normalize_margins(1920, 1080, margins)
        attempts = underscan.plan_apply(screen, output, normalized, "auto")
        self.assertEqual(attempts[0].method, "sharp")

    def test_second_monitor_is_scaled_so_the_desktop_is_not_shrunk(self):
        screen = underscan.parse_xrandr(DUAL)
        output = screen.by_name("HDMI-1")
        margins = underscan.Margins(48, 48, 27, 27)
        attempts = underscan.plan_apply(screen, output, margins, "auto")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].method, "scale")
        command = attempts[0].commands[0]
        self.assertIn("3840x1080", command)
        self.assertIn("--filter", command)
        matrix = command[command.index("--transform") + 1].split(",")
        self.assertAlmostEqual(float(matrix[0]), 1920 / (1920 - 96), places=5)
        self.assertAlmostEqual(float(matrix[2]), -(1920 / (1920 - 96)) * 48, places=4)

    def test_reset_restores_the_mode_on_a_single_display(self):
        screen = underscan.parse_xrandr(INTEL)
        command = underscan.plan_reset(screen, screen.by_name("HDMI-1"))
        self.assertIn("1920x1080", command)
        self.assertIn("none", command)
        self.assertEqual(command[command.index("--fb") + 1], "1920x1080")

    def test_sharp_verify_matches_the_shrunk_framebuffer(self):
        screen = underscan.parse_xrandr(INTEL)
        screen.width, screen.height = 1728, 972
        self.assertTrue(underscan.sharp_applied(screen, underscan.Margins(96, 96, 54, 54)))
        self.assertFalse(underscan.sharp_applied(screen, underscan.Margins(10, 10, 10, 10)))


class ConfigTests(unittest.TestCase):
    def test_round_trip(self):
        import tempfile

        previous = underscan.os.environ.get("UNDERSCAN_HOME")
        with tempfile.TemporaryDirectory() as temporary:
            underscan.os.environ["UNDERSCAN_HOME"] = temporary
            try:
                output = underscan.parse_xrandr(INTEL).by_name("HDMI-1")
                mode = output.current_mode()
                margins = underscan.Margins(96, 96, 54, 54)
                underscan.save_config(
                    underscan.config_from_margins(output, mode, margins, "sharp", False, True)
                )
                loaded = underscan.load_config()
                self.assertEqual(loaded["output"], "HDMI-1")
                self.assertEqual(underscan.margins_from_config(loaded), margins)
                self.assertTrue(json.loads(underscan.config_path().read_text())["enabled"])
            finally:
                if previous is None:
                    underscan.os.environ.pop("UNDERSCAN_HOME", None)
                else:
                    underscan.os.environ["UNDERSCAN_HOME"] = previous


class SessionTests(unittest.TestCase):
    def test_wayland_is_refused(self):
        previous = underscan.os.environ.get("XDG_SESSION_TYPE")
        underscan.os.environ["XDG_SESSION_TYPE"] = "wayland"
        try:
            reason = underscan.session_block_reason()
        finally:
            if previous is None:
                underscan.os.environ.pop("XDG_SESSION_TYPE", None)
            else:
                underscan.os.environ["XDG_SESSION_TYPE"] = previous
        self.assertIn("Cinnamon", reason)
        self.assertIn("Xorg", reason)

    def test_flags_can_follow_the_subcommand(self):
        args = underscan.build_parser().parse_args(["set", "--output", "HDMI-1", "5%", "--dry-run", "--save"])
        self.assertEqual(args.cmd, "set")
        self.assertEqual(args.output, "HDMI-1")
        self.assertEqual(args.amount, "5%")
        self.assertTrue(args.dry_run)
        self.assertTrue(args.save)


if __name__ == "__main__":
    unittest.main()
