import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.services import pool, store  # noqa: E402


class EnrichTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "worker.db"
        store.init(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cache_miss_then_hit(self):
        calls = []

        def fake_fetch(name, ip):
            calls.append((name, ip))
            return {"country_code": "DE", "provider_id": name}

        with patch.dict("os.environ", {"IPAPI_IS_KEY": "k"}, clear=False):
            geo, cached = pool.enrich_ip("8.8.8.8", self.db, fetch=fake_fetch)
        self.assertFalse(cached)
        self.assertEqual(geo["country_code"], "DE")
        self.assertEqual(len(calls), 1)
        # second call: cache hit, no provider call
        with patch.dict("os.environ", {"IPAPI_IS_KEY": "k"}, clear=False):
            geo2, cached2 = pool.enrich_ip("8.8.8.8", self.db, fetch=lambda n, i: (_ for _ in ()).throw(AssertionError("must not fetch")))
        self.assertTrue(cached2)
        self.assertEqual(geo2["country_code"], "DE")

    def test_no_providers_still_caches_bogon(self):
        with patch.dict("os.environ", {}, clear=False):
            for key in ("IPAPI_IS_KEY", "IPINFO_TOKEN", "IPQS_API_KEY", "ABUSEIPDB_API_KEY", "MAXMIND_CITY_DB", "MAXMIND_ASN_DB"):
                import os as _os
                _os.environ.pop(key, None)
            geo, cached = pool.enrich_ip("10.0.0.1", self.db)
        self.assertFalse(cached)
        self.assertTrue(geo["is_bogon"])

    def test_enrich_result_keeps_egress_fields(self):
        store.geo_put(self.db, "1.2.3.4", {"ip": "1.2.3.4", "country_code": "NL", "asn": 123})
        result = {"alive": True, "geo": {"ip": "1.2.3.4", "city": "Amsterdam"}}
        out = pool.enrich_result(result, self.db)
        self.assertEqual(out["geo"]["country_code"], "NL")
        self.assertEqual(out["geo"]["city"], "Amsterdam")
        self.assertTrue(out["geo"]["geo_cached"])

    def test_enrich_result_without_ip_is_noop(self):
        result = {"alive": True, "geo": {}}
        self.assertEqual(pool.enrich_result(result, self.db), result)

    def test_egress_ip_picks_first_known_key(self):
        self.assertEqual(pool.egress_ip({"resolved_ipv4": "5.5.5.5"}), "5.5.5.5")
        self.assertEqual(pool.egress_ip({}), "")

    def test_merge_prefers_first_and_keeps_scores(self):
        merged = pool.merge_geo([
            {"country_code": "US", "abuse_score": 0.9, "provider_id": "a"},
            {"country_code": "DE", "abuse_score": 0.1, "provider_id": "b"},
        ])
        self.assertEqual(merged["country_code"], "US")
        self.assertEqual(merged["abuse_score"], 0.9)
        self.assertEqual(merged["abuse_scores"], {"a": 0.9, "b": 0.1})


class RunCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "worker.db"
        store.init(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_start_error_short_circuits(self):
        with patch("lib.services.sidecar.worker_check_start", return_value={"error": "down"}):
            with patch("lib.services.sidecar.worker_check_poll") as poll:
                out = pool.run_check(["vless://x"], db_path=self.db)
        poll.assert_not_called()
        self.assertEqual(out["state"], "error")

    def test_full_run_enriches_and_persists(self):
        poll_state = {"n": 0}

        def fake_poll(run_id):
            poll_state["n"] += 1
            if poll_state["n"] < 2:
                return {"run_id": run_id, "state": "running", "results": []}
            return {"run_id": run_id, "state": "done", "results": [
                {"tag": "t1", "alive": True, "latency_ms": 50, "geo": {"ip": "9.9.9.9"}},
                {"tag": "t2", "alive": False, "geo": {}},
            ]}

        store.geo_put(self.db, "9.9.9.9", {"ip": "9.9.9.9", "country_code": "US"})
        with patch("lib.services.sidecar.worker_check_start", return_value={"run_id": "r1", "accepted": ["x"]}):
            with patch("lib.services.sidecar.worker_check_poll", side_effect=fake_poll):
                with patch.object(pool.time, "sleep", return_value=None):
                    out = pool.run_check(["x", "y"], {"parallel": 4, "max_accepted_lines": 60}, db_path=self.db)
        self.assertEqual(out["state"], "done")
        self.assertEqual(out["enriched"], 1)
        self.assertEqual(store.summary(self.db)["total"], 2)


if __name__ == "__main__":
    unittest.main()
