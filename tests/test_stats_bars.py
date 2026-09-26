import subprocess
import sys
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.core.config import COLOR_BORDER, COLOR_OURS  # noqa: E402
from lib.sections.stats import (ROLE_LABEL, _float_history,  # noqa: E402
                                _live_cpu_split, _live_per_process,
                                render_core_bars)
from lib.services import procs  # noqa: E402
from lib.services.limits import effective_cores  # noqa: E402


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

    def test_ours_green_host_pale(self):
        bars = render_core_bars({"cpu0": 5.0, "cpu1": 5.0, "cpu2": 5.0}, {}, limit=2)
        self.assertIn(COLOR_OURS, bars[0])
        self.assertIn(COLOR_OURS, bars[1])
        self.assertNotIn(COLOR_OURS, bars[2])
        self.assertIn(COLOR_BORDER, bars[2])

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


class ProcessesTabTests(unittest.TestCase):
    """The Processes tab unpacked effective_cores() into three names while it
    returns two — ValueError on every render."""

    def test_effective_cores_unpacks_to_two(self):
        ours, _source = effective_cores()
        self.assertIsInstance(ours, int)
        self.assertGreater(ours, 0)

    def test_census_renders_a_row_per_pid(self):
        # The table body must be built from census() without assuming a
        # particular unpacking shape of the limits helpers.
        rows = procs.census()
        self.assertTrue(rows)
        for p in rows:
            role = ROLE_LABEL.get(p.role, p.role)
            self.assertTrue(role)
            self.assertIsInstance(p.pid, int)


class LiveCpuSplitTests(unittest.TestCase):
    """The CPU panel measures a live rate between ticks, not a lifetime average."""

    def test_first_tick_has_no_baseline(self):
        st = types.SimpleNamespace(session_state={})
        self.assertEqual(_live_cpu_split(st, cores=2), (0.0, 0.0))
        self.assertIn("uf5_our_cpu_at", st.session_state)

    def test_burner_reads_as_half_of_a_two_core_quota(self):
        # A process pinned at 100% of one core is 50% of a 2-core quota.
        # It must exist before the first snapshot to get a baseline.
        burner = subprocess.Popen(
            [sys.executable, "-c", "x=0\nwhile True:\n x+=1"])
        try:
            time.sleep(0.5)
            st = types.SimpleNamespace(session_state={})
            _live_cpu_split(st, cores=2)
            time.sleep(2.5)
            ours, other = _live_cpu_split(st, cores=2)
            self.assertGreater(ours, 20.0, f"expected real load, got {ours}%")
            self.assertLess(ours, 100.0)
            self.assertEqual(other, 0.0)
        finally:
            burner.kill()
            burner.wait()

    def test_per_process_reports_share_of_one_core(self):
        burner = subprocess.Popen(
            [sys.executable, "-c", "x=0\nwhile True:\n x+=1"])
        try:
            time.sleep(0.5)
            st = types.SimpleNamespace(session_state={})
            self.assertEqual(_live_per_process(st), {})
            time.sleep(2.5)
            per = _live_per_process(st)
            self.assertIn(burner.pid, per)
            self.assertGreater(per[burner.pid], 40.0)
        finally:
            burner.kill()
            burner.wait()

    def test_idle_container_reads_zero_not_crash(self):
        st = types.SimpleNamespace(session_state={})
        _live_per_process(st)
        time.sleep(0.3)
        per = _live_per_process(st)
        self.assertTrue(all(v >= 0.0 for v in per.values()))


if __name__ == "__main__":
    unittest.main()
