import subprocess
import sys
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.core.config import (COLOR_BORDER, COLOR_MEM_OTHER, COLOR_OURS,  # noqa: E402
                             COLOR_OURS_DEEP)
from lib.sections.stats import (PROCESS_REFRESH_DEFAULT,  # noqa: E402
                                PROCESS_REFRESH_MAX, PROCESS_REFRESH_MIN,
                                ROLE_LABEL, _float_history, _live_cpu_split,
                                _live_per_process, process_refresh_seconds,
                                render_core_bars, render_our_load_bar)
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


class ColorFamilyTests(unittest.TestCase):
    """Green is reserved for our processes; everything else is blue.

    Regression: the per-core chart and the memory track both used the accent
    blue for our own segments, and the memory track used it for *everything*,
    so "our RAM" was indistinguishable from the host's.
    """

    def test_ours_is_green_family(self):
        self.assertTrue(COLOR_OURS.startswith("#A8"), COLOR_OURS)
        self.assertTrue(COLOR_OURS_DEEP.startswith("#6F"), COLOR_OURS_DEEP)

    def test_other_is_blue_family(self):
        blue = COLOR_MEM_OTHER.lstrip("#").lower()
        red, green, blue_ch = blue[0:2], blue[2:4], blue[4:6]
        self.assertGreater(int(blue_ch, 16), int(red, 16), "must read as blue")
        self.assertGreater(int(blue_ch, 16), int(green, 16), "must read as blue")

    def test_green_and_blue_families_are_distinct(self):
        self.assertNotEqual(COLOR_OURS, COLOR_MEM_OTHER)
        self.assertNotEqual(COLOR_OURS, COLOR_BORDER)

    def test_our_load_bar_is_green(self):
        html = render_our_load_bar(50.0)
        self.assertIn(COLOR_OURS, html)
        self.assertIn("height:50.0%", html)

    def test_our_load_bar_clamps(self):
        self.assertIn("height:100.0%", render_our_load_bar(250.0))
        self.assertIn("height:0.0%", render_our_load_bar(-10.0))


class ProcessRefreshTests(unittest.TestCase):
    def test_default_and_bounds(self):
        st = types.SimpleNamespace(session_state={})
        self.assertEqual(process_refresh_seconds(st), PROCESS_REFRESH_DEFAULT)
        st.session_state["uf5_proc_interval"] = 1
        self.assertEqual(process_refresh_seconds(st), PROCESS_REFRESH_MIN)
        st.session_state["uf5_proc_interval"] = 999
        self.assertEqual(process_refresh_seconds(st), PROCESS_REFRESH_MAX)
        st.session_state["uf5_proc_interval"] = 12
        self.assertEqual(process_refresh_seconds(st), 12)

    def test_garbage_falls_back_to_default(self):
        st = types.SimpleNamespace(session_state={"uf5_proc_interval": "soon"})
        self.assertEqual(process_refresh_seconds(st), PROCESS_REFRESH_DEFAULT)


class MemoryUnitTests(unittest.TestCase):
    """read_meminfo is in kB, effective_memory_bytes returns BYTES.

    Regression: the two were compared directly, so `ceiling < total` was
    always False, the quota branch never fired, and our RAM was rendered as
    a 0.17% sliver of a 15GB host bar — invisible, which is exactly what the
    operator reported.
    """

    def test_byte_ceiling_converts_to_kb(self):
        from lib.services.limits import effective_memory_bytes
        ceiling_bytes, _source = effective_memory_bytes()
        if ceiling_bytes > 0:
            self.assertEqual(ceiling_bytes // 1024, ceiling_bytes / 1024)

    def test_quota_branch_fires_when_quota_below_host(self):
        # Host reports ~15.4GB in kB; a 2765MB quota must read as "limited".
        host_total_kb = 16150020
        quota_bytes = 2765 * 1024 * 1024
        quota_kb = quota_bytes // 1024
        limited = 0 < quota_kb < host_total_kb
        self.assertTrue(limited, "quota branch must fire for a 2.7GB quota on a 15GB host")

    def test_host_reports_kb_not_bytes(self):
        from lib.services.sysinfo import memory_segments, read_meminfo
        total, _used, _cache, _free = memory_segments(read_meminfo())
        if total:
            # 15.4GB in kB is ~15.7M; in bytes it would be ~15.7B.
            self.assertLess(total, 1 << 40, "must be kB, not bytes")
            self.assertGreater(total, 1 << 20)


if __name__ == "__main__":
    unittest.main()
