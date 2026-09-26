"""30-minute snapshot loop with a session break and a traffic gate.

Every SNAPSHOT_MIN minutes (default 30) the worker exports its SQLite
snapshot and uploads it to Postgres in one short connection, then closes
it immediately — never a held connection. When the worker has been idle
(no check activity for IDLE_SKIP_S, default 2h) the cycle is skipped, so
an idle fleet costs zero database minutes. Failures back off without
killing the host process.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from . import store

SNAPSHOT_MIN_DEFAULT = 30
IDLE_SKIP_S = 2 * 3600
FAIL_BACKOFF_S = 300

_thread: threading.Thread | None = None
_stop = threading.Event()


def snapshot_interval_s() -> int:
    try:
        minutes = int(os.environ.get("WORKER_SNAPSHOT_MIN", str(SNAPSHOT_MIN_DEFAULT)))
    except ValueError:
        minutes = SNAPSHOT_MIN_DEFAULT
    return max(300, min(720 * 60, minutes * 60))


def should_snapshot(db_path: Path | str | None = None, idle_skip_s: int = IDLE_SKIP_S) -> tuple[bool, str]:
    path = Path(db_path) if db_path else store.DB_PATH
    try:
        if not store.checkpoint_due(path, snapshot_interval_s()):
            return False, "not due"
    except Exception as exc:
        return False, f"checkpoint state unreadable: {type(exc).__name__}"
    try:
        last = store.last_activity(path)
    except Exception:
        last = 0
    if last and time.time() - last > idle_skip_s:
        return False, "idle: no check activity"
    return True, "due"


def snapshot_once(db_path: Path | str | None = None) -> dict[str, Any]:
    """Export + upload one snapshot. Returns a status dict, never raises."""
    path = Path(db_path) if db_path else store.DB_PATH
    ok, reason = should_snapshot(path)
    if not ok:
        return {"state": "skipped", "reason": reason}
    try:
        snapshot = store.export_snapshot(path)
    except Exception as exc:
        return {"state": "error", "reason": f"export: {type(exc).__name__}"}
    dsn = os.environ.get("LAYERBASE_DATABASE_URL", "").strip()
    if not dsn:
        return {"state": "skipped", "reason": "LAYERBASE_DATABASE_URL is not set"}
    try:
        uploaded = _upload_snapshot(dsn, snapshot)
    except Exception as exc:
        return {"state": "error", "reason": f"upload: {type(exc).__name__} {str(exc)[:100]}"}
    try:
        store.mark_checkpointed(path)
    except Exception:
        pass
    return {"state": "ok", "tables": uploaded}


def _upload_snapshot(dsn: str, snapshot: dict[str, Any]) -> dict[str, int]:
    """One short Postgres session: connect, upsert, close. Session break."""
    try:
        import psycopg  # type: ignore
    except ImportError as exc:
        raise RuntimeError("psycopg is not installed") from exc
    import json

    tables = snapshot.get("tables") or {}
    counts: dict[str, int] = {}
    conn = psycopg.connect(dsn, connect_timeout=10, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """CREATE TABLE IF NOT EXISTS worker_snapshots(
                   worker_id TEXT NOT NULL, taken_at BIGINT NOT NULL,
                   table_name TEXT NOT NULL, rows_json JSONB NOT NULL,
                   PRIMARY KEY(worker_id, table_name))"""
            )
            worker_id = os.environ.get("WORKER_ID", "worker")
            taken_at = int(time.time())
            for table, rows in tables.items():
                cur.execute(
                    """INSERT INTO worker_snapshots(worker_id,taken_at,table_name,rows_json)
                       VALUES(%s,%s,%s,%s) ON CONFLICT(worker_id,table_name) DO UPDATE SET
                       taken_at=excluded.taken_at, rows_json=excluded.rows_json""",
                    (worker_id, taken_at, table, json.dumps(rows or [])),
                )
                counts[table] = len(rows or [])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return counts


def restore_if_needed(db_path: Path | str | None = None) -> dict[str, Any]:
    """Restore the worker's own snapshot row set on boot when local DB is empty."""
    path = Path(db_path) if db_path else store.DB_PATH
    if path.exists() and path.stat().st_size > 0:
        return {"state": "skipped", "reason": "local db present"}
    dsn = os.environ.get("LAYERBASE_DATABASE_URL", "").strip()
    if not dsn:
        return {"state": "skipped", "reason": "LAYERBASE_DATABASE_URL is not set"}
    try:
        import psycopg  # type: ignore
    except ImportError:
        return {"state": "skipped", "reason": "psycopg is not installed"}
    worker_id = os.environ.get("WORKER_ID", "worker")
    conn = psycopg.connect(dsn, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT table_name, rows_json FROM worker_snapshots WHERE worker_id=%s", (worker_id,))
            tables = {name: (payload if isinstance(payload, list) else []) for name, payload in cur.fetchall()}
    finally:
        conn.close()
    if not tables:
        return {"state": "skipped", "reason": "no snapshot for worker"}
    counts = store.import_snapshot(path, {"tables": tables})
    return {"state": "ok", "tables": counts}


def _loop() -> None:
    interval = snapshot_interval_s()
    while not _stop.wait(interval):
        try:
            snapshot_once()
        except Exception:
            pass
        try:
            interval = snapshot_interval_s()
        except Exception:
            pass


def start() -> bool:
    """Start the snapshot thread once per process. Returns True if started."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="worker-snapshot", daemon=True)
    _thread.start()
    return True
