"""uf5vmjt.lib.services.sidecar — Go worker sidecar lifecycle + pg/vlk verdicts."""
from __future__ import annotations
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from lib.core.config import GO_LOG_PATH, WORKER_BIN_ENV, WORKER_ENV_KEYS, WORKER_PORT
from lib.core.events import log_event
from lib.services.netinfo import fetch_json


WORKER_GRACE_SEC = 30


def _in_worker_grace() -> bool:
    """True within WORKER_GRACE_SEC of spawn: verdicts are retried silently.

    The versions row resolves before the sidecar finishes booting (binary
    start → first tick takes seconds); failure notes during the window are
    red herrings, so callers skip them while this is true.
    """
    import streamlit as st

    started = st.session_state.get("uf5_worker_started_at", 0) or 0
    try:
        return (time.time() - float(started)) < WORKER_GRACE_SEC
    except (TypeError, ValueError):
        return False



def seed_worker_env_from_secrets() -> None:
    """Copy WORKER_ENV_KEYS from st.secrets into os.environ when unset.

    Cloud deploys are secrets-only (no shell env); the sidecar inherits
    os.environ, so without this it would never see PG_DATABASE_URL or the
    session token. Key names (never values) go to the event log.
    Never raises.
    """
    import streamlit as st

    try:
        secrets = st.secrets
    except Exception:  # noqa: BLE001
        return
    for key in WORKER_ENV_KEYS:
        if os.environ.get(key):
            continue
        try:
            value = secrets.get(key, "")
        except Exception:  # noqa: BLE001
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        os.environ[key] = str(value).strip() if isinstance(value, str) else str(value)
        log_event("debug", f"secret {key} applied to worker env", source="go")


def find_worker_binary() -> str:
    """Locate the worker binary: env override, ./worker (launch dir), or next
    to main.py. Empty when nothing is shipped."""
    override = (os.environ.get(WORKER_BIN_ENV) or "").strip()
    if override and os.path.isfile(override):
        return override
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(os.getcwd(), "worker"),
                 os.path.join(here, "worker")):
        if os.path.isfile(cand):
            return cand
    return ""


def worker_alive() -> bool:
    """True when our child is running or something answers /health on port."""
    proc = _WORKER_PROC
    try:
        if proc is not None and proc.poll() is None:
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{WORKER_PORT}/health",
            headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=1) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


GO_LOG_MAX_BYTES = 5 * 1024 * 1024
GO_LOG_KEEP_BYTES = 1 * 1024 * 1024


def _rotate_go_log(path: str) -> None:
    """Keep the Go log bounded: over GO_LOG_MAX_BYTES, retain the last
    GO_LOG_KEEP_BYTES. The pump thread calls this once at startup."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size <= GO_LOG_MAX_BYTES:
        return
    try:
        with open(path, "rb") as f:
            f.seek(max(0, size - GO_LOG_KEEP_BYTES))
            tail = f.read().split(b"\n", 1)
            keep = tail[-1] if len(tail) > 1 else tail[0]
        with open(path, "wb") as f:
            f.write(keep)
    except OSError:
        pass


def _pump_worker_logs(proc, path: str) -> None:
    """Forward child stdout lines to the Go JSON-lines log (daemon thread).

    Never touches session_state (wrong context here) — ingest_go_events()
    tails the file back into the event stream on the script thread.
    """
    _rotate_go_log(path)
    try:
        assert proc.stdout is not None
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            for line in proc.stdout:
                text = line.strip()
                if not text:
                    continue
                low = text.lower()
                level = ("error" if "error" in low or "fatal" in low
                         else "warn" if "warn" in low else "info")
                try:
                    f.write(json.dumps({"level": level, "msg": text[:500]}) + "\n")
                    f.flush()
                except OSError:
                    break
    except Exception:  # noqa: BLE001
        pass


def ensure_worker() -> None:
    """Spawn ./worker once per Python process; no-op when already up.

    Child inherits APP_URL (set earlier in main) and gets its own PORT so it
    never fights the hosting port. Never raises — failures become log lines.
    """
    import streamlit as st

    global _WORKER_PROC
    try:
        if worker_alive():
            return
        if st.session_state.get("uf5_worker_started"):
            return
        binary = find_worker_binary()
        if not binary:
            if not st.session_state.get("uf5_worker_missing_logged"):
                st.session_state["uf5_worker_missing_logged"] = True
                log_event("debug", "no ./worker next to launch dir — sidecar off",
                          source="go")
            return
        try:
            if not os.access(binary, os.X_OK):
                os.chmod(binary, 0o755)
        except OSError:
            pass
        env = dict(os.environ)
        env["PORT"] = str(WORKER_PORT)
        try:
            import threading

            proc = subprocess.Popen(
                [binary], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=os.path.dirname(binary) or None,
                env=env,
            )
        except Exception as e:  # noqa: BLE001
            log_event("warn", f"worker spawn failed: {e}", source="go")
            return
        _WORKER_PROC = proc
        thread = threading.Thread(target=_pump_worker_logs,
                                  args=(proc, GO_LOG_PATH), daemon=True)
        thread.start()
        st.session_state["uf5_worker_started"] = True
        st.session_state["uf5_worker_started_at"] = time.time()
        log_event("ok", f"worker started: {binary} on :{WORKER_PORT} "
                        f"app={os.environ.get('APP_URL', '?')} "
                        f"every={os.environ.get('UP_EVERY', '?')} min",
                  source="go")
    except Exception as e:  # noqa: BLE001
        try:
            log_event("warn", f"worker ensure failed: {e}", source="go")
        except Exception:  # noqa: BLE001
            pass


def worker_pg_verdict() -> dict:
    """Cached /v1/pg verdict from the local sidecar. Empty when unreachable.

    The failure reason lands in uf5_pg_note (shown under the versions row),
    so 'unknown' is always diagnosable: old worker, unconfigured pg, or a
    sidecar that is not up (yet).
    """
    import streamlit as st

    cached = st.session_state.get("uf5_pg_verdict")
    if isinstance(cached, dict) and cached:
        return cached
    url = f"http://127.0.0.1:{WORKER_PORT}/v1/pg"
    try:
        data = fetch_json(url, timeout=3)
    except urllib.error.HTTPError as e:
        detail = f"sidecar http {e.code}"
        try:
            body = json.loads(e.read().decode(errors="replace") or "{}")
            if isinstance(body, dict) and body.get("error"):
                detail += f": {body['error']}"
        except (ValueError, OSError):
            pass
        if e.code == 404:
            detail += " (old worker — redeploy for /v1/pg)"
        if not _in_worker_grace():
            st.session_state["uf5_pg_note"] = detail
        return {}
    except Exception:  # noqa: BLE001
        if not _in_worker_grace():
            st.session_state["uf5_pg_note"] = f"sidecar unreachable on :{WORKER_PORT}"
        return {}
    if isinstance(data, dict) and data.get("version"):
        st.session_state["uf5_pg_verdict"] = data
        st.session_state.pop("uf5_pg_note", None)
        return data
    st.session_state["uf5_pg_note"] = "empty verdict"
    return {}


def worker_vlk_verdict() -> dict:
    """Cached /v1/vlk verdict from the local sidecar. Empty when unreachable.

    Mirrors worker_pg_verdict: the failure reason lands in uf5_vlk_note
    (shown under the versions row), so 'unknown' is always diagnosable.
    """
    import streamlit as st

    cached = st.session_state.get("uf5_vlk_verdict")
    if isinstance(cached, dict) and cached:
        return cached
    url = f"http://127.0.0.1:{WORKER_PORT}/v1/vlk"
    try:
        data = fetch_json(url, timeout=3)
    except urllib.error.HTTPError as e:
        detail = f"sidecar http {e.code}"
        try:
            body = json.loads(e.read().decode(errors="replace") or "{}")
            if isinstance(body, dict) and body.get("error"):
                detail += f": {body['error']}"
        except (ValueError, OSError):
            pass
        if e.code == 404:
            detail += " (old worker — redeploy for /v1/vlk)"
        if not _in_worker_grace():
            st.session_state["uf5_vlk_note"] = detail
        return {}
    except Exception:  # noqa: BLE001
        if not _in_worker_grace():
            st.session_state["uf5_vlk_note"] = f"sidecar unreachable on :{WORKER_PORT}"
        return {}
    if isinstance(data, dict) and data.get("version"):
        st.session_state["uf5_vlk_verdict"] = data
        st.session_state.pop("uf5_vlk_note", None)
        return data
    st.session_state["uf5_vlk_note"] = "empty verdict"
    return {}


def worker_singbox_status() -> dict:
    """Cached /v1/singbox status from the local sidecar. Empty when unreachable.

    Mirrors worker_vlk_verdict: the failure reason lands in uf5_singbox_note
    (shown under the versions row), so 'unknown' is always diagnosable.
    Success is an `installed` version ("" while still downloading).
    """
    import streamlit as st

    cached = st.session_state.get("uf5_singbox_status")
    if isinstance(cached, dict) and cached:
        return cached
    url = f"http://127.0.0.1:{WORKER_PORT}/v1/singbox"
    try:
        data = fetch_json(url, timeout=3)
    except urllib.error.HTTPError as e:
        detail = f"sidecar http {e.code}"
        try:
            body = json.loads(e.read().decode(errors="replace") or "{}")
            if isinstance(body, dict) and body.get("error"):
                detail += f": {body['error']}"
        except (ValueError, OSError):
            pass
        if e.code == 404:
            detail += " (old worker — redeploy for /v1/singbox)"
        if not _in_worker_grace():
            st.session_state["uf5_singbox_note"] = detail
        return {}
    except Exception:  # noqa: BLE001
        if not _in_worker_grace():
            st.session_state["uf5_singbox_note"] = f"sidecar unreachable on :{WORKER_PORT}"
        return {}
    if isinstance(data, dict) and "installed" in data:
        st.session_state["uf5_singbox_status"] = data
        st.session_state.pop("uf5_singbox_note", None)
        return data
    st.session_state["uf5_singbox_note"] = "empty status"
    return {}


def _worker_post(path: str, payload: dict, timeout: float = 30) -> dict:
    """POST JSON to the local sidecar. Raises on transport/HTTP errors."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{WORKER_PORT}{path}", data=body,
        headers={"Content-Type": "application/json", "User-Agent": "uf5vmjt/1.0"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode(errors="replace") or "{}")
    if not isinstance(data, dict):
        raise ValueError("bad sidecar response")
    return data


def worker_boot() -> int:
    """Worker process boot_unix from /version (0 when unreachable).
    Lets the panel tell a restarted worker from a slow one. Never raises."""
    try:
        data = fetch_json(f"http://127.0.0.1:{WORKER_PORT}/version", timeout=2)
    except Exception:  # noqa: BLE001
        return 0
    if not isinstance(data, dict):
        return 0
    try:
        return int(data.get("boot_unix") or 0)
    except (TypeError, ValueError):
        return 0


def worker_check_start(lines: list, timeout_ms: int = 10000,
                        parallel: int = 8, geo_parallel: int = 4,
                        dns: str = "auto") -> dict:
    """Start a proxy check run: POST /v1/check. Returns the 202 payload
    ({run_id, accepted, rejected}) or {"error": ...}. Never raises."""
    try:
        data = _worker_post("/v1/check", {"lines": lines, "timeout_ms": timeout_ms,
                                          "parallel": parallel, "geo_parallel": geo_parallel,
                                          "dns": dns or "auto"},
                            timeout=10)
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode(errors="replace") or "{}")
            detail = body.get("error", "") if isinstance(body, dict) else ""
        except (ValueError, OSError):
            detail = ""
        return {"error": f"sidecar http {e.code}" + (f": {detail}" if detail else "")}
    except Exception as e:  # noqa: BLE001
        return {"error": f"sidecar unreachable on :{WORKER_PORT} ({e})"}
    if not data.get("run_id"):
        return {"error": data.get("error") or "empty start verdict"}
    return data


def worker_check_poll(run_id: str) -> dict:
    """Poll a run: GET /v1/check?id=. Failure kinds stay distinct so the
    caller can tell them apart: {} = transport trouble (timeout/down),
    {"gone": True} = worker answered but forgot the run (restart or
    expiry). Snapshots carry run_id. Never raises."""
    url = f"http://127.0.0.1:{WORKER_PORT}/v1/check?id={run_id}"
    try:
        data = fetch_json(url, timeout=2)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"gone": True}
        return {}
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict) or not data.get("run_id"):
        return {}
    return data


_WORKER_PROC = None

