"""uf5vmjt.lib.sections.stats — stats section."""
from __future__ import annotations
import html
import os
import re
import time
from typing import Any
from lib.core.config import (BAR_FILL, COLOR_MEM_OTHER, COLOR_MUTED, COLOR_OURS,
                             COLOR_TEXT, CORE_OURS, MEM_FREE, MEM_HOST,
                             MEM_HOST_CACHE, MEM_OURS, MEM_OURS_SIBLING)
from lib.core.ui import badge
from lib.services import procs
from lib.services.disk import render_disk_panel
from lib.services.limits import effective_cores, effective_memory_bytes, our_cpu_ids
from lib.services.netinfo import flag_url, get_cluster, get_geo, shard_color
from lib.services.sysinfo import cpu_info, fmt_gb, fmt_mb, memory_segments, read_cpu_times, read_cpu_total, read_meminfo
from lib.services.versions import render_versions



def render_memory_bar() -> None:
    """Stacked track: ours (green) + everything else (blues), plus free.

    /proc/meminfo reports host RAM on a quota-limited container, so the
    ceiling comes from the quota. The host's used figure is then clamped to
    the ceiling and OUR share is subtracted from it: a 45GB host reading
    clipped to 2.7GB would otherwise swallow our 176MB entirely and the
    green segment would be invisible. The truth is simpler — the host's
    numbers are not our concern, so the track shows our RSS against the quota
    and everything unaccounted as "other".
    """
    import streamlit as st

    mem = read_meminfo()
    total, used, cache, free = memory_segments(mem)
    if not total:
        st.caption("memory info unavailable (no /proc/meminfo)")
        return
    # Unit trap: read_meminfo is in kB, effective_memory_bytes is in BYTES.
    # Comparing them directly is always False, which is why the quota branch
    # never fired and our RAM was sliced out of a host-sized bar.
    ceiling_bytes, mem_source = effective_memory_bytes()
    ceiling_kb = ceiling_bytes // 1024
    limited = mem_source in ("quota", "override") and 0 < ceiling_kb < total

    # Two tracks with two different denominators, because they are two
    # different questions: how much of our 2.7GB quota do we burn, and how much
    # of the host's 128GB is in use. Squeezing both onto one bar is what made
    # our 176MB read as 0.1%. Page cache is excluded from "active" on both —
    # it is reclaimable, not consumption.
    census = procs.census()
    our_rss_kb = max(0, sum(p.rss_kb for p in census if p.is_ours))
    other_pids_kb = max(0, sum(p.rss_kb for p in census if not p.is_ours))
    host_active_kb = max(0, used - cache)

    # Our container's track. Denominator is the quota, so our 176MB reads as
    # 6% of 2.7GB rather than 0.1% of the host's 128GB.
    st.markdown(
        f'<div style="color:{COLOR_MUTED};font-size:11px;margin:8px 0 2px">'
        f'our app · {fmt_mb(ceiling_kb if limited else total)} quota · active, no cache</div>',
        unsafe_allow_html=True)
    our_base = ceiling_kb if limited else total
    our_segs = [("our app", min(our_rss_kb, our_base), MEM_OURS),
                ("our app, other pids",
                 max(0, min(other_pids_kb, our_base - our_rss_kb)), MEM_OURS_SIBLING),
                ("free", max(0, our_base - our_rss_kb - other_pids_kb), MEM_FREE)]
    st.markdown(render_memory_track(our_segs, our_base), unsafe_allow_html=True)

    # The host's track. Same container seen from the machine's side, so our
    # share is repeated here at a different scale instead of being invisible.
    st.markdown(
        f'<div style="color:{COLOR_MUTED};font-size:11px;margin:12px 0 2px">'
        f'the host · {fmt_gb(total * 1024)} total · active {fmt_mb(host_active_kb)} · '
        f'cache {fmt_mb(cache)} reclaimable</div>', unsafe_allow_html=True)
    host_segs = [("our app", min(our_rss_kb, total), MEM_OURS),
                ("other tenants",
                 max(0, min(host_active_kb - our_rss_kb, total - our_rss_kb)), MEM_HOST),
                ("host cache", min(cache, max(0, total - host_active_kb)), MEM_HOST_CACHE),
                ("free", max(0, total - host_active_kb - cache), MEM_FREE)]
    st.markdown(render_memory_track(host_segs, total), unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    quota_kb = ceiling_kb if limited else total
    c1.metric("Our quota", fmt_mb(quota_kb),
              f"{our_rss_kb / quota_kb * 100:.1f}% used by us" if quota_kb else "n/a")
    c2.metric("Host active", fmt_mb(host_active_kb),
              f"{host_active_kb / total * 100:.1f}% of {fmt_gb(total * 1024)}", delta_color="off")
    c3.metric("Host cache", fmt_mb(cache), "reclaimable, not counted", delta_color="off")


def render_memory_track(segs: list[tuple[str, int, str]], base: int) -> str:
    """One stacked memory track with a legend; `base` is the denominator in kB.

    Kept pure so the segment arithmetic is testable without Streamlit. Segments
    are clipped cumulatively: callers pass raw RSS sums that can exceed the
    base (a container's RSS is counted per process, so shared pages are double
    counted), and an overflowing stack would silently overflow the track.
    """
    total = max(int(base), 1)
    parts = []
    legend = []
    remaining = total
    for label, value_kb, color in segs:
        value = max(0, int(value_kb))
        value = min(value, remaining)
        remaining -= value
        pct = value / total * 100.0
        parts.append(
            f'<div class="uf5-memseg" title="{label} {fmt_mb(value)} ({pct:.1f}%)" '
            f'style="width:{pct:.2f}%;background:{color}"></div>'
        )
        legend.append(
            f'<span><span class="uf5-dot" style="background:{color}"></span>'
            f'{label} <b>{fmt_mb(value)}</b> {pct:.1f}%</span>'
        )
    return (f'<div class="uf5-memtrack">{"".join(parts)}</div>'
            f'<div class="uf5-legend">{"".join(legend)}</div>')


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
    """Per-core water-fill bars.

    Colour is the whole point of this chart, so the families are exact:
      green  — cores proven to be ours (affinity mask narrowed below the host)
      blue   — every other core: other tenants' work
      gray   — the drop ghost, the portion a bar fell since the last tick

    Our load is NOT smeared across these bars. It was tried, and at a realistic
    0.3% of a 16-core quota that works out to 0.05% of a bar — a tenth of a
    pixel, invisible, and it obscured the host's own data to no benefit. It is
    shown instead on the green segment of the "who is using the host" bar
    above, where it is measured and readable.

    A bar that fell keeps the lost portion in gray until the next tick melts
    it away, which reads as motion instead of a jump.
    """
    bars: list[str] = []
    for i, (name, value) in enumerate(per_core.items()):
        label = re.sub(r"[^a-z0-9]", "", name.lower()) or f"c{i}"
        pct = max(min(float(value), 100.0), 0.0)
        before = prev.get(name)
        drop = max(min(float(before), 100.0) - pct, 0.0) if isinstance(before, (int, float)) else 0.0
        has_ghost = drop >= 0.5
        ours_core = i < limit
        fill = CORE_OURS if ours_core else BAR_FILL
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
            f'<div class="uf5-track">'
            f'<div class="uf5-fill" '
            f'style="height:{pct:.1f}%;background:{fill};'
            f'animation-delay:{i * 70}ms;border-radius:{fill_radius}"></div>'
            f'{ghost}</div>'
            f'<div class="uf5-cap">{label}</div></div>'
        )
    return bars


def _live_cpu_split(st: Any, cores: int) -> tuple[float, float]:
    """Live CPU of our pids and of the others, as percent of the quota.

    Deltas the previous tick's jiffies against this tick's, so a checker
    burning a core right now reads 100% instead of a lifetime average that
    rounds it away. Processes without a baseline are skipped: there is no
    measurement for them, and inventing one would overstate the load.

    Both figures come from one sample and one window, so they are comparable.
    """
    now = procs.sample()
    prev = st.session_state.get("uf5_our_cpu_prev")
    st.session_state["uf5_our_cpu_prev"] = dict(now)
    window = _tick_window(st)
    if not prev or cores <= 0 or window <= 0:
        return 0.0, 0.0
    ours_pids = {p.pid for p in procs.census() if p.is_ours}
    hz = float(_clock_ticks())
    our_jiffies = 0.0
    other_jiffies = 0.0
    for pid, (_comm, utime, stime) in now.items():
        old = prev.get(pid)
        if old is None:
            continue
        delta = (utime + stime) - (old[1] + old[2])
        if delta <= 0:
            continue
        if pid in ours_pids:
            our_jiffies += delta
        else:
            other_jiffies += delta
    scale = 100.0 / hz / window / cores
    return (round(max(0.0, min(our_jiffies * scale, 100.0)), 1),
            round(max(0.0, min(other_jiffies * scale, 100.0)), 1))


def _clock_ticks() -> int:
    try:
        return int(os.sysconf("SC_CLK_TCK")) or 100
    except (ValueError, OSError, AttributeError):
        return 100


def _tick_window(st: Any) -> float:
    """Seconds since the previous tick.

    Returns 0.0 on the very first call — there is no baseline yet, so a rate
    cannot be computed and the caller must not pretend otherwise. The fragment
    ticks every 2s, so by the second call a real window exists.
    """
    now = time.time()
    stamp = st.session_state.get("uf5_our_cpu_at")
    st.session_state["uf5_our_cpu_at"] = now
    if stamp is None:
        return 0.0
    return max(now - stamp, 0.001)


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
    # Our CPU is a live delta between the previous tick and this one, not a
    # lifetime average: the operator needs to see the checker burning a core
    # right now. Lifetime stays in the Processes tab, where it is the useful
    # number. A process with no baseline (started after the last tick) cannot
    # be measured and is excluded rather than counted as zero.
    our_core_pct, other_core_pct = _live_cpu_split(st, ours)
    our_rss_mb = sum(p.rss_kb for p in ours_rows) / 1024
    their_rss_mb = sum(p.rss_kb for p in their_rows) / 1024

    m1, m2, m3 = st.columns(3)
    m1.metric("Our CPU", f"{our_core_pct:.1f}%", f"of {ours} core(s)", delta_color="off")
    m2.metric("Other pids", f"{other_core_pct:.1f}%", f"{their_rss_mb:.0f} MB not ours", delta_color="off")
    m3.metric("Host per-core", f"{host_avg:.1f}%", "all tenants, not ours", delta_color="off")

    # One track, two owners: our processes in green, the rest of the host in
    # blue. Both measured — ours from /proc/<pid>/stat inside the container,
    # the host share from the per-core average scaled to the whole machine.
    host_share = round(max(0.0, 100.0 - our_core_pct - other_core_pct), 1)
    st.markdown(
        f'<div style="color:{COLOR_MUTED};font-size:11px;margin:10px 0 2px">'
        f'who is using the host right now</div>', unsafe_allow_html=True)
    st.markdown(render_split_load_bar(our_core_pct, other_core_pct, host_share),
                unsafe_allow_html=True)

    if per_core:
        # The per-core rows come from /proc/stat, which is host-wide. A row is
        # ours only when the platform narrowed our affinity mask to exactly
        # `ours` CPUs — then the first `ours` rows are demonstrably ours. When
        # the mask is the whole host (Cloud without cpuset) NO row is
        # attributable to us, so zero bars get our green. Painting them all
        # green (the earlier `limit = len(per_core)` fallback) was worse than
        # useless: it claimed 16 green cores for a 2-core quota.
        split = _affinity_narrows(source, ours, len(per_core))
        limit = ours if split else 0
        prev = _float_history(st.session_state.get("uf5_cpu_prev"))
        bars = render_core_bars(per_core, prev, limit)
        st.session_state["uf5_cpu_prev"] = dict(per_core)
        st.markdown(f'<div class="uf5-row">{"".join(bars)}</div>',
                    unsafe_allow_html=True)
        if split:
            st.markdown(
                f'<div style="color:{COLOR_MUTED};font-size:11px;margin-top:4px">'
                f'first {limit} cores are ours (green) · the rest are other tenants (blue)'
                f'</div>', unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div style="color:{COLOR_MUTED};font-size:11px;margin-top:4px">'
                f'host per-core load, all {len(per_core)} cores · the platform does not tell us which '
                f'are ours, so every bar here is the host\'s. Our {our_core_pct:.1f}% is the green '
                f'segment in the bar above.'
                f'</div>', unsafe_allow_html=True)


PROCESS_REFRESH_DEFAULT = 5
PROCESS_REFRESH_MIN = 2
PROCESS_REFRESH_MAX = 30


def process_refresh_seconds(st: Any) -> int:
    """The operator's chosen refresh cadence for the process table.

    Read from session_state rather than taken as an argument because the
    slider writes there during the same rerun that builds the fragment, and
    run_every is fixed at construction time — so the value has to come from
    state on every pass, not from a stale local.
    """
    raw = st.session_state.get("uf5_proc_interval", PROCESS_REFRESH_DEFAULT)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return PROCESS_REFRESH_DEFAULT
    return max(PROCESS_REFRESH_MIN, min(PROCESS_REFRESH_MAX, value))


def _affinity_narrows(source: str, ours: int, visible: int) -> bool:
    """True only when we can prove which per-core rows are ours.

    The affinity mask is the only evidence: if it lists exactly `visible` CPUs
    the platform never narrowed us, and no row can be attributed. Declaring a
    quota or an override is not proof of placement, so those do not enable the
    split.
    """
    return source == "affinity" and 0 < ours < visible


def render_split_load_bar(ours_pct: float, other_pct: float, host_pct: float) -> str:
    """Horizontal track: our processes green, other pids blue, rest of the host
    pale. A full-width bar, not a column — a lone 200px column next to the
    per-core chart read as a seventeenth core.
    """
    ours = max(0.0, min(float(ours_pct), 100.0))
    other = max(0.0, min(float(other_pct), 100.0 - ours))
    host = max(0.0, min(float(host_pct), 100.0 - ours - other))
    # A 0.3% share is 3px of a 22px bar and reads as nothing. Give any
    # *non-zero* share a visible floor so "we are using some CPU" is legible;
    # a true zero stays zero, otherwise the bar would claim load that is not
    # there. The label always carries the real number.
    shown_ours = 0.0 if ours < 0.05 else (ours if ours >= 2.0 else 1.5)
    segs = [("Ours", ours, shown_ours, COLOR_OURS),
            ("Other pids", other, other, COLOR_MEM_OTHER),
            ("Rest of host", host, host, MEM_HOST)]
    bar = "".join(
        f'<div class="uf5-memseg" title="{label} {value:.1f}%" '
        f'style="width:{shown:.1f}%;background:{color}"></div>'
        for label, value, shown, color in segs
    )
    legend = "".join(
        f'<span><span class="uf5-dot" style="background:{color}"></span>'
        f'{label} <b>{value:.1f}%</b></span>'
        for label, value, _shown, color in segs
    )
    return (f'<div class="uf5-memtrack" style="height:22px">{bar}</div>'
            f'<div class="uf5-legend">{legend}</div>')


def _live_per_process(st: Any) -> dict[int, float]:
    """Per-pid CPU over the last fragment period, as a percent of one core.

    Separate keys from _live_cpu_split because the two fragments tick on
    different cadences (2s for the CPU panel, 5s for this table); sharing a
    baseline would make one of them measure across a window it never observed.
    A pid with no baseline returns nothing for that tick — an em dash, not a
    fabricated zero.
    """
    now = procs.sample()
    prev = st.session_state.get("uf5_proc_cpu_prev")
    st.session_state["uf5_proc_cpu_prev"] = dict(now)
    stamp = st.session_state.get("uf5_proc_cpu_at")
    at = time.time()
    st.session_state["uf5_proc_cpu_at"] = at
    # The window is measured, not assumed: the operator's slider can change the
    # cadence, and a hardcoded 2s would scale every reading wrongly.
    if prev is None or stamp is None:
        return {}
    window = at - stamp
    if window <= 0:
        return {}
    hz = float(_clock_ticks())
    out: dict[int, float] = {}
    for pid, (_comm, utime, stime) in now.items():
        old = prev.get(pid)
        if old is None:
            continue
        delta = (utime + stime) - (old[1] + old[2])
        if delta < 0:
            continue
        out[pid] = round((delta / hz) / window * 100.0, 1)
    return out


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

    # Refresh cadence is operator-controlled. run_every is baked in when the
    # fragment is constructed, so changing the slider has to rebuild it — hence
    # the value participates in the cache key below and the fragment is
    # re-declared whenever it changes.
    interval = process_refresh_seconds(st)
    st.slider("Process refresh (seconds)", min_value=PROCESS_REFRESH_MIN,
              max_value=PROCESS_REFRESH_MAX, value=interval, step=1,
              key="uf5_proc_interval", label_visibility="collapsed")
    st.markdown(
        f'<div style="color:{COLOR_MUTED};font-size:11px;margin:2px 0 6px">'
        f'refresh: every {interval}s · CPU now is the rate over that window</div>',
        unsafe_allow_html=True)

    try:
        live = st.fragment(run_every=interval)
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
        # effective_cores returns (cores, source) — two values, not three.
        ours, _source = effective_cores()
        live_pct = _live_per_process(st)
        head = (
            f'<tr><th style="text-align:left;padding:0 12px 4px 0;color:{COLOR_MUTED};'
            f'font-weight:500">pid</th>'
            f'<th style="text-align:left;padding:0 12px 4px 0;color:{COLOR_MUTED};font-weight:500">what it is</th>'
            f'<th style="text-align:right;padding:0 12px 4px 0;color:{COLOR_MUTED};font-weight:500">RAM</th>'
            f'<th style="text-align:right;padding:0 12px 4px 0;color:{COLOR_MUTED};font-weight:500">CPU now</th>'
            f'<th style="text-align:right;padding:0 0 4px 0;color:{COLOR_MUTED};font-weight:500">CPU avg</th></tr>'
        )
        body = ""
        for p in census:
            accent = CORE_OURS if p.is_ours else BAR_FILL
            role = ROLE_LABEL.get(p.role, p.role)
            now_pct = live_pct.get(p.pid)
            now_cell = ("—" if now_pct is None else f"{now_pct:.1f}%")
            body += (
                f'<tr><td style="padding:3px 12px 3px 0;color:{COLOR_MUTED}">{p.pid}</td>'
                f'<td style="padding:3px 12px 3px 0">'
                f'<span style="color:{accent}">●</span> {html.escape(p.comm)}'
                f'<div style="color:{COLOR_MUTED};font-size:11px">{html.escape(role)}</div></td>'
                f'<td style="padding:3px 12px 3px 0;text-align:right">{p.rss_kb / 1024:.0f} MB</td>'
                f'<td style="padding:3px 12px 3px 0;text-align:right">{now_cell}</td>'
                f'<td style="padding:3px 0 3px 0;text-align:right;color:{COLOR_MUTED}">{p.cpu_pct:.1f}%</td></tr>'
            )
        total_mb = sum(p.rss_kb for p in census) / 1024
        st.markdown(
            f'<div class="uf5-card" style="padding:12px 14px">'
            f'<table style="border-collapse:collapse;width:100%;font-size:12px">'
            f'{head}{body}</table>'
            f'<div style="color:{COLOR_MUTED};font-size:11px;margin-top:8px">'
            f'● ours — Go worker, tailscaled, Streamlit, Python · quota is {ours} core(s)</div>'
            f'<div style="color:{COLOR_MUTED};font-size:11px">'
            f'CPU now = last 5s · CPU avg = since the process started · '
            f'{len(census)} pids · {total_mb:.0f} MB resident (shared pages counted per process)</div>'
            f'</div>',
            unsafe_allow_html=True)

    _table()


def render_canvas() -> None:
    """Memory / CPU / Processes / Disk as tabs with lazy execution.

    Streamlit computes and ships every tab's content by default, so all four
    fragments were ticking at once — including the two the operator was not
    looking at. `on_change="rerun"` makes tab content lazy: only the selected
    tab is executed, and `TabContainer.open` says which one it is. That drops
    the per-tick work from four panels to one.
    """
    import streamlit as st

    labels = ["Memory", "CPU", "Processes", "Disk"]
    try:
        tabs = st.tabs(labels, on_change="rerun")
    except TypeError:
        # Older Streamlit has no lazy tabs and no .open; fall back to eager
        # rendering and rely on the section guard instead.
        tabs = st.tabs(labels)
        has_open = False
    else:
        has_open = True

    def _open(tab, name: str) -> bool:
        if not has_open:
            return True
        try:
            return bool(tab.open)
        except Exception:
            return True

    def _on_stats() -> bool:
        # Stale auto-timers from a previous section must render nothing —
        # otherwise their deltas land in a foreign section tree.
        return st.session_state.get("uf5_section", "stats") == "stats"

    tab_mem, tab_cpu, tab_proc, tab_disk = tabs
    is_mem, is_cpu, is_proc, is_disk = (_open(t, n) for t, n in zip(tabs, labels))

    if is_mem:
        with tab_mem:
            @_live_fragment(st, 2)
            def _live_memory() -> None:
                if _on_stats():
                    render_memory_bar()

            _live_memory()

    if is_cpu:
        with tab_cpu:
            @_live_fragment(st, 2)
            def _live_cpu() -> None:
                if _on_stats():
                    render_cpu_panel()

            _live_cpu()

    if is_proc:
        with tab_proc:
            render_processes()

    if is_disk:
        with tab_disk:
            @_live_fragment(st, 5)
            def _live_disk() -> None:
                if _on_stats():
                    render_disk_panel()

            _live_disk()


def _live_fragment(st: Any, seconds: int):
    """st.fragment(run_every=N) with a fallback for builds that lack it."""
    try:
        return st.fragment(run_every=seconds)
    except TypeError:
        return st.fragment


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


