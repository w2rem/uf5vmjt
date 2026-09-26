"""uf5vmjt.lib.sections.stats — stats section."""
from __future__ import annotations
import html
import re
from typing import Any
from lib.core.config import (COLOR_ACCENT, COLOR_BORDER, COLOR_MEM_CACHE, COLOR_MEM_FREE,
                             COLOR_MEM_USED, COLOR_MUTED, COLOR_TEXT)
from lib.core.ui import badge
from lib.services import procs
from lib.services.disk import render_disk_panel
from lib.services.limits import effective_cores, effective_memory_bytes, our_cpu_ids
from lib.services.netinfo import flag_url, get_cluster, get_geo, shard_color
from lib.services.sysinfo import cpu_info, fmt_gb, fmt_mb, memory_segments, read_cpu_times, read_cpu_total, read_meminfo
from lib.services.versions import render_versions



def render_memory_bar() -> None:
    """Single 0..max stacked track: used + cache + free (HTML, animated).

    /proc/meminfo reports host RAM on a quota-limited container, so the ceiling
    is taken from the cgroup limit when there is one. The host total is kept as
    a separate metric: seeing 16GB on a 2.7GB quota is exactly the confusion
    this avoids.
    """
    import streamlit as st

    mem = read_meminfo()
    total, used, cache, free = memory_segments(mem)
    if not total:
        st.caption("memory info unavailable (no /proc/meminfo)")
        return
    ceiling, mem_source = effective_memory_bytes()
    limited = mem_source in ("quota", "override") and 0 < ceiling < total
    shown_total = ceiling if limited else total
    # Our own processes are the memory we are responsible for; the host's
    # free/total is context. Mixing them produced the "we use 1.2% RAM" line:
    # our RSS divided by the host's 128GB is arithmetically true and
    # operationally useless.
    census = procs.census()
    our_rss_kb = sum(p.rss_kb for p in census if p.is_ours)
    our_rss_kb = min(our_rss_kb, shown_total)
    host_used_kb = min(used, shown_total) if limited else used
    segs = [("Ours", our_rss_kb, COLOR_MEM_USED),
            ("Host other", max(host_used_kb - our_rss_kb, 0), COLOR_MEM_CACHE),
            ("Free", max(shown_total - host_used_kb, 0), COLOR_MEM_FREE)]
    if not limited:
        segs = [("Used", used, COLOR_MEM_USED), ("Cache", cache, COLOR_MEM_CACHE),
                ("Free", free, COLOR_MEM_FREE)]
    bar = "".join(
        f'<div class="uf5-memseg" title="{label} {fmt_mb(v)}" '
        f'style="width:{v / shown_total * 100:.2f}%;background:{color}"></div>'
        for label, v, color in segs
    )
    st.markdown(f'<div class="uf5-memtrack">{bar}</div>', unsafe_allow_html=True)
    legend = "".join(
        f'<span><span class="uf5-dot" style="background:{color}"></span>'
        f'{label} <b>{fmt_mb(v)}</b> {v / shown_total * 100:.1f}%</span>'
        for label, v, color in segs
    )
    st.markdown(f'<div class="uf5-legend">{legend}</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    if limited:
        c1.metric("Quota", fmt_mb(shown_total), f"host has {fmt_gb(total * 1024)}", delta_color="off")
    else:
        c1.metric("Total", fmt_mb(total), "capacity", delta_color="off")
    c2.metric("Our RAM", fmt_mb(our_rss_kb), f"{our_rss_kb / shown_total * 100:.1f}% of quota")
    c3.metric("Host used", fmt_mb(host_used_kb if limited else used),
              "other tenants share this host" if limited else "of host total")


def _float_history(raw: Any) -> dict[str, float]:
    """Keep only numeric entries from a session-state history blob.

    Regression guard: this key once held raw (total, idle) jiffies tuples, and
    a later revision read it as percentages — min() on a tuple is a TypeError
    that took down the whole CPU panel. A session can also survive a redeploy
    carrying the old shape, so anything non-numeric is dropped rather than
    trusted.
    """
    if not isinstance(raw, dict):
        return {}
    return {k: float(v) for k, v in raw.items() if isinstance(v, (int, float))}


def render_core_bars(per_core: dict[str, float], prev: dict[str, float], limit: int) -> list[str]:
    """Per-core water-fill bars with a drop ghost, ours in accent, host's pale.

    A bar that fell since the last tick keeps the lost portion in gray until
    the next tick melts it away, which reads as motion instead of a jump.
    """
    bars: list[str] = []
    for i, (name, value) in enumerate(per_core.items()):
        label = re.sub(r"[^a-z0-9]", "", name.lower()) or f"c{i}"
        pct = max(min(float(value), 100.0), 0.0)
        before = prev.get(name)
        drop = max(min(float(before), 100.0) - pct, 0.0) if isinstance(before, (int, float)) else 0.0
        has_ghost = drop >= 0.5
        ours_core = i < limit
        fill = COLOR_ACCENT if ours_core else COLOR_BORDER
        value_color = COLOR_TEXT if ours_core else COLOR_MUTED
        # Flush joint: flat top hugged by the ghost, no seam.
        fill_radius = "0 0 8px 8px" if has_ghost else "8px"
        ghost = (
            f'<div class="uf5-ghost" '
            f'style="bottom:{pct:.1f}%;height:{drop:.1f}%"></div>'
            if has_ghost else ""
        )
        bars.append(
            f'<div class="uf5-col"><div class="uf5-val" style="color:{value_color}">{pct:.0f}</div>'
            f'<div class="uf5-track"><div class="uf5-fill" '
            f'style="height:{pct:.1f}%;background:{fill};'
            f'animation-delay:{i * 70}ms;border-radius:{fill_radius}"></div>'
            f'{ghost}</div>'
            f'<div class="uf5-cap">{label}</div></div>'
        )
    return bars


def render_cpu_panel() -> None:
    """CPU model, core counts, per-core bars + our-process figures.

    On a quota-limited container /proc/cpuinfo and /proc/stat describe the
    host, so the counts and the per-core rows are filtered down to the CPUs
    the scheduler will actually place us on, and the header says which source
    the number came from (override / quota / affinity / host).
    """
    import streamlit as st

    model, host_physical, host_logical = cpu_info()
    ours, source = effective_cores()
    st.markdown(f'<div class="uf5-big">{model}</div>', unsafe_allow_html=True)
    if source == "host":
        st.markdown(f'<span class="uf5-muted">{host_physical} physical · {host_logical} logical cores</span>',
                    unsafe_allow_html=True)
    elif source == "override":
        st.markdown(
            f'<span class="uf5-muted">our quota: {ours} core(s) · host has {host_logical} '
            f'· declared via STREAM_CPU_LIMIT</span>',
            unsafe_allow_html=True)
    else:
        quota_txt = "cgroup quota" if source == "quota" else "sched affinity"
        st.markdown(
            f'<span class="uf5-muted">our quota: {ours} core(s) · {quota_txt} · host has {host_logical}</span>',
            unsafe_allow_html=True)
    # Per-core deltas come from the previous tick, kept in session_state.
    # Sampling twice inside one call with a sleep would block the fragment
    # for half a second on every tick, and sampling once per call is not a
    # delta at all — the first implementation did that and every bar read 0.
    snap_now = read_cpu_times()
    total_now, idle_now = read_cpu_total()
    prev = st.session_state.get("uf5_cpu_prev_sample")
    st.session_state["uf5_cpu_prev_sample"] = {"cores": dict(snap_now), "total": (total_now, idle_now)}
    per_core: dict[str, float] = {}
    host_avg = 0.0
    if prev:
        old_cores = prev.get("cores") or {}
        allowed = our_cpu_ids(sorted(snap_now.keys(), key=lambda n: int("".join(c for c in n if c.isdigit()) or 0)))
        for name in allowed:
            t1, i1 = old_cores.get(name, (0, 0))
            t2, i2 = snap_now.get(name, (0, 0))
            dt, di = t2 - t1, i2 - i1
            per_core[name] = round(max(0.0, min((1 - di / dt) * 100, 100.0)), 1) if dt > 0 else 0.0
        if per_core:
            host_avg = round(sum(per_core.values()) / len(per_core), 1)
        else:
            ot, oi = prev.get("total", (0, 0))
            dt, di = total_now - ot, idle_now - oi
            host_avg = round((1 - di / dt) * 100, 1) if dt > 0 else 0.0

    # Lifetime accounting, not a sub-second delta: a process that started a
    # moment ago rounds to ~0% over a 0.5s window and then reads as idle,
    # which is exactly the process an operator is looking for. /proc/uptime
    # minus the process's own starttime gives every process a real average.
    census = procs.census()
    ours_rows = [p for p in census if p.is_ours]
    their_rows = [p for p in census if not p.is_ours]
    our_core_pct = round(min(sum(p.cpu_pct for p in ours_rows) / ours, 100.0), 1) if ours else 0.0
    their_core_pct = round(min(sum(p.cpu_pct for p in their_rows) / ours, 100.0), 1) if ours else 0.0
    our_rss_mb = sum(p.rss_kb for p in ours_rows) / 1024
    their_rss_mb = sum(p.rss_kb for p in their_rows) / 1024

    m1, m2, m3 = st.columns(3)
    m1.metric("Our CPU", f"{our_core_pct}%", f"{their_core_pct}% other pids", delta_color="off")
    m2.metric("Our RAM", f"{our_rss_mb:.0f} MB", f"{their_rss_mb:.0f} MB other pids", delta_color="off")
    m3.metric("Host load", f"{host_avg:.1f}%", "includes other tenants", delta_color="off")

    if per_core:
        limit = ours if source != "host" else len(per_core)
        prev = _float_history(st.session_state.get("uf5_cpu_prev"))
        bars = render_core_bars(per_core, prev, limit)
        st.session_state["uf5_cpu_prev"] = dict(per_core)
        st.markdown(f'<div class="uf5-row">{"".join(bars)}</div>',
                    unsafe_allow_html=True)
        if source != "host":
            st.markdown(
                f'<div style="color:{COLOR_MUTED};font-size:11px;margin-top:4px">'
                f'first {limit} cores are ours (accent) · the rest are other tenants on this host (pale)</div>',
                unsafe_allow_html=True)


ROLE_LABEL = {
    "go worker": "Go sidecar · checks proxies, serves /v1",
    "tailscaled": "Tailscale daemon · mesh transport",
    "streamlit": "Streamlit server · UI and this panel",
    "python": "Python · pool, snapshots, geo cache",
    "runtime": "Runtime plumbing",
    "other": "Other pid in this container",
}


def render_processes() -> None:
    """Separate section: every pid in this container with what it is and what it burns.

    /proc is namespaced inside a container, so every pid listed here is ours —
    which is the only CPU/RAM figure that survives a host-mounted /proc. The
    table is intentionally outside the ticking fragment: process identity
    changes rarely, so re-rendering it every 2s only causes flicker.
    """
    import streamlit as st

    try:
        live = st.fragment(run_every=5)
    except TypeError:
        live = st.fragment

    @live
    def _table() -> None:
        if st.session_state.get("uf5_section", "stats") != "stats":
            return
        census = procs.census()
        if not census:
            st.caption("no processes visible")
            return
        ours, _c, _p = effective_cores()
        head = (
            f'<tr><th style="text-align:left;padding:0 12px 4px 0;color:{COLOR_MUTED};'
            f'font-weight:500">pid</th>'
            f'<th style="text-align:left;padding:0 12px 4px 0;color:{COLOR_MUTED};font-weight:500">what it is</th>'
            f'<th style="text-align:right;padding:0 12px 4px 0;color:{COLOR_MUTED};font-weight:500">RAM</th>'
            f'<th style="text-align:right;padding:0 0 4px 0;color:{COLOR_MUTED};font-weight:500">CPU of 1 core</th></tr>'
        )
        body = ""
        for p in census:
            accent = COLOR_ACCENT if p.is_ours else COLOR_BORDER
            role = ROLE_LABEL.get(p.role, p.role)
            body += (
                f'<tr><td style="padding:3px 12px 3px 0;color:{COLOR_MUTED}">{p.pid}</td>'
                f'<td style="padding:3px 12px 3px 0">'
                f'<span style="color:{accent}">●</span> {html.escape(p.comm)}'
                f'<div style="color:{COLOR_MUTED};font-size:11px">{html.escape(role)}</div></td>'
                f'<td style="padding:3px 12px 3px 0;text-align:right">{p.rss_kb / 1024:.0f} MB</td>'
                f'<td style="padding:3px 0 3px 0;text-align:right">{p.cpu_pct:.1f}%</td></tr>'
            )
        total_mb = sum(p.rss_kb for p in census) / 1024
        st.markdown(
            f'<div class="uf5-card" style="padding:12px 14px">'
            f'<table style="border-collapse:collapse;width:100%;font-size:12px">'
            f'{head}{body}</table>'
            f'<div style="color:{COLOR_MUTED};font-size:11px;margin-top:8px">'
            f'● ours — Go worker, tailscaled, Streamlit, Python · quota is {ours} core(s)</div>'
            f'<div style="color:{COLOR_MUTED};font-size:11px">'
            f'{len(census)} pids · {total_mb:.0f} MB resident (shared pages counted per process)</div>'
            f'</div>',
            unsafe_allow_html=True)

    _table()


def render_canvas() -> None:
    """Memory / CPU canvases as native tabs (no switcher widget, no emoji).

    Each tab owns a realtime fragment; the visible one animates, the hidden
    one costs a single /proc read per tick. Wheel switching is impossible in
    pure Streamlit — tabs are the lightest native mechanism.

    The process table inside render_cpu_panel is the one exception to the
    realtime rule: it is re-read but its identity changes rarely, so the
    census is cached per section visit instead of being re-rendered into the
    DOM every 2 seconds (the F7 pattern that made the panel flicker).
    """
    import streamlit as st

    try:
        live = st.fragment(run_every=2)
        slow = st.fragment(run_every=5)
    except TypeError:
        live = slow = st.fragment

    tab_mem, tab_cpu, tab_proc, tab_disk = st.tabs(["Memory", "CPU", "Processes", "Disk"])

    def _on_stats() -> bool:
        # Stale auto-timers from a previous section must render nothing —
        # otherwise their deltas land in a foreign section tree.
        return st.session_state.get("uf5_section", "stats") == "stats"

    with tab_mem:
        @live
        def _live_memory() -> None:
            if _on_stats():
                render_memory_bar()

        _live_memory()

    with tab_cpu:
        @live
        def _live_cpu() -> None:
            if _on_stats():
                render_cpu_panel()

        _live_cpu()

    with tab_proc:
        render_processes()

    with tab_disk:
        @slow
        def _live_disk() -> None:
            if _on_stats():
                render_disk_panel()

        _live_disk()


def render_geo_cluster() -> None:
    """Network block without card chrome: flag + IP + shard badge, watermark behind."""
    import streamlit as st

    # Staged: skeleton first (covers teardown gap), real content on resolve.
    slot = st.empty()
    slot.markdown(
        '<div class="uf5-geocard"><div class="uf5-sk" style="height:26px;width:45%"></div>'
        '<div style="height:10px"></div>'
        '<div class="uf5-sk" style="height:15px;width:70%"></div>'
        '<div style="height:8px"></div>'
        '<div class="uf5-sk" style="height:13px;width:55%"></div></div>',
        unsafe_allow_html=True,
    )
    geo = get_geo()
    cluster = get_cluster()
    if not geo:
        slot.caption("geo unavailable")
        return
    cc = str(geo.get("country_code", ""))
    flag = flag_url(cc)
    shard = str(cluster.get("cluster", "") or "")
    small = f'<img class="uf5-flag" src="{flag}" alt="{cc}"/>' if flag else ""
    big = f'<img class="uf5-geocard-bg" src="{flag}" alt=""/>' if flag else ""
    slot.markdown(
        f'<div class="uf5-geocard">{big}'
        f'<div class="uf5-geo-fg">'
        f'<div class="uf5-geo-ip">{small}'
        f'<span class="uf5-big">{geo.get("ip", "unknown")}</span>'
        + (badge(shard, shard_color(shard)) if shard != "unknown" else "")
        + f'</div>'
        f'<div>{geo.get("city", "?")}, {geo.get("country", "?")} ({cc or "?"})</div>'
        f'<div class="uf5-muted">AS{geo.get("asn", "?")} · '
        f'{geo.get("asn_organization", "unknown")}</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )


def render_stats() -> None:
    render_canvas()
    render_geo_cluster()
    render_versions()


