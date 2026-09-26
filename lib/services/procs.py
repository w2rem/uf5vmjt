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
from dataclasses import dataclass
from pathlib import Path

CLOCK_TICKS = 100
# /proc/<pid>/stat: comm may contain spaces and parentheses, so fields are
# counted from after the last ')'. utime is field 14 and stime field 15 in
# 1-based proc(5) terms, which is index 11 and 12 once comm is skipped.
_AFTER_COMM_UTIME = 11
_AFTER_COMM_STIME = 12
_AFTER_COMM_RSS = 21
# starttime is field 22 (1-based) in proc(5), i.e. index 19 after comm.
_AFTER_COMM_START = 19
_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def read_cmdline(pid: int) -> str:
    """Full command name from /proc/<pid>/cmdline.

    /proc/<pid>/comm is capped at 15 bytes by the kernel, which is why a
    worker shows up as "w" and a Streamlit runner as "run-streamlit.s". The
    first cmdline argument carries the real name, so it is preferred.
    """
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    if not raw:
        return ""
    first = raw.split(b"\0", 1)[0]
    return first.decode("utf-8", "replace").rsplit("/", 1)[-1][:32]


def read_statm_rss_kb(pid: int) -> int:
    """Resident pages from /proc/<pid>/statm, in kB. Matches `ps` RSS."""
    try:
        parts = Path(f"/proc/{pid}/statm").read_text().split()
    except (OSError, ValueError):
        return 0
    if len(parts) < 2:
        return 0
    try:
        return int(parts[1]) * 4
    except ValueError:
        return 0


def boot_jiffies() -> int:
    """System uptime in jiffies, from /proc/uptime."""
    try:
        raw = Path("/proc/uptime").read_text().split()
        return int(float(raw[0]) * _clock_ticks())
    except (OSError, ValueError, IndexError):
        return 0


def lifetime_percent(utime: int, stime: int, start_jiffies: int, now_jiffies: int) -> float:
    """Lifetime CPU of one process as a percent of one core.

    The window is uptime minus the process's own start time, so a short-lived
    process reports its true average instead of vanishing: a delta over a
    sub-second window rounds to zero for anything that just started, which is
    exactly the process an operator is looking for.
    """
    hz = _clock_ticks()
    window = (now_jiffies - start_jiffies) / hz
    if window <= 0 or hz <= 0:
        return 0.0
    return round(((utime + stime) / hz) / window * 100.0, 1)


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


# Names that identify the components this worker runs. The Go binary reports
# comm "w" (15-byte cap), so the Go sidecar is matched on the binary name.
KNOWN_ROLES = ("worker", "tailscaled", "streamlit", "python", "supervisord",
               "run-streamlit", "entrypoint", "sh")


@dataclass
class ProcInfo:
    """One visible process, with everything the panel renders about it."""

    pid: int
    comm: str
    rss_kb: int
    cpu_pct: float
    role: str
    is_ours: bool


def _classify(name: str) -> tuple[str, bool]:
    low = name.lower()
    if low == "worker":
        return "go worker", True
    if low == "tailscaled":
        return "tailscaled", True
    if low == "streamlit" or low.startswith("run-streamlit"):
        return "streamlit", True
    if low.startswith("python"):
        return "python", True
    if low in {"supervisord", "entrypoint", "sh", "sleep", "bash"}:
        return "runtime", True
    return "other", False


def census() -> list[ProcInfo]:
    """Every visible process with CPU, RSS and a role classification.

    Every pid in here is inside our container, so `is_ours` marks the ones
    that are actually part of the worker stack (Go sidecar, tailscaled,
    Streamlit) rather than shell plumbing and transients.
    """
    now = boot_jiffies()
    out: list[ProcInfo] = []
    for pid in our_pids():
        fields = read_stat_fields(pid)
        if fields is None:
            continue
        _comm15, utime, stime, _rss_pages = fields
        name = read_cmdline(pid) or fields[0]
        role, ours = _classify(name)
        start = read_start_jiffies(pid)
        out.append(ProcInfo(
            pid=pid,
            comm=name,
            rss_kb=read_statm_rss_kb(pid),
            cpu_pct=lifetime_percent(utime, stime, start, now),
            role=role,
            is_ours=ours,
        ))
    out.sort(key=lambda p: (not p.is_ours, -p.cpu_pct, -p.rss_kb))
    return out


def read_start_jiffies(pid: int) -> int:
    """Absolute start time of a process, in jiffies since boot."""
    fields = _raw_fields(pid)
    if fields is None:
        return 0
    try:
        return int(fields[_AFTER_COMM_START])
    except (ValueError, IndexError):
        return 0


def _raw_fields(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    close = raw.rfind(")")
    if close < 0:
        return None
    return raw[close + 2:].split()
