"""uf5vmjt.lib.services.procs — per-process CPU and memory of our own PIDs.

/proc/cpuinfo and /proc/stat describe the host on a quota-limited container,
so a per-core chart shows a neighbour's load. Our own processes are the one
thing the container always sees truthfully: /proc/<pid>/stat is namespaced, so
every PID in the table is ours and its utime+stime is CPU we actually burned.

This is the honest replacement for "our share of the host": a 2-core quota is
enforced by the scheduler, so summing our processes shows what we use, not
what we are allowed to use.
"""
from __future__ import annotations

import os
from pathlib import Path

CLOCK_TICKS = 100
# /proc/<pid>/stat: comm may contain spaces and parentheses, so fields are
# counted from after the last ')'. utime is field 14 and stime field 15 in
# 1-based proc(5) terms, which is index 11 and 12 once comm is skipped.
_AFTER_COMM_UTIME = 11
_AFTER_COMM_STIME = 12
_AFTER_COMM_RSS = 21
_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def _clock_ticks() -> int:
    try:
        return int(os.sysconf("SC_CLK_TCK")) or CLOCK_TICKS
    except (ValueError, OSError, AttributeError):
        return CLOCK_TICKS


def read_stat_fields(pid: int) -> tuple[str, int, int, int] | None:
    """Return (comm, utime_jiffies, stime_jiffies, rss_pages) for one pid.

    None when the process exited between listing and reading — a normal race
    on a live box, not an error.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    close = raw.rfind(")")
    if close < 0:
        return None
    comm = raw[raw.find("(") + 1:close]
    rest = raw[close + 2:].split()
    if len(rest) <= _AFTER_COMM_RSS:
        return None
    try:
        utime = int(rest[_AFTER_COMM_UTIME])
        stime = int(rest[_AFTER_COMM_STIME])
        rss = int(rest[_AFTER_COMM_RSS])
    except (ValueError, IndexError):
        return None
    return comm, utime, stime, rss


def our_pids() -> list[int]:
    """Every pid visible to us. Namespaced /proc means these are all ours."""
    out: list[int] = []
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            out.append(int(entry))
    return sorted(out)


def is_ours(pid: int) -> bool:
    """True when a pid belongs to our user — the shell's own processes."""
    try:
        uid = os.stat(f"/proc/{pid}").st_uid
    except OSError:
        return False
    return uid == os.getuid()


def sample() -> dict[int, tuple[str, int, int]]:
    """Snapshot {pid: (comm, utime_jiffies, stime_jiffies)}."""
    out: dict[int, tuple[str, int, int]] = {}
    for pid in our_pids():
        fields = read_stat_fields(pid)
        if fields is not None:
            out[pid] = (fields[0], fields[1], fields[2])
    return out


def rss_bytes(pid: int) -> int:
    fields = read_stat_fields(pid)
    if fields is None:
        return 0
    return fields[3] * _PAGE_SIZE


def delta_percent(before: dict[int, tuple[str, int, int]],
                  after: dict[int, tuple[str, int, int]],
                  cores: int, window_s: float) -> float:
    """Our CPU as a percentage of one core, summed over live processes.

    A process that appeared after the first sample contributes nothing: there
    is no baseline for it, so counting it would invent work. `cores` divides
    the sum so the number reads against the container's quota (100% = all of
    it busy) rather than as an unbounded per-core figure.
    """
    if window_s <= 0 or cores <= 0:
        return 0.0
    hz = _clock_ticks()
    total_jiffies = 0.0
    for pid, (_comm, utime, stime) in after.items():
        prev = before.get(pid)
        if prev is None:
            continue
        total_jiffies += (utime + stime) - (prev[1] + prev[2])
    if total_jiffies <= 0:
        return 0.0
    return round((total_jiffies / hz) / window_s / cores * 100.0, 1)


def top_cpu(before: dict[int, tuple[str, int, int]],
            after: dict[int, tuple[str, int, int]],
            window_s: float, limit: int = 6) -> list[tuple[int, str, float]]:
    """Busiest of our processes: [(pid, comm, percent_of_one_core)]."""
    if window_s <= 0:
        return []
    hz = _clock_ticks()
    rows: list[tuple[int, str, float]] = []
    for pid, (comm, utime, stime) in after.items():
        prev = before.get(pid)
        if prev is None:
            continue
        jiffies = (utime + stime) - (prev[1] + prev[2])
        if jiffies <= 0:
            continue
        rows.append((pid, comm, round((jiffies / hz) / window_s * 100.0, 1)))
    rows.sort(key=lambda r: r[2], reverse=True)
    return rows[:limit]


def total_rss_bytes(pids: list[int] | None = None) -> int:
    """Resident memory of our processes. Shared pages are counted per process,
    so this is an upper bound, not a system total."""
    total = 0
    for pid in (pids if pids is not None else our_pids()):
        total += rss_bytes(pid)
    return total
