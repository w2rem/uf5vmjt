import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.sections.stats import _float_history, render_core_bars  # noqa: E402


class FloatHistoryTests(unittest.TestCase):
    """The key once held raw (total, idle) jiffies tuples and was read as
    percentages — min() on a tuple raised TypeError and killed the CPU panel."""

    def test_tuples_are_dropped(self):
        self.assertEqual(_float_history({"cpu0": (1000, 600)}), {})

    def test_ints_and_floats_survive(self):
        self.assertEqual(_float_history({"cpu0": 40, "cpu1": 12.5}),
                         {"cpu0": 40.0, "cpu1": 12.5})

    def test_mixed_shapes_filtered(self):
        raw = {"cpu0": 40, "cpu1": (1, 2), "cpu2": 7.5, "cpu3": "x", "cpu4": None}
        self.assertEqual(_float_history(raw), {"cpu0": 40.0, "cpu2": 7.5})

    def test_non_dict_is_empty(self):
        for bad in (None, [], 5, "x", (1, 2)):
            self.assertEqual(_float_history(bad), {})

    def test_legacy_tuple_payload_does_not_crash_bars(self):
        # The exact deployed failure: session_state held the old tuple shape
        # and the panel raised. Rendering must survive it.
        history = _float_history({"cpu0": (1000, 600), "cpu1": (1000, 900)})
        bars = render_core_bars({"cpu0": 40.0, "cpu1": 0.0}, history, limit=2)
        self.assertEqual(len(bars), 2)


class CoreBarsTests(unittest.TestCase):
    def test_bars_render_for_every_core(self):
        bars = render_core_bars({"cpu0": 10.0, "cpu1": 20.0}, {}, limit=2)
        self.assertEqual(len(bars), 2)
        self.assertIn("height:10.0%", bars[0])
        self.assertIn("height:20.0%", bars[1])

    def test_ours_in_accent_host_in_border(self):
        bars = render_core_bars({"cpu0": 5.0, "cpu1": 5.0, "cpu2": 5.0}, {}, limit=2)
        self.assertIn("#4A7FA5", bars[0])
        self.assertIn("#4A7FA5", bars[1])
        self.assertNotIn("#4A7FA5", bars[2])
        self.assertIn("#E2E7EB", bars[2])

    def test_ghost_only_when_value_dropped(self):
        rising = render_core_bars({"cpu0": 50.0}, {"cpu0": 10.0}, limit=1)
        self.assertNotIn("uf5-ghost", rising[0])
        falling = render_core_bars({"cpu0": 10.0}, {"cpu0": 60.0}, limit=1)
        self.assertIn("uf5-ghost", falling[0])
        self.assertIn("height:50.0%", falling[0])

    def test_tiny_drop_below_threshold_has_no_ghost(self):
        bars = render_core_bars({"cpu0": 50.0}, {"cpu0": 50.3}, limit=1)
        self.assertNotIn("uf5-ghost", bars[0])

    def test_values_are_clamped(self):
        bars = render_core_bars({"cpu0": 250.0, "cpu1": -10.0}, {}, limit=2)
        self.assertIn("height:100.0%", bars[0])
        self.assertIn("height:0.0%", bars[1])

    def test_labels_are_sanitised(self):
        bars = render_core_bars({"cpu 12 weird": 5.0}, {}, limit=1)
        self.assertIn("cpu12weird", bars[0])

    def test_missing_history_defaults_to_current(self):
        bars = render_core_bars({"cpu0": 30.0}, {}, limit=1)
        self.assertNotIn("uf5-ghost", bars[0])


if __name__ == "__main__":
    unittest.main()
