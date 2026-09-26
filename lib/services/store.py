"""Worker-local store: SQLite with proxy results, geo IP cache, runtime profile.

Hot storage is local SQLite (WAL). Postgres/Layerbase holds only cold
snapshots every 30 minutes; the snapshot uploader opens one short
connection per cycle and closes it immediately (session break), so the
cloud database never holds an idle worker connection.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(os.environ.get("WORKER_DB_PATH", Path(__file__).resolve().parents[2] / "data" / "worker.db"))
GEO_TTL_S = 30 * 24 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proxy_canonical(canonical_id TEXT PRIMARY KEY,host TEXT NOT NULL DEFAULT '',port INTEGER,first_seen_at INTEGER NOT NULL,last_seen_at INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'new');
CREATE TABLE IF NOT EXISTS proxy_checks(id INTEGER PRIMARY KEY AUTOINCREMENT,canonical_id TEXT NOT NULL,checked_at INTEGER NOT NULL,alive INTEGER NOT NULL,latency_ms INTEGER,geo_json TEXT,error_class TEXT NOT NULL DEFAULT '',error_message TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS idx_proxy_checks_lookup ON proxy_checks(canonical_id,checked_at DESC);
CREATE TABLE IF NOT EXISTS geo_cache(ip TEXT PRIMARY KEY,geo_json TEXT NOT NULL,fetched_at INTEGER NOT NULL,hit_count INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS runtime_cfg(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoint_state(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS activity(kind TEXT PRIMARY KEY,at INTEGER NOT NULL);
"""

_lock = threading.RLock()


def now() -> int:
    return int(time.time())


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init(path: Path | str = DB_PATH) -> None:
    with _lock:
        conn = connect(path)
        try:
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version','2')")
        finally:
            conn.close()


@contextmanager
def transaction(path: Path | str = DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def touch_activity(path: Path | str, kind: str = "check") -> None:
    with transaction(path) as conn:
        conn.execute(
            "INSERT INTO activity(kind,at) VALUES(?,?) ON CONFLICT(kind) DO UPDATE SET at=excluded.at",
            (kind, now()),
        )


def last_activity(path: Path | str, kind: str = "check") -> int:
    conn = connect(path)
    try:
        row = conn.execute("SELECT at FROM activity WHERE kind=?", (kind,)).fetchone()
        return int(row["at"]) if row else 0
    finally:
        conn.close()


def save_result(path: Path | str, canonical_id: str, host: str, result: dict[str, Any]) -> None:
    with transaction(path) as conn:
        conn.execute(
            """INSERT INTO proxy_canonical(canonical_id,host,first_seen_at,last_seen_at,state)
               VALUES(?,?,?,?,?) ON CONFLICT(canonical_id) DO UPDATE SET
               host=excluded.host,last_seen_at=excluded.last_seen_at,state='checked'""",
            (canonical_id, host, now(), now(), "checked"),
        )
        conn.execute(
            "INSERT INTO proxy_checks(canonical_id,checked_at,alive,latency_ms,geo_json,error_class,error_message) VALUES(?,?,?,?,?,?,?)",
            (canonical_id, now(), int(bool(result.get("alive"))), result.get("latency_ms"),
             json.dumps(result.get("geo") or {}, separators=(",", ":")), result.get("error_class", ""), result.get("error_message", "")),
        )
        conn.execute(
            "INSERT INTO activity(kind,at) VALUES('check',?) ON CONFLICT(kind) DO UPDATE SET at=excluded.at",
            (now(),),
        )


def geo_get(path: Path | str, ip: str, ttl_s: int = GEO_TTL_S) -> dict[str, Any] | None:
    conn = connect(path)
    try:
        row = conn.execute("SELECT geo_json,fetched_at FROM geo_cache WHERE ip=?", (ip,)).fetchone()
        if not row:
            return None
        if now() - int(row["fetched_at"]) > ttl_s:
            return None
        try:
            doc = json.loads(row["geo_json"])
        except ValueError:
            return None
        return doc if isinstance(doc, dict) else None
    finally:
        conn.close()


def geo_put(path: Path | str, ip: str, geo: dict[str, Any]) -> None:
    with transaction(path) as conn:
        conn.execute(
            """INSERT INTO geo_cache(ip,geo_json,fetched_at,hit_count) VALUES(?,?,?,1)
               ON CONFLICT(ip) DO UPDATE SET geo_json=excluded.geo_json,fetched_at=excluded.fetched_at,hit_count=geo_cache.hit_count+1""",
            (ip, json.dumps(geo or {}, separators=(",", ":")), now()),
        )


def geo_bump(path: Path | str, ip: str) -> None:
    with transaction(path) as conn:
        conn.execute("UPDATE geo_cache SET hit_count=hit_count+1 WHERE ip=?", (ip,))


def set_runtime(path: Path | str, cfg: dict[str, Any]) -> None:
    with transaction(path) as conn:
        conn.execute(
            "INSERT INTO runtime_cfg(key,value,updated_at) VALUES('profile',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (json.dumps(cfg, separators=(",", ":")), now()),
        )


def get_runtime(path: Path | str) -> dict[str, Any]:
    conn = connect(path)
    try:
        row = conn.execute("SELECT value FROM runtime_cfg WHERE key='profile'").fetchone()
        if not row:
            return {}
        try:
            doc = json.loads(row["value"])
        except ValueError:
            return {}
        return doc if isinstance(doc, dict) else {}
    finally:
        conn.close()


def checkpoint_due(path: Path | str, interval_s: int = 1800) -> bool:
    conn = connect(path)
    try:
        row = conn.execute("SELECT value FROM checkpoint_state WHERE key='last_snapshot_at'").fetchone()
        if not row:
            return True
        return now() - int(row["value"]) >= interval_s
    finally:
        conn.close()


def mark_checkpointed(path: Path | str) -> None:
    with transaction(path) as conn:
        conn.execute(
            "INSERT INTO checkpoint_state(key,value,updated_at) VALUES('last_snapshot_at',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (str(now()), now()),
        )


def export_snapshot(path: Path | str) -> dict[str, Any]:
    """Dump the tables a restart needs: results, geo cache, runtime profile."""
    conn = connect(path)
    try:
        tables: dict[str, list[dict[str, Any]]] = {}
        for table in ("proxy_canonical", "proxy_checks", "geo_cache", "runtime_cfg", "activity", "meta"):
            try:
                tables[table] = [dict(r) for r in conn.execute(f"SELECT * FROM {table} LIMIT 50000")]
            except sqlite3.OperationalError:
                tables[table] = []
        return {"schema_version": 2, "exported_at": now(), "tables": tables}
    finally:
        conn.close()


def import_snapshot(path: Path | str, snapshot: dict[str, Any]) -> dict[str, int]:
    """Restore a snapshot with a full rewrite per table. Returns row counts."""
    tables = snapshot.get("tables") or {}
    counts: dict[str, int] = {}
    with transaction(path) as conn:
        for table, rows in tables.items():
            if table not in {"proxy_canonical", "proxy_checks", "geo_cache", "runtime_cfg", "activity", "meta"}:
                continue
            if not isinstance(rows, list) or not rows:
                counts[table] = 0
                continue
            cols = list(rows[0].keys())
            conn.execute(f"DELETE FROM {table}")
            placeholders = ",".join("?" for _ in cols)
            conn.executemany(
                f"INSERT OR REPLACE INTO {table}({','.join(cols)}) VALUES({placeholders})",
                [[r.get(c) for c in cols] for r in rows if isinstance(r, dict)],
            )
            counts[table] = len(rows)
    return counts


def summary(path: Path | str) -> dict[str, Any]:
    conn = connect(path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total, SUM(alive) AS alive FROM proxy_checks WHERE id IN (SELECT MAX(id) FROM proxy_checks GROUP BY canonical_id)"
        ).fetchone()
        geo = conn.execute("SELECT COUNT(*) AS n, SUM(hit_count) AS hits FROM geo_cache").fetchone()
        return {
            "total": int(row["total"] or 0),
            "alive": int(row["alive"] or 0),
            "geo_cached_ips": int(geo["n"] or 0),
            "geo_cache_hits": int(geo["hits"] or 0),
        }
    finally:
        conn.close()
