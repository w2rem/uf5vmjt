import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.services import limits  # noqa: E402


def _patch_files(cpu_max="", mem_max="", v1_quota="", v1_period="", v1_mem="", status=""):
    def fake_read(path):
        table = {
            limits.CGROUP_V2_CPU: cpu_max,
            limits.CGROUP_V2_MEM: mem_max,
            limits.CGROUP_V1_CPU_QUOTA: v1_quota,
            limits.CGROUP_V1_CPU_PERIOD: v1_period,
            limits.CGROUP_V1_MEM: v1_mem,
        }
        return table.get(path, "")

    def fake_status(path):
        if status:
            return status
        raise OSError("no status")

    return patch.multiple(limits, _read=fake_read), patch.object(limits, "_read", fake_read), patch(
        "builtins.open", fake_status
    )


class CpuQuotaTests(unittest.TestCase):
    def test_v2_quota_is_cores(self):
        # Reproduces `docker run --cpus=2`: cpu.max = "200000 100000".
        with patch.object(limits, "_read", side_effect=lambda p: "200000 100000" if p == limits.CGROUP_V2_CPU else ""):
            self.assertAlmostEqual(limits.cgroup_cpu_quota(), 2.0)

    def test_v2_fractional_quota(self):
        with patch.object(limits, "_read", side_effect=lambda p: "150000 100000" if p == limits.CGROUP_V2_CPU else ""):
            self.assertAlmostEqual(limits.cgroup_cpu_quota(), 1.5)

    def test_v2_max_means_unlimited(self):
        with patch.object(limits, "_read", side_effect=lambda p: "max 100000" if p == limits.CGROUP_V2_CPU else ""):
            self.assertIsNone(limits.cgroup_cpu_quota())

    def test_v1_quota_period(self):
        def fake(p):
            return {limits.CGROUP_V1_CPU_QUOTA: "400000", limits.CGROUP_V1_CPU_PERIOD: "100000"}.get(p, "")
        with patch.object(limits, "_read", side_effect=fake):
            self.assertAlmostEqual(limits.cgroup_cpu_quota(), 4.0)

    def test_v1_negative_quota_is_unlimited(self):
        def fake(p):
            return {limits.CGROUP_V1_CPU_QUOTA: "-1", limits.CGROUP_V1_CPU_PERIOD: "100000"}.get(p, "")
        with patch.object(limits, "_read", side_effect=fake):
            self.assertIsNone(limits.cgroup_cpu_quota())

    def test_garbage_quota_is_unlimited(self):
        with patch.object(limits, "_read", side_effect=lambda p: "abc def" if p == limits.CGROUP_V2_CPU else ""):
            self.assertIsNone(limits.cgroup_cpu_quota())


class AffinityTests(unittest.TestCase):
    def _affinity(self, raw):
        payload = f"Cpus_allowed_list:\t{raw}\n"
        import io

        def fake_open(path, *a, **k):
            if path == "/proc/self/status":
                return io.StringIO(payload)
            raise OSError(path)

        return patch("builtins.open", fake_open)

    def test_range_is_counted(self):
        with self._affinity("0-3"):
            self.assertEqual(limits.affinity_indices(), {0, 1, 2, 3})
            self.assertEqual(limits.affinity_cores(), 4)

    def test_list_and_mixed(self):
        with self._affinity("0,2-3,7"):
            self.assertEqual(limits.affinity_indices(), {0, 2, 3, 7})

    def test_unrestricted_is_empty(self):
        with self._affinity(""):
            self.assertEqual(limits.affinity_indices(), set())
            self.assertIsNone(limits.affinity_cores())

    def test_host_rows_are_filtered_to_our_cpus(self):
        host = ["cpu0", "cpu1", "cpu2", "cpu3", "cpu4", "cpu5", "cpu6", "cpu7"]
        with self._affinity("4-5"):
            self.assertEqual(limits.our_cpu_ids(host), ["cpu4", "cpu5"])

    def test_filter_is_noop_without_mask(self):
        host = ["cpu0", "cpu1"]
        with self._affinity(""):
            self.assertEqual(limits.our_cpu_ids(host), host)

    def test_filter_falls_back_when_mask_matches_nothing(self):
        host = ["cpu0", "cpu1"]
        with self._affinity("90-95"):
            self.assertEqual(limits.our_cpu_ids(host), host)


class MemoryLimitTests(unittest.TestCase):
    def test_v2_limit(self):
        # docker --memory=2700m => memory.max
        with patch.object(limits, "_read", side_effect=lambda p: "2831155200" if p == limits.CGROUP_V2_MEM else ""):
            self.assertEqual(limits.cgroup_memory_bytes(), 2831155200)

    def test_v2_max_is_unlimited(self):
        with patch.object(limits, "_read", side_effect=lambda p: "max" if p == limits.CGROUP_V2_MEM else ""):
            self.assertIsNone(limits.cgroup_memory_bytes())

    def test_v1_sentinel_near_two_pow_63_is_unlimited(self):
        with patch.object(limits, "_read", side_effect=lambda p: str(1 << 63) if p == limits.CGROUP_V1_MEM else ""):
            self.assertIsNone(limits.cgroup_memory_bytes())

    def test_effective_memory_reports_source(self):
        with patch.object(limits, "_read", side_effect=lambda p: "2831155200" if p == limits.CGROUP_V2_MEM else ""):
            self.assertEqual(limits.effective_memory_bytes(), (2831155200, "quota"))

    def test_effective_memory_host_fallback(self):
        with patch.object(limits, "_read", side_effect=lambda p: ""):
            self.assertEqual(limits.effective_memory_bytes()[1], "host")


class EffectiveCoresTests(unittest.TestCase):
    def test_quota_wins_over_affinity(self):
        with patch.object(limits, "_read", side_effect=lambda p: "200000 100000" if p == limits.CGROUP_V2_CPU else ""):
            with patch.object(limits, "affinity_cores", return_value=16):
                self.assertEqual(limits.effective_cores(), (2, "quota"))

    def test_fractional_quota_rounds_up(self):
        with patch.object(limits, "_read", side_effect=lambda p: "150000 100000" if p == limits.CGROUP_V2_CPU else ""):
            with patch.object(limits, "affinity_cores", return_value=16):
                self.assertEqual(limits.effective_cores(), (2, "quota"))

    def test_affinity_used_without_quota(self):
        with patch.object(limits, "_read", side_effect=lambda p: ""):
            with patch.object(limits, "affinity_cores", return_value=4):
                self.assertEqual(limits.effective_cores(), (4, "affinity"))

    def test_host_fallback(self):
        with patch.object(limits, "_read", side_effect=lambda p: ""):
            with patch.object(limits, "affinity_cores", return_value=None):
                self.assertEqual(limits.effective_cores()[1], "host")


if __name__ == "__main__":
    unittest.main()
