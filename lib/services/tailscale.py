"""uf5vmjt.lib.services.tailscale — sidecar tailscale trigger/status."""
from __future__ import annotations
import json
import time
import urllib.error
import urllib.request
from lib.core.config import WORKER_PORT
from lib.core.events import log_event

TS_OLD_WORKER_TTL_SEC = 600


def trigger_tailscale_update() -> None:
    """Nudge the sidecar to refresh /tmp/bin/tailscale (fire-and-forget).

    The worker answers 202 immediately and downloads detached, so the panel
    never blocks on the 25 MB fetch. State lives in session_state (survives
    script re-execution in any process model): done once the version badge
    resolved a real version, quiet for 10 min against a pre-endpoint worker,
    retried on transient errors (sidecar still starting). Never raises.
    """
    import streamlit as st

    if st.session_state.get("uf5_ts_triggered"):
        return
    cached = (st.session_state.get("uf5_versions") or {}).get("Tailscale", "")
    if cached and cached not in ("unknown", "error", "not installed"):
        st.session_state["uf5_ts_triggered"] = True
        return
    try:
        if time.time() - float(st.session_state.get("uf5_ts_old_worker_ts", 0) or 0) < TS_OLD_WORKER_TTL_SEC:
            return
    except (TypeError, ValueError):
        pass
    # The version badge renders on stats only; other sections would never
    # close the gate above. Ask the sidecar directly (localhost, instant):
    # installed means done, no update POST at all.
    stt = worker_ts_status()
    if isinstance(stt.get("installed"), str) and stt["installed"]:
        st.session_state["uf5_ts_triggered"] = True
        return
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{WORKER_PORT}/v1/tailscale",
            data=json.dumps({"func": "update"}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            if r.status == 202:
                st.session_state["uf5_ts_triggered"] = True
                log_event("debug", "tailscale update triggered on sidecar",
                          source="tool")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            st.session_state["uf5_ts_old_worker_ts"] = time.time()
        log_event("debug", f"tailscale trigger skipped: HTTP {e.code}", source="tool")
    except Exception as e:  # noqa: BLE001
        log_event("debug", f"tailscale trigger skipped: {e}", source="tool")


def worker_ts_status() -> dict:
    """Sidecar tailscale snapshot (func=status). Empty when unreachable."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{WORKER_PORT}/v1/tailscale",
            data=json.dumps({"func": "status"}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as r:
            data = json.loads(r.read().decode(errors="replace"))
            return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}
