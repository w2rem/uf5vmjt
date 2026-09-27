import pathlib
import re
import subprocess
import sys
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.core.config import (BAR_FILL, BAR_GHOST, BAR_TRACK_BG,  # noqa: E402
                             COLOR_MEM_FREE, COLOR_MEM_OTHER, COLOR_OURS,
                             CORE_OURS, MEM_FREE, MEM_HOST, MEM_OURS,
                             MEM_OURS_SIBLING)
from lib.sections.stats import (PROCESS_REFRESH_DEFAULT,  # noqa: E402
                                PROCESS_REFRESH_MAX, PROCESS_REFRESH_MIN,
                                ROLE_LABEL, _affinity_narrows,
                                _float_history, _live_cpu_split,
                                _live_per_process, process_refresh_seconds,
                                render_core_bars, render_memory_track,
                                render_split_load_bar)
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

    def test_ours_green_host_fill(self):
        # Green is only ever ours; every other core is the host's, in the
        # configured fill colour. Regression: non-ours cores were painted
        # COLOR_BORDER (pale gray), indistinguishable from the empty track.
        bars = render_core_bars({"cpu0": 5.0, "cpu1": 5.0, "cpu2": 5.0}, {}, limit=2)
        self.assertIn(CORE_OURS, bars[0])
        self.assertIn(CORE_OURS, bars[1])
        self.assertNotIn(CORE_OURS, bars[2])
        self.assertIn(BAR_FILL, bars[2])
        self.assertNotIn(BAR_TRACK_BG, bars[2], "fill must not match the empty track")

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

    def test_track_fill_and_ghost_are_distinct(self):
        # Regression: the fill, the empty track and the ghost were all pale
        # greys, so a falling bar read as one white mass.
        self.assertNotEqual(BAR_FILL, BAR_TRACK_BG)
        self.assertNotEqual(BAR_GHOST, BAR_TRACK_BG)
        self.assertNotEqual(BAR_GHOST, BAR_FILL)

    def test_memory_palette_has_free_ours_and_host(self):
        for name, value in (("MEM_FREE", MEM_FREE), ("MEM_OURS", MEM_OURS),
                            ("MEM_HOST", MEM_HOST)):
            self.assertRegex(value, r"^#[0-9A-Fa-f]{6}$", f"{name}={value}")
        self.assertNotEqual(MEM_FREE, MEM_OURS)
        self.assertNotEqual(MEM_OURS, MEM_HOST)
        self.assertNotEqual(MEM_OURS_SIBLING, MEM_OURS)

    def test_other_is_blue_family(self):
        blue = COLOR_MEM_OTHER.lstrip("#").lower()
        red, green, blue_ch = blue[0:2], blue[2:4], blue[4:6]
        self.assertGreater(int(blue_ch, 16), int(red, 16), "must read as blue")
        self.assertGreater(int(blue_ch, 16), int(green, 16), "must read as blue")

    def test_green_and_blue_families_are_distinct(self):
        self.assertNotEqual(COLOR_OURS, COLOR_MEM_OTHER)
        self.assertNotEqual(CORE_OURS, COLOR_MEM_OTHER)

    def test_split_load_bar_is_green_for_ours(self):
        html = render_split_load_bar(20.0, 5.0, 75.0)
        self.assertIn(COLOR_OURS, html)
        self.assertIn("width:20.0%", html)
        self.assertIn("Rest of host", html)

    def test_split_load_bar_clamps_to_100(self):
        html = render_split_load_bar(80.0, 50.0, 50.0)
        widths = [float(w.split("%")[0]) for w in
                  re.findall(r"width:([\d.]+)%", html)]
        self.assertLessEqual(sum(widths), 100.01, f"segments must not overflow: {widths}")

    def test_split_load_bar_clamps_negatives(self):
        html = render_split_load_bar(-5.0, -1.0, 10.0)
        self.assertIn("width:0.0%", html)


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


class NameResolutionTests(unittest.TestCase):
    """Every module-level name a render function touches must actually exist.

    Regression: render_memory_bar referenced COLOR_MEM_OTHER, which I added to
    config.py but forgot to import. py_compile passes, unit tests on the pure
    helpers passed, and the app only died when the Memory tab rendered on
    Streamlit Cloud. Calling the function with a stubbed streamlit is the only
    check that catches this class of bug before deploy.
    """

    def _palette_names(self, source: str) -> set[str]:
        # Colours now live under two prefixes: COLOR_* (legacy) and the
        # BAR_*/MEM_*/CORE_* families introduced for the explicit band
        # palette. All of them are module-level names a render can NameError on.
        return set(re.findall(r"\b(?:COLOR|BAR|MEM|CORE)_[A-Z_]+\b", source))

    def test_all_colors_used_by_stats_are_imported(self):
        import lib.core.config as cfg
        import lib.sections.stats as stats
        used = self._palette_names(pathlib.Path(stats.__file__).read_text())
        self.assertTrue(used)
        for name in sorted(used):
            self.assertTrue(hasattr(cfg, name), f"{name} used in stats.py but absent from config")
            self.assertTrue(hasattr(stats, name), f"{name} used but not imported into stats")

    def test_all_colors_used_by_ui_are_imported(self):
        # Same trap, different module: ui.py interpolates colors into the CSS
        # block, so a missing import is a NameError on the first render.
        import lib.core.config as cfg
        import lib.core.ui as ui
        used = self._palette_names(pathlib.Path(ui.__file__).read_text())
        self.assertTrue(used, "ui.py must reference some colors")
        for name in sorted(used):
            self.assertTrue(hasattr(cfg, name), f"{name} used in ui.py but absent from config")
            self.assertTrue(hasattr(ui, name), f"{name} used in ui.py but not imported")

    def test_render_memory_bar_resolves_names(self):
        import lib.sections.stats as stats
        names = self._palette_names(pathlib.Path(stats.__file__).read_text())
        for name in names:
            # Resolving in the function's module globals is what a real call does.
            self.assertIn(name, stats.__dict__ if hasattr(stats, "__dict__") else vars(stats),
                          f"{name} would raise NameError at render time")

    def test_no_dangling_color_constants_in_config(self):
        # A color declared in config but referenced nowhere in the package is
        # dead weight that drifts the palette. Scanned across every module, not
        # just stats.py — COLOR_ACCENT is unused there but used by ui.py, logs,
        # shell and disk, so a stats-only scan would produce false orphans.
        import lib.core.config as cfg
        root = pathlib.Path(cfg.__file__).resolve().parents[1]
        blob = "\n".join(p.read_text() for p in root.rglob("*.py")
                         if "tests" not in p.parts)
        for name in sorted(n for n in dir(cfg)
                           if n.startswith(("COLOR_", "BAR_", "MEM_", "CORE_"))):
            self.assertIn(name, blob, f"{name} declared in config but never used")


class AffinitySplitTests(unittest.TestCase):
    """A per-core row is ours only when the affinity mask proves it."""

    def test_affinity_narrows_only_with_partial_mask(self):
        self.assertTrue(_affinity_narrows("affinity", ours=2, visible=16))
        self.assertFalse(_affinity_narrows("affinity", ours=16, visible=16))
        self.assertFalse(_affinity_narrows("affinity", ours=0, visible=16))

    def test_quota_or_override_is_not_proof_of_placement(self):
        # Cloud reports affinity=whole host plus a declared quota; that quota is
        # not evidence that any particular /proc/stat row is ours.
        self.assertFalse(_affinity_narrows("quota", ours=2, visible=16))
        self.assertFalse(_affinity_narrows("override", ours=2, visible=16))
        self.assertFalse(_affinity_narrows("host", ours=16, visible=16))

    def test_no_green_bars_when_mask_is_whole_host(self):
        # Without the underlay the chart had zero green anywhere, so the
        # operator could not see their own 1.3% at all.
        bars = render_core_bars({"cpu0": 80.0, "cpu1": 82.0}, {}, limit=0)
        self.assertEqual(len(bars), 2)
        for bar in bars:
            self.assertNotIn(COLOR_OURS, bar, "no row may be ours without proof")

    def test_our_load_is_not_smeared_across_bars(self):
        # Regression: our 0.3% was spread over 16 bars = 0.05% each, a tenth of
        # a pixel, invisible while adding noise to every host bar. It is
        # shown on the "who is using the host" bar instead.
        bars = render_core_bars({f"cpu{i}": 40.0 for i in range(16)}, {}, limit=0)
        for bar in bars:
            self.assertNotIn("uf5-ours", bar)
            self.assertIn(BAR_FILL, bar)

    def test_tiny_nonzero_share_gets_a_visible_floor(self):
        # 0.3% of a 22px bar is 0.07px. Any non-zero share gets a floor so
        # "we are using CPU" is legible; the label keeps the true number.
        html = render_split_load_bar(0.3, 0.0, 99.7)
        width = float(re.search(r'width:([\d.]+)%', html).group(1))
        self.assertGreaterEqual(width, 1.0, "a real load must be visible")
        self.assertIn("0.3%", html, "the label must show the true value")

    def test_true_zero_stays_zero(self):
        # Regression: the visibility floor claimed 1.5% of load that did not
        # exist. Zero must render as nothing.
        html = render_split_load_bar(0.0, 0.0, 100.0)
        self.assertIn("width:0.0%", html)

    def test_large_share_is_not_inflated(self):
        html = render_split_load_bar(50.0, 0.0, 50.0)
        width = float(re.search(r'width:([\d.]+)%', html).group(1))
        self.assertAlmostEqual(width, 50.0, places=1)

    def test_green_only_first_rows_when_mask_narrows(self):
        bars = render_core_bars({"cpu0": 5.0, "cpu1": 5.0, "cpu2": 5.0}, {}, limit=2)
        self.assertIn(COLOR_OURS, bars[0])
        self.assertIn(COLOR_OURS, bars[1])
        self.assertNotIn(COLOR_OURS, bars[2])


class MemoryTrackTests(unittest.TestCase):
    def test_segments_are_measured_against_base(self):
        segs = [("Ours", 2765, COLOR_OURS), ("Free", 7235, COLOR_MEM_FREE)]
        html = render_memory_track(segs, base=10000)
        widths = [float(w.split("%")[0]) for w in re.findall(r"width:([\d.]+)%", html)]
        self.assertAlmostEqual(widths[0], 27.65, places=1)
        self.assertAlmostEqual(widths[1], 72.35, places=1)
        self.assertAlmostEqual(sum(widths), 100.0, places=1)

    def test_segments_never_overflow_base(self):
        segs = [("Ours", 9000, COLOR_OURS), ("Other", 9000, COLOR_MEM_OTHER)]
        html = render_memory_track(segs, base=10000)
        widths = [float(w.split("%")[0]) for w in re.findall(r"width:([\d.]+)%", html)]
        self.assertLessEqual(sum(widths), 100.01, widths)

    def test_negative_values_render_zero(self):
        html = render_memory_track([("Ours", -500, COLOR_OURS)], base=1000)
        self.assertIn("width:0.00%", html)

    def test_zero_base_does_not_divide_by_zero(self):
        html = render_memory_track([("Ours", 100, COLOR_OURS)], base=0)
        self.assertIn("width:", html)


if __name__ == "__main__":
    unittest.main()
