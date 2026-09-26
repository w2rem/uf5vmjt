"""uf5vmjt.lib.services.versions — version resolvers + versions row."""
from __future__ import annotations
import glob
import os
import platform
import re
import shutil
import subprocess
from lib.core.config import BIN_TAILSCALE, COLOR_MUTED, COLOR_OK, SB_BIN_DIR
from lib.core.events import log_event
from lib.core.ui import badge, load_icon
from lib.services.sidecar import worker_pg_verdict, worker_vlk_verdict
from lib.services.sidecar import worker_singbox_status
from lib.services.tailscale import worker_ts_status



def resolve_python_version() -> str:
    return platform.python_version()


def resolve_postgres_version() -> str:
    """Postgres server version via the sidecar pinger. Never raises."""
    return str(worker_pg_verdict().get("version", "") or "unknown")


def resolve_valkey_version() -> str:
    """Valkey server version via the sidecar pinger. Never raises."""
    return str(worker_vlk_verdict().get("version", "") or "unknown")


def resolve_tailscale_version() -> str:
    """Tailscale version from the sidecar status first, local binary fallback.

    The worker owns /tmp/bin/tailscale; both share /tmp, so the local probe
    still works when the sidecar is down. Never raises.
    """
    st = worker_ts_status()
    if isinstance(st.get("installed"), str) and st["installed"]:
        return st["installed"]
    ts = (BIN_TAILSCALE if os.path.isfile(BIN_TAILSCALE)
          else shutil.which("tailscale") or shutil.which("tailscaled"))
    if not ts:
        for cand in ("/tmp/tailit", os.path.expanduser("~/.local/bin")):
            try:
                for entry in os.listdir(cand):
                    if entry == "tailscale":
                        ts = os.path.join(cand, entry)
                        break
            except OSError:
                continue
            if ts:
                break
    if not ts:
        return "not installed"
    try:
        out = subprocess.run([ts, "version"], capture_output=True, text=True, timeout=10)
        m = re.search(r"(\d+\.\d+\.\d+)", (out.stdout or "") + (out.stderr or ""))
        return m.group(1) if m else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "error"


def resolve_singbox_version() -> str:
    """sing-box version from the sidecar status first, local tree fallback.

    The worker owns /tmp/bin/sagernet; both share /tmp, so the local probe
    still works when the sidecar is down. Never raises.
    """
    st = worker_singbox_status()
    if isinstance(st.get("installed"), str) and st["installed"]:
        return st["installed"]
    try:
        cands = sorted(glob.glob(os.path.join(SB_BIN_DIR, "singbox-v*", "sing-box")),
                       reverse=True)
    except OSError:
        cands = []
    for cand in cands:
        if not os.path.isfile(cand) or not os.access(cand, os.X_OK):
            continue
        try:
            out = subprocess.run([cand, "version"], capture_output=True,
                                 text=True, timeout=10)
            m = re.search(r"(\d+\.\d+\.\d+)", (out.stdout or "") + (out.stderr or ""))
            if m:
                return m.group(1)
        except (OSError, subprocess.SubprocessError):
            continue
    if cands:
        return "unknown"
    return "not installed"


def render_versions() -> None:
    """Python / Postgres / Valkey / Tailscale / sing-box row: icon + name + version."""
    import streamlit as st

    items = (
        ("Python", "python", resolve_python_version),
        ("Postgres", "postgres", resolve_postgres_version),
        ("Valkey", "valkey", resolve_valkey_version),
        ("Tailscale", "tailscale", resolve_tailscale_version),
        ("sing-box", "sagernet", resolve_singbox_version),
    )
    # Five badges share one row: shrink icons + type so nothing wraps.
    compact = len(items) > 4
    row_cls = "uf5-ver uf5-ver-compact" if compact else "uf5-ver"
    icon_px = 32 if compact else 44
    cols = st.columns(len(items))
    slots = [c.empty() for c in cols]
    cached_versions = st.session_state.setdefault("uf5_versions", {})
    for s in slots:
        s.markdown('<div class="uf5-sk" style="height:44px"></div>',
                   unsafe_allow_html=True)
    for slot, (label, icon, resolver) in zip(slots, items):
        version = cached_versions.get(label)
        # Transient answers are retried on the next visit: the toolchain may
        # still be downloading and the sidecar may still be warming up.
        # Final answers stay cached.
        if not version or version in ("unknown", "error", "not installed"):
            with st.spinner(f"resolving {label.lower()}…"):
                version = resolver()
            cached_versions[label] = version
        svg = load_icon(icon, icon_px)
        color = COLOR_OK if version not in ("not installed", "error", "unknown") else COLOR_MUTED
        slot.markdown(
            f'<div class="{row_cls}">{svg}<span class="uf5-ver-name">{label}</span>'
            f'{badge(version, color)}</div>',
            unsafe_allow_html=True)
        log_event("debug", f"version {label}={version}")
    note = st.session_state.get("uf5_pg_note")
    if note:
        st.caption(f"postgres source: {note}")
    vlk_note = st.session_state.get("uf5_vlk_note")
    if vlk_note:
        st.caption(f"valkey source: {vlk_note}")
    sb_note = st.session_state.get("uf5_singbox_note")
    if sb_note:
        st.caption(f"sing-box source: {sb_note}")


