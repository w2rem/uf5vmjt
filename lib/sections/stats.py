"""uf5vmjt.lib.sections.stats — stats section."""
from __future__ import annotations
import html
import re
import time
from lib.core.config import (COLOR_ACCENT, COLOR_BORDER, COLOR_MEM_CACHE, COLOR_MEM_FREE,
                             COLOR_MEM_USED, COLOR_MUTED, COLOR_TEXT)
from lib.core.ui import badge
from lib.services import procs
from lib.services.disk import render_disk_panel
from lib.services.limits import declared_vs_visible, effective_cores, effective_memory_bytes, our_cpu_ids
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
    shown_total = ceiling if mem_source == "quota" and 0 < ceiling < total else total
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
    c1, c2, c3, c4 = st.columns(4)
    if mem_source in ("quota", "override") and 0 < ceiling < total:
        c1.metric("Quota", fmt_mb(ceiling),
                 f"host has {fmt_gb(total * 1024)}", delta_color="off")
    else:
        c1.metric("Total", fmt_mb(total), "capacity", delta_color="off")
    c2.metric("Used", fmt_mb(used), f"{used / shown_total * 100:.1f}%")
    c3.metric("Cache", fmt_mb(cache), f"{cache / shown_total * 100:.1f}%")
    c4.metric("Free", fmt_mb(free), f"{free / shown_total * 100:.1f}%")


def render_cpu_panel(realtime: bool = True) -> None:
    """CPU model, core counts, per-core bars (st.bar_chart) + used/free.

    On a quota-limited container /proc/cpuinfo and /proc/stat describe the
    host, so the counts and the per-core rows are filtered down to the CPUs
    the scheduler will actually place us on, and the header says which source
    the number came from (quota / affinity / host).

    `realtime=False` is used inside the ticking fragment: the per-core bars
    animate, but the process census is stable enough that re-rendering it
    every 2s only causes flicker, so it is read and rendered once per call
    without the animation churn.
    """
    import streamlit as st

    model, host_physical, host_logical = cpu_info()
    ours, source = effective_cores()
    core_txt, mem_txt = declared_vs_visible()
    st.markdown(f'<div class="uf5-big">{model}</div>', unsafe_allow_html=True)
    if source == "host":
        st.markdown(f'<span class="uf5-muted">{host_physical} physical · {host_logical} logical cores</span>',
                    unsafe_allow_html=True)
    elif source == "override":
        st.markdown(
            f'<span class="uf5-muted">{ours} cores (declared) · host reports {host_logical} '
            f'· STREAM_CPU_LIMIT</span>',
            unsafe_allow_html=True)
    else:
        quota_txt = "cgroup quota" if source == "quota" else "sched affinity"
        st.markdown(
            f'<span class="uf5-muted">{ours} cores for this container '
            f'· {quota_txt} · host has {host_logical}</span>',
            unsafe_allow_html=True)
    snap1 = read_cpu_times()
    total1, idle1 = read_cpu_total()
    if realtime:
        # Only the animated bar path needs a second sample; the lifetime
        # figures below are order-independent and a 0.5s sleep on every tick
        # would stall the fragment.
        time.sleep(0.5)
        snap2 = read_cpu_times()
        total2, idle2 = read_cpu_total()
    else:
        snap2, total2, idle2 = snap1, total1, idle1

    allowed = our_cpu_ids(sorted(snap1.keys(), key=lambda n: int("".join(c for c in n if c.isdigit()) or 0)))
    per_core: dict[str, float] = {}
    for name in allowed:
        t1, i1 = snap1.get(name, (0, 0))
        t2, i2 = snap2.get(name, (0, 0))
        dt, di = t2 - t1, i2 - i1
        per_core[name] = round((1 - di / dt) * 100, 1) if dt > 0 else 0.0
    if per_core:
        host_avg = round(sum(per_core.values()) / len(per_core), 1)
    else:
        dt, di = total2 - total1, idle2 - idle1
        host_avg = round((1 - di / dt) * 100, 1) if dt > 0 else 0.0

    # Lifetime accounting, not a 0.5s delta: a process that started a moment
    # ago rounds to ~0% over a sub-second window and then reads as idle, which
    # is exactly the process an operator is looking for. /proc/uptime minus the
    # process's own starttime gives every process a real average.
    census = procs.census()
    ours_rows = [p for p in census if p.is_ours]
    their_rows = [p for p in census if not p.is_ours]
    our_core_pct = round(min(sum(p.cpu_pct for p in ours_rows) / ours, 100.0), 1) if ours else 0.0
    their_core_pct = round(min(sum(p.cpu_pct for p in their_rows) / ours, 100.0), 1) if ours else 0.0
    our_rss_mb = sum(p.rss_kb for p in ours_rows) / 1024
    their_rss_mb = sum(p.rss_kb for p in their_rows) / 1024

    m1, m2, m3 = st.columns(3)
    m1.metric("Our CPU", f"{our_core_pct}%", f"{their_core_pct}% of other pids", delta_color="off")
    m2.metric("Our RAM", f"{our_rss_mb:.0f} MB", f"{their_rss_mb:.0f} MB other pids", delta_color="off")
    m3.metric("Host load", f"{host_avg:.1f}%", "includes other tenants", delta_color="off")

    rows_html = ""
    for group, accent, note in ((ours_rows, COLOR_ACCENT, "ours"), (their_rows, COLOR_BORDER, "other")):
        if not group:
            continue
        for p in group:
            rows_html += (
                f'<tr><td style="padding:2px 10px 2px 0;color:{COLOR_MUTED}">{p.pid}</td>'
                f'<td style="padding:2px 10px 2px 0">'
                f'<span style="color:{accent}">●</span> {html.escape(p.comm)}'
                f'<span style="color:{COLOR_MUTED}"> · {html.escape(p.role)}</span></td>'
                f'<td style="padding:2px 10px 2px 0;text-align:right">{p.rss_kb / 1024:.0f} MB</td>'
                f'<td style="padding:2px 0;text-align:right">{p.cpu_pct:.0f}%</td></tr>'
            )
    if rows_html:
        st.markdown(
            f'<div class="uf5-legend" style="margin-top:6px">'
            f'<table style="border-collapse:collapse;font-size:12px">{rows_html}</table>'
            f'<div style="color:{COLOR_MUTED};font-size:11px;margin-top:4px">'
            f'● ours (Go worker, tailscaled, Streamlit) · other pids in this container</div></div>',
            unsafe_allow_html=True)

    if per_core:
        # Water-fill bars + drop ghost: when a bar decreases, the lost portion
        # stays visible in gray until the next tick melts it away.
        # Cores inside our quota are filled in the accent colour; the rest of
        # the host's cores stay pale so the split is visible at a glance.
        limit = ours if source != "host" else len(per_core)
        prev = st.session_state.get("uf5_cpu_prev", {})
        bars = []
        for i, (name, value) in enumerate(per_core.items()):
            label = re.sub(r"[^a-z0-9]", "", name.lower()) or f"c{i}"
            pct = max(min(value, 100), 0)
            drop = max(min(prev.get(name, value), 100) - pct, 0)
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
                f'<div class="uf5-col"><div class="uf5-val" style="color:{value_color}">{value:.0f}</div>'
                f'<div class="uf5-track"><div class="uf5-fill" '
                f'style="height:{pct:.1f}%;background:{fill};'
                f'animation-delay:{i * 70}ms;border-radius:{fill_radius}"></div>'
                f'{ghost}</div>'
                f'<div class="uf5-cap">{label}</div></div>'
            )
        st.session_state["uf5_cpu_prev"] = dict(per_core)
        st.markdown(f'<div class="uf5-row">{"".join(bars)}</div>',
                    unsafe_allow_html=True)
        if source != "host":
            st.markdown(
                f'<div style="color:{COLOR_MUTED};font-size:11px;margin-top:4px">'
                f'first {limit} cores are ours (accent) · the rest are other tenants on this host (pale)</div>',
                unsafe_allow_html=True)


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

    tab_mem, tab_cpu, tab_disk = st.tabs(["Memory", "CPU", "Disk"])

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
                render_cpu_panel(realtime=False)

        _live_cpu()

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


