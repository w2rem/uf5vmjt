"""uf5vmjt.lib.sections.stats — stats section."""
from __future__ import annotations
import html
import re
import time
from lib.core.config import COLOR_MEM_CACHE, COLOR_MEM_FREE, COLOR_MEM_USED, COLOR_MUTED
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


def render_cpu_panel() -> None:
    """CPU model, core counts, per-core bars (st.bar_chart) + used/free.

    On a quota-limited container /proc/cpuinfo and /proc/stat describe the
    host, so the counts and the per-core rows are filtered down to the CPUs
    the scheduler will actually place us on, and the header says which source
    the number came from (quota / affinity / host).
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
    our_before = procs.sample()
    time.sleep(0.5)
    snap2 = read_cpu_times()
    total2, idle2 = read_cpu_total()
    our_after = procs.sample()
    window = 0.5

    allowed = our_cpu_ids(sorted(snap1.keys(), key=lambda n: int("".join(c for c in n if c.isdigit()) or 0)))
    per_core: dict[str, float] = {}
    for name in allowed:
        t1, i1 = snap1.get(name, (0, 0))
        t2, i2 = snap2.get(name, (0, 0))
        dt, di = t2 - t1, i2 - i1
        per_core[name] = round((1 - di / dt) * 100, 1) if dt > 0 else 0.0
    # Our own processes are the only CPU figure that is truthful on a
    # quota-limited container: /proc/<pid>/stat is namespaced, so every pid is
    # ours, while /proc/stat rows and the aggregate "cpu" line span the host.
    our_pct = procs.delta_percent(our_before, our_after, cores=ours, window_s=window)
    if per_core:
        host_avg = round(sum(per_core.values()) / len(per_core), 1)
    else:
        dt, di = total2 - total1, idle2 - idle1
        host_avg = round((1 - di / dt) * 100, 1) if dt > 0 else 0.0
    avg = our_pct

    m1, m2, m3 = st.columns(3)
    m1.metric("Our CPU", f"{our_pct}%", f"{host_avg}% host", delta_color="off")
    top = procs.top_cpu(our_before, our_after, window, limit=6)
    m2.metric("Our processes", len(our_after), f"{len(procs.our_pids())} pids total", delta_color="off")
    m3.metric("Free", f"{max(0.0, 100 - our_pct):.1f}%", "of our quota")

    if top:
        rows = "".join(
            f'<tr><td style="padding:2px 10px 2px 0;color:{COLOR_MUTED}">{pid}</td>'
            f'<td style="padding:2px 10px 2px 0">{html.escape(comm)}</td>'
            f'<td style="padding:2px 0;text-align:right">{pct:.0f}%</td></tr>'
            for pid, comm, pct in top
        )
        st.markdown(
            f'<div class="uf5-legend" style="margin-top:6px">'
            f'<table style="border-collapse:collapse;font-size:12px">{rows}</table></div>',
            unsafe_allow_html=True)
    st.caption("Our processes = pids in this container (namespaced /proc). "
               "Host load includes other tenants.")

    if per_core:
        # Water-fill bars + drop ghost: when a bar decreases, the lost portion
        # stays visible in gray until the next tick melts it away.
        prev = st.session_state.get("uf5_cpu_prev", {})
        bars = []
        for i, (name, value) in enumerate(per_core.items()):
            label = re.sub(r"[^a-z0-9]", "", name.lower()) or f"c{i}"
            pct = max(min(value, 100), 0)
            drop = max(min(prev.get(name, value), 100) - pct, 0)
            has_ghost = drop >= 0.5
            # Flush joint: flat blue top hugged by the ghost, no seam.
            fill_radius = "0 0 8px 8px" if has_ghost else "8px"
            ghost = (
                f'<div class="uf5-ghost" '
                f'style="bottom:{pct:.1f}%;height:{drop:.1f}%"></div>'
                if has_ghost else ""
            )
            bars.append(
                f'<div class="uf5-col"><div class="uf5-val">{value:.0f}</div>'
                f'<div class="uf5-track"><div class="uf5-fill" '
                f'style="height:{pct:.1f}%;animation-delay:{i * 70}ms;'
                f'border-radius:{fill_radius}"></div>'
                f'{ghost}</div>'
                f'<div class="uf5-cap">{label}</div></div>'
            )
        st.session_state["uf5_cpu_prev"] = dict(per_core)
        st.markdown(f'<div class="uf5-row">{"".join(bars)}</div>',
                    unsafe_allow_html=True)


def render_canvas() -> None:
    """Memory / CPU canvases as native tabs (no switcher widget, no emoji).

    Each tab owns a realtime fragment; the visible one animates, the hidden
    one costs a single /proc read per tick. Wheel switching is impossible in
    pure Streamlit — tabs are the lightest native mechanism.
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
                render_cpu_panel()

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


