"""uf5vmjt — Streamlit system monitor: stats, shell, logs.

Thin entry point: page chrome, service boot, sidebar dispatch. Sections live
in lib/sections/, services in lib/services/, shared foundation in lib/core/.
"""
from __future__ import annotations

import os

from lib.core.auth import render_lock_button
from lib.core.events import log_event
from lib.core.ui import inject_style
from lib.sections.check import render_check
from lib.sections.logs import render_logs
from lib.sections.shell import render_shell
from lib.sections.stats import render_stats
from lib.services.netinfo import app_host, ensure_keepalive_config
from lib.services.sidecar import ensure_worker, seed_worker_env_from_secrets
from lib.services.snap import restore_if_needed, start as start_snapshot_loop
from lib.services.store import init as init_store
from lib.services.tailscale import trigger_tailscale_update

SECTION_ORDER = ["stats", "shell", "logs", "check"]
SECTION_LABELS = {"stats": "Stats", "shell": "Shell", "logs": "Logs", "check": "Check"}


def section_animation(section: str) -> str:
    """Slide direction vs the previous section (Stats→Shell→Logs = forward).

    Tracks the last full render in session state; fragment ticks never reach
    here, so realtime canvases keep animating undisturbed.
    """
    import streamlit as st

    prev = st.session_state.get("uf5_section_prev")
    st.session_state["uf5_section_prev"] = section
    if not prev or prev == section or prev not in SECTION_ORDER:
        return "uf5enter"
    forward = SECTION_ORDER.index(section) > SECTION_ORDER.index(prev)
    return "uf5enterR" if forward else "uf5enterL"


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="uf5vmjt", layout="wide")
    inject_style()
    seed_worker_env_from_secrets()
    trigger_tailscale_update()
    # Publish our own public URL for the keepalive worker: an explicit APP_URL
    # env wins, otherwise derive https://<host> from the browser URL so a
    # sidecar in the same container (or the operator copying the log line)
    # knows what to ping. Never overwrites, never raises.
    _host = app_host()
    if _host and not os.environ.get("APP_URL"):
        os.environ["APP_URL"] = f"https://{_host}"
        log_event("debug", f"APP_URL set to https://{_host}", source="net")
    ensure_keepalive_config()
    ensure_worker()
    try:
        init_store()
        restore_if_needed()
    except Exception as exc:
        log_event("warn", f"worker store boot: {type(exc).__name__}", source="store")
    try:
        start_snapshot_loop()
    except Exception:
        pass

    section = st.sidebar.radio("Navigate", SECTION_ORDER,
                               format_func=SECTION_LABELS.get,
                               label_visibility="collapsed",
                               key="uf5_section")
    anim = section_animation(section)
    # Fresh veil node on every full rerun -> replays on section switches only
    # (fragment ticks never re-execute main, so realtime canvases keep animating).
    st.markdown(f'<div class="uf5-veil"><div class="uf5-veil-line"></div>'
                f'<div class="uf5-veil-name">{SECTION_LABELS[section]}</div></div>',
                unsafe_allow_html=True)
    if anim != "uf5enter":
        st.markdown(f'<style>div[data-testid="stMainBlock"]{{animation-name:{anim};}}</style>',
                    unsafe_allow_html=True)

    st.sidebar.divider()
    render_lock_button()

    if section == "stats":
        render_stats()
    elif section == "shell":
        render_shell()
    elif section == "check":
        render_check()
    else:
        render_logs()


if __name__ == "__main__":
    main()
