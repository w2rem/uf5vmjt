import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.services import snap, store  # noqa: E402


class SnapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "worker.db"
        store.init(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_skipped_without_dsn(self):
        with patch.dict("os.environ", {"LAYERBASE_DATABASE_URL": ""}):
            out = snap.snapshot_once(self.db)
        self.assertEqual(out["state"], "skipped")

    def test_not_due_after_mark(self):
        store.mark_checkpointed(self.db)
        ok, reason = snap.should_snapshot(self.db)
        self.assertFalse(ok)
        self.assertEqual(reason, "not due")

    def test_idle_gate_skips(self):
        import time
        import os as _os
        with patch.dict("os.environ", {"LAYERBASE_DATABASE_URL": "postgres://x"}, clear=False):
            store.touch_activity(self.db, "check")
            # fake old activity by writing directly
            import sqlite3
            conn = sqlite3.connect(self.db)
            conn.execute("UPDATE activity SET at=? WHERE kind='check'", (int(time.time()) - 10 * 3600,))
            conn.commit()
            conn.close()
            ok, reason = snap.should_snapshot(self.db, idle_skip_s=3600)
        self.assertFalse(ok)
        self.assertIn("idle", reason)

    def test_upload_error_is_status_not_raise(self):
        with patch.dict("os.environ", {"LAYERBASE_DATABASE_URL": "postgres://x"}, clear=False):
            with patch.object(snap, "_upload_snapshot", side_effect=RuntimeError("db down")):
                out = snap.snapshot_once(self.db)
        self.assertEqual(out["state"], "error")

    def test_restore_skipped_when_local_present(self):
        store.save_result(self.db, "t", "h", {"alive": True})
        out = snap.restore_if_needed(self.db)
        self.assertEqual(out["state"], "skipped")

    def test_start_is_idempotent(self):
        self.assertTrue(snap.start())
        self.assertFalse(snap.start())
        snap._stop.set()


if __name__ == "__main__":
    unittest.main()
