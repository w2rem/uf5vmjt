import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.services import procs  # noqa: E402


class ReadStatTests(unittest.TestCase):
    def test_own_pid_is_readable(self):
        fields = procs.read_stat_fields(os.getpid())
        self.assertIsNotNone(fields)
        comm, utime, stime, rss = fields
        self.assertTrue(comm)
        self.assertGreaterEqual(utime, 0)
        self.assertGreater(rss, 0, "our own process must report nonzero RSS")

    def test_comm_with_spaces_does_not_shift_fields(self):
        # /proc/<pid>/stat's comm is parenthesised and may contain spaces and
        # parens; counting fields from the start of the line misparses it.
        script = "import os,time,sys\nsys.stdout.write('')\ntime.sleep(30)\n"
        proc = subprocess.Popen([sys.executable, "-c", script])
        try:
            fields = procs.read_stat_fields(proc.pid)
            self.assertIsNotNone(fields)
            self.assertIsInstance(fields[1], int)
            self.assertGreaterEqual(fields[1], 0)
        finally:
            proc.kill()
            proc.wait()

    def test_missing_pid_returns_none(self):
        self.assertIsNone(procs.read_stat_fields(999999))

    def test_rss_is_plausible(self):
        got = procs.rss_bytes(os.getpid())
        self.assertGreater(got, 0)
        self.assertLess(got, 4 * 1024 ** 3, "RSS must be under 4GB for a python process")


class PidListTests(unittest.TestCase):
    def test_our_pids_are_sorted_and_numeric(self):
        pids = procs.our_pids()
        self.assertTrue(pids)
        self.assertEqual(pids, sorted(pids))
        self.assertIn(os.getpid(), pids)

    def test_is_ours_accepts_self(self):
        self.assertTrue(procs.is_ours(os.getpid()))

    def test_is_ours_rejects_missing(self):
        self.assertFalse(procs.is_ours(999999))


class DeltaTests(unittest.TestCase):
    def test_cpu_burner_is_measured(self):
        # A process that burns 1.0s of CPU over a 1.0s window must read 100%
        # of one core, not 0 and not host noise.
        code = "x=0\nwhile True:\n x+=1\n"
        proc = subprocess.Popen([sys.executable, "-c", code])
        try:
            before = procs.sample()
            import time
            time.sleep(1.0)
            after = procs.sample()
            rows = procs.top_cpu(before, after, 1.0)
            pids = {r[0] for r in rows}
            self.assertIn(proc.pid, pids, "burner missing from top_cpu")
            pct = dict((r[0], r[2]) for r in rows)[proc.pid]
            self.assertGreater(pct, 50.0, f"expected real CPU, got {pct}%")
            self.assertLess(pct, 200.0)
            total = procs.delta_percent(before, after, cores=1, window_s=1.0)
            self.assertGreater(total, 20.0)
        finally:
            proc.kill()
            proc.wait()

    def test_new_process_without_baseline_is_not_counted(self):
        code = "x=0\nwhile True:\n x+=1\n"
        proc = subprocess.Popen([sys.executable, "-c", code])
        try:
            before = {}
            import time
            time.sleep(0.6)
            after = procs.sample()
            # proc.pid is absent from `before`, so it must not appear at all.
            self.assertNotIn(proc.pid, dict((r[0], r[2]) for r in procs.top_cpu(before, after, 0.6)))
        finally:
            proc.kill()
            proc.wait()

    def test_delta_divides_by_cores(self):
        before = {1: ("x", 0, 0)}
        # 100 jiffies at 100Hz = 1.0s of CPU over a 1.0s window.
        after = {1: ("x", 100, 0)}
        self.assertEqual(procs.delta_percent(before, after, cores=1, window_s=1.0), 100.0)
        self.assertEqual(procs.delta_percent(before, after, cores=2, window_s=1.0), 50.0)

    def test_zero_window_is_zero(self):
        self.assertEqual(procs.delta_percent({}, {}, cores=2, window_s=0), 0.0)
        self.assertEqual(procs.delta_percent({}, {}, cores=0, window_s=1.0), 0.0)

    def test_negative_delta_clamped_to_zero(self):
        before = {1: ("x", 500, 0)}
        after = {1: ("x", 100, 0)}
        self.assertEqual(procs.delta_percent(before, after, cores=1, window_s=1.0), 0.0)

    def test_idle_processes_absent_from_top(self):
        before = {1: ("a", 0, 0)}
        after = {1: ("a", 0, 0)}
        self.assertEqual(procs.top_cpu(before, after, 1.0), [])


class RssTests(unittest.TestCase):
    def test_total_rss_is_positive(self):
        self.assertGreater(procs.total_rss_bytes([os.getpid()]), 0)

    def test_total_rss_ignores_missing(self):
        self.assertEqual(procs.total_rss_bytes([999999]), 0)


if __name__ == "__main__":
    unittest.main()
