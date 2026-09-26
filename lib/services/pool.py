"""Pool runner: check proxies through the Go sidecar, enrich geo with an IP cache.

Flow per result: egress geo arrives inside the Go verdict (measured through
the proxy itself). The worker then looks the egress IP up in the local
geo_cache (30-day TTL): same server with a different port costs zero
provider calls. Only a cache miss fans out to the keyed providers and the
local MaxMind files; the merged record is cached before persist.

No streamlit import at module top: this module must stay importable from
plain unit tests.
"""
from __future__ import annotations

import ipaddress
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import store

GEO_TTL_S = 30 * 24 * 3600
PROVIDER_TIMEOUT_S = 6
PROVIDER_BODY_LIMIT = 32768

FIELD_MAPS: dict[str, dict[str, Any]] = {
    "ipapi.is": {
        "url": "https://api.ipapi.is/?ip={ip}",
        "key_env": "IPAPI_IS_KEY",
        "score_scale": 1.0,
        "map": {
            "country_code": "location.country_code", "city": "location.city",
            "asn": "asn.asn", "org": "asn.org", "is_proxy": "is_proxy",
            "is_vpn": "is_vpn", "is_tor": "is_tor", "is_hosting": "is_datacenter",
            "is_mobile": "is_mobile", "abuse_score": "company.abuser_score",
            "lat": "location.latitude", "lon": "location.longitude",
        },
    },
    "ipqualityscore": {
        "url": "https://ipqualityscore.com/api/json/ip/{ip}?key={key}",
        "key_env": "IPQS_API_KEY",
        "score_scale": 100.0,
        "map": {
            "country_code": "country_code", "city": "city", "asn": "asn",
            "org": "organization", "is_proxy": "proxy", "is_vpn": "vpn",
            "is_tor": "tor", "is_abuser": "recent_abuse",
            "abuse_score": "fraud_score", "lat": "latitude", "lon": "longitude",
        },
    },
    "abuseipdb": {
        "url": "https://api.abuseipdb.com/api/v2/check?ipAddress={ip}",
        "key_env": "ABUSEIPDB_API_KEY",
        "score_scale": 100.0,
        "headers": {"Accept": "application/json"},
        "map": {"is_tor": "isTor", "abuse_score": "abuseConfidenceScore", "org": "isp"},
    },
}


def _get_path(doc: Any, path: str) -> Any:
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def normalize_payload(payload: dict[str, Any], field_map: dict[str, str], scale: float = 1.0) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field, paths in field_map.items():
        for path in paths.split("|"):
            value = _get_path(payload, path.strip())
            if value is not None and value != "":
                out[field] = value
                break
    score = out.get("abuse_score")
    if isinstance(score, (int, float)) and scale and scale != 1.0:
        out["abuse_score"] = round(float(score) / float(scale), 4)
    return out


def fetch_provider(name: str, ip: str, timeout: float = PROVIDER_TIMEOUT_S) -> dict[str, Any]:
    spec = FIELD_MAPS[name]
    key = os.environ.get(spec["key_env"], "").strip()
    if not key:
        raise RuntimeError(f"{name}: {spec['key_env']} is not set")
    url = spec["url"].replace("{ip}", urllib.parse.quote(ip, safe="")).replace("{key}", key)
    headers = {"User-Agent": "libertius-geoip/0.1", "Accept": "application/json"}
    headers.update(spec.get("headers") or {})
    if name == "abuseipdb":
        headers["Key"] = key
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw = resp.read(PROVIDER_BODY_LIMIT + 1)
    if len(raw) > PROVIDER_BODY_LIMIT:
        raise ValueError(f"{name}: response exceeds 32 KiB")
    payload = json.loads(raw.decode("utf-8", "replace"))
    if not isinstance(payload, dict):
        raise ValueError(f"{name}: response is not an object")
    out = normalize_payload(payload, spec["map"], spec.get("score_scale", 1.0))
    out["provider_id"] = name
    return out


def configured_providers() -> list[str]:
    return [name for name, spec in FIELD_MAPS.items() if os.environ.get(spec["key_env"], "").strip()]


def maxmind_lookup(ip: str) -> dict[str, Any]:
    """Local MMDB enrichment; always reports is_bogon, MMDB files optional."""
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return {}
    out: dict[str, Any] = {"is_bogon": not parsed.is_global}
    city_path = os.environ.get("MAXMIND_CITY_DB", "").strip()
    asn_path = os.environ.get("MAXMIND_ASN_DB", "").strip()
    if not (city_path or asn_path):
        return out
    try:
        import maxminddb  # type: ignore
    except ImportError:
        out["maxmind_error"] = "maxminddb not installed"
        return out
    if city_path and Path(city_path).is_file():
        try:
            with maxminddb.open_database(city_path) as reader:
                data = reader.get(ip) or {}
            country = data.get("country") or {}
            out["country_code"] = (country.get("iso_code") or "").upper() or None
            out["city"] = (data.get("city") or {}).get("names", {}).get("en")
        except Exception as exc:
            out["maxmind_city_error"] = type(exc).__name__
    if asn_path and Path(asn_path).is_file():
        try:
            with maxminddb.open_database(asn_path) as reader:
                data = reader.get(ip) or {}
            out["asn"] = data.get("autonomous_system_number")
            out["org"] = data.get("autonomous_system_organization")
        except Exception as exc:
            out["maxmind_asn_error"] = type(exc).__name__
    return out


def merge_geo(records: list[dict[str, Any]]) -> dict[str, Any]:
    """First non-empty value wins per field; all abuse scores are kept."""
    merged: dict[str, Any] = {}
    scores: dict[str, Any] = {}
    for rec in records:
        for key, value in rec.items():
            if value is None or value == "":
                continue
            if key == "abuse_score":
                scores[str(rec.get("provider_id", "unknown"))] = value
                if "abuse_score" not in merged:
                    merged["abuse_score"] = value
                continue
            if key not in merged:
                merged[key] = value
    if scores:
        merged["abuse_scores"] = scores
    return merged


def enrich_ip(ip: str, db_path: Path | str | None = None,
              fetch: Callable[[str, str], dict[str, Any]] | None = None) -> tuple[dict[str, Any], bool]:
    """Return (geo, from_cache). Cache hit bumps hit_count and skips providers."""
    path = Path(db_path) if db_path else store.DB_PATH
    cached = store.geo_get(path, ip, GEO_TTL_S)
    if cached is not None:
        store.geo_bump(path, ip)
        return cached, True
    do_fetch = fetch or (lambda name, addr: fetch_provider(name, addr))
    records = [maxmind_lookup(ip)]
    errors: list[dict[str, str]] = []
    for name in configured_providers():
        try:
            records.append(do_fetch(name, ip))
        except Exception as exc:
            errors.append({"provider": name, "error": type(exc).__name__})
    merged = merge_geo([r for r in records if r])
    merged["ip"] = ip
    if errors:
        merged["errors"] = errors
    store.geo_put(path, ip, merged)
    return merged, False


def tunecfg_profile(db_path: Path | str | None = None) -> dict[str, Any]:
    """Read the panel-pushed profile from the Go tunecfg file, if present.

    The panel POSTs to Go /v1/runtime; Go persists runtime.json under
    TUNECFG_DIR. Python reads the same file so checks run with the profile
    the panel assigned, surviving restarts. Falls back to {} (pool defaults).
    """
    root = os.environ.get("TUNECFG_DIR", "/tmp/bin/tunecfg").strip() or "/tmp/bin/tunecfg"
    try:
        raw = Path(root, "runtime.json").read_text()
    except OSError:
        return dict(store.get_runtime(db_path) if db_path else {})
    try:
        doc = json.loads(raw)
    except ValueError:
        return {}
    if isinstance(doc, dict):
        try:
            if db_path:
                store.set_runtime(db_path, doc)
        except Exception:
            pass
        return doc
    return {}


def egress_ip(geo: dict[str, Any]) -> str:
    for key in ("ip", "resolved_ipv4", "resolved_ipv6", "query"):
        value = (geo or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def enrich_result(result: dict[str, Any], db_path: Path | str | None = None) -> dict[str, Any]:
    """Enrich one Go check result in place; returns it for chaining."""
    geo = dict(result.get("geo") or {})
    ip = egress_ip(geo)
    if not ip:
        return result
    try:
        enriched, from_cache = enrich_ip(ip, db_path)
    except Exception:
        return result
    for key, value in enriched.items():
        if value is not None and geo.get(key) is None:
            geo[key] = value
    geo["geo_cached"] = from_cache
    result["geo"] = geo
    return result


def run_check(lines: list[str], profile: dict[str, Any] | None = None,
              db_path: Path | str | None = None, timeout_s: float = 120.0) -> dict[str, Any]:
    """POST /v1/check via the sidecar helpers, poll to done, enrich + persist.

    Profile keys mirror panel tuning: parallel, geo_parallel, delay_ms (as
    timeout_ms), max_accepted_lines, dns. Returns the Go snapshot with an
    added `enriched` count.
    """
    from . import sidecar

    cfg = dict(profile or {})
    parallel = int(cfg.get("parallel", 8))
    geo_parallel = int(cfg.get("geo_parallel", 4))
    timeout_ms = int(cfg.get("delay_ms", cfg.get("timeout_ms", 10000)))
    max_lines = int(cfg.get("max_accepted_lines", 200))
    payload_lines = list(lines)[:max_lines]
    start = sidecar.worker_check_start(
        payload_lines, timeout_ms=timeout_ms, parallel=parallel,
        geo_parallel=geo_parallel, dns=str(cfg.get("dns", "auto")),
    )
    if start.get("error") or not start.get("run_id"):
        return {"state": "error", "detail": start.get("error", "start failed"), "results": [], "enriched": 0}
    run_id = str(start["run_id"])
    deadline = time.monotonic() + timeout_s + timeout_ms / 1000.0
    snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snapshot = sidecar.worker_check_poll(run_id)
        if not snapshot:
            time.sleep(1.0)
            continue
        if snapshot.get("gone"):
            return {"state": "error", "detail": "worker forgot the run (restart/expiry)", "results": [], "enriched": 0}
        if snapshot.get("state") in {"done", "error"}:
            break
        time.sleep(2.0)
    else:
        return {"state": "error", "detail": "check poll timeout", "results": [], "enriched": 0}
    path = Path(db_path) if db_path else store.DB_PATH
    store.init(path)
    enriched = 0
    for item in snapshot.get("results", []):
        if isinstance(item, dict) and item.get("alive"):
            enrich_result(item, path)
            enriched += 1
        tag = str((item or {}).get("tag", ""))
        host = str(((item or {}).get("geo") or {}).get("ip", ""))
        if tag:
            try:
                store.save_result(path, tag, host, item if isinstance(item, dict) else {})
            except Exception:
                pass
    try:
        store.touch_activity(path, "check")
    except Exception:
        pass
    snapshot["enriched"] = enriched
    return snapshot
