import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.services import store  # noqa: E402


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "worker.db"
        store.init(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_summary(self):
        store.save_result(self.db, "tag-1", "1.2.3.4", {"alive": True, "latency_ms": 120, "geo": {"ip": "1.2.3.4"}})
        store.save_result(self.db, "tag-2", "5.6.7.8", {"alive": False, "error_class": "dead"})
        s = store.summary(self.db)
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["alive"], 1)

    def test_geo_cache_hit_and_ttl(self):
        self.assertIsNone(store.geo_get(self.db, "9.9.9.9"))
        store.geo_put(self.db, "9.9.9.9", {"ip": "9.9.9.9", "country_code": "US"})
        got = store.geo_get(self.db, "9.9.9.9")
        self.assertEqual(got["country_code"], "US")
        # expired entry reads as miss
        self.assertIsNone(store.geo_get(self.db, "9.9.9.9", ttl_s=-1))
        # bump keeps the row
        store.geo_bump(self.db, "9.9.9.9")
        self.assertIsNotNone(store.geo_get(self.db, "9.9.9.9", ttl_s=10**9))

    def test_runtime_roundtrip(self):
        self.assertEqual(store.get_runtime(self.db), {})
        store.set_runtime(self.db, {"profile": "quiet", "parallel": 4})
        self.assertEqual(store.get_runtime(self.db)["parallel"], 4)

    def test_checkpoint_due_and_mark(self):
        self.assertTrue(store.checkpoint_due(self.db, 1800))
        store.mark_checkpointed(self.db)
        self.assertFalse(store.checkpoint_due(self.db, 1800))

    def test_export_import_roundtrip(self):
        store.save_result(self.db, "t1", "h", {"alive": True})
        store.geo_put(self.db, "1.1.1.1", {"ip": "1.1.1.1"})
        snap = store.export_snapshot(self.db)
        self.assertIn("proxy_checks", snap["tables"])
        other = Path(self.tmp.name) / "other.db"
        store.init(other)
        counts = store.import_snapshot(other, snap)
        self.assertGreater(counts.get("proxy_checks", 0), 0)
        self.assertEqual(store.summary(other)["total"], 1)

    def test_import_ignores_unknown_tables(self):
        counts = store.import_snapshot(self.db, {"tables": {"evil": [{"a": 1}]}})
        self.assertEqual(counts, {})


if __name__ == "__main__":
    unittest.main()
