"""uf5vmjt.lib.services.limits — effective cgroup resource limits.

On GCP (and most managed container platforms) /proc/cpuinfo and /proc/meminfo
describe the *host*, not the container: a 2-core / 2.7GB quota renders as 16
cores and 16GB. /proc/self/status Cpus_allowed_list is the CPU affinity mask
when the runtime sets one, and the cgroup files are the authoritative quota.
Both are read here so the UI reports what the process actually gets.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

CGROUP_V2_CPU = "/sys/fs/cgroup/cpu.max"
CGROUP_V2_MEM = "/sys/fs/cgroup/memory.max"
CGROUP_V1_CPU_QUOTA = "/sys/fs/cgroup/cpu/cpu.cfs_quota_us"
CGROUP_V1_CPU_PERIOD = "/sys/fs/cgroup/cpu/cpu.cfs_period_us"
CGROUP_V1_MEM = "/sys/fs/cgroup/memory/memory.limit_in_bytes"

# Values the kernel reports for "no limit set" — never show these as a cap.
UNLIMITED_MEMORY = {"max", "", "0"}
_MIN_MHZ = 1.0


def _read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def _read_int(path: str) -> int | None:
    raw = _read(path)
    if not raw or raw in UNLIMITED_MEMORY:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def cgroup_cpu_quota() -> float | None:
    """CPU quota in whole cores, or None when unlimited.

    cgroup v2: "<quota> <period>" with quota "max" meaning unlimited.
    cgroup v1: quota/period, where quota -1 means unlimited.
    """
    raw = _read(CGROUP_V2_CPU)
    if raw:
        parts = raw.split()
        if parts:
            if parts[0] == "max":
                return None
            try:
                quota = float(parts[0])
                period = float(parts[1]) if len(parts) > 1 else 100000.0
            except ValueError:
                quota = 0.0
                period = 0.0
            if quota > 0 and period > 0:
                return quota / period
    quota = _read_int(CGROUP_V1_CPU_QUOTA)
    period = _read_int(CGROUP_V1_CPU_PERIOD)
    if quota is not None and quota > 0 and period and period > 0:
        return quota / period
    return None


def affinity_cores() -> int | None:
    """Number of CPUs in the affinity mask, or None when unrestricted."""
    indices = affinity_indices()
    return len(indices) if indices else None


def affinity_indices() -> set[int]:
    """CPU ids in the affinity mask, or an empty set when unrestricted.

    /proc/stat exposes a row per *host* CPU, so a container limited to two
    cores still sees sixteen rows. Only the masked ones are ours; the rest
    belong to neighbours and their load is not our load.
    """
    raw = ""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("Cpus_allowed_list:"):
                    raw = line.partition(":")[2].strip()
                    break
    except OSError:
        return set()
    if not raw:
        return set()
    out: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo, _, hi = chunk.partition("-")
            try:
                start, end = int(lo), int(hi)
            except ValueError:
                continue
            if end - start < 4096:
                out.update(range(start, end + 1))
        else:
            try:
                out.add(int(chunk))
            except ValueError:
                continue
    return out


def cgroup_memory_bytes() -> int | None:
    """Memory ceiling in bytes, or None when unlimited."""
    raw = _read(CGROUP_V2_MEM)
    if raw and raw not in UNLIMITED_MEMORY:
        try:
            return int(raw)
        except ValueError:
            pass
    value = _read_int(CGROUP_V1_MEM)
    if value is not None and value > 0:
        # v1 also reports a page-filling sentinel close to 2^63 for "no limit".
        if value < (1 << 62):
            return value
    return None


def our_cpu_ids(host_ids: list[str]) -> list[str]:
    """Filter /proc/stat CPU row names down to the ones we may run on.

    Returns the input unchanged when there is no affinity mask (bare metal),
    so a local run still shows every core.
    """
    allowed = affinity_indices()
    if not allowed:
        return host_ids
    out: list[str] = []
    for name in host_ids:
        digits = "".join(ch for ch in name if ch.isdigit())
        if not digits:
            continue
        if int(digits) in allowed:
            out.append(name)
    return out or host_ids


def effective_cores() -> tuple[int, int | None]:
    """Return (cores_visible_to_us, source).

    Preference order: an explicit override, then the cgroup quota (the real
    limit), then the affinity mask (what the scheduler will actually place us
    on), then os.cpu_count(). The source string is for display: "override" /
    "quota" / "affinity" / "host".

    The override exists because some managed platforms hide /sys/fs/cgroup
    from the container entirely. There is no in-container way to detect the
    quota then, and silently reporting the host's 16 cores is worse than an
    explicit declaration — hence STREAM_CPU_LIMIT / STREAM_MEM_LIMIT_MB.
    """
    override = _env_int("STREAM_CPU_LIMIT")
    if override is not None and override > 0:
        return override, "override"
    quota = cgroup_cpu_quota()
    if quota is not None and quota >= _MIN_MHZ:
        # 1.5 cores is 2 schedulable CPUs at 75% — round up so a fractional
        # quota never reads as fewer cores than the process may run on.
        return max(1, int(quota + 0.999)), "quota"
    aff = affinity_cores()
    if aff is not None:
        return aff, "affinity"
    return (os.cpu_count() or 1), "host"


def effective_memory_bytes() -> tuple[int, str]:
    """Return (memory_bytes, source) with source in override/quota/host."""
    override_mb = _env_int("STREAM_MEM_LIMIT_MB")
    if override_mb is not None and override_mb > 0:
        return override_mb * 1024 * 1024, "override"
    limit = cgroup_memory_bytes()
    if limit is not None and limit > 0:
        return limit, "quota"
    return 0, "host"


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def declared_vs_visible() -> tuple[str, str]:
    """Compare our declared limits against what the host reports.

    Returns two short strings: (cores, memory). A mismatch is the signal that
    the platform does not expose the container's cgroup — the header then says
    "2/16" rather than pretending the host number is the container's.
    """
    cores, source = effective_cores()
    mem_bytes, mem_source = effective_memory_bytes()
    try:
        from .sysinfo import cpu_info, read_meminfo
        host_logical = cpu_info()[2]
        host_mem_kb = read_meminfo().get("MemTotal", 0)
    except Exception:
        host_logical, host_mem_kb = 0, 0
    if source == "host" or not host_logical or cores == host_logical:
        core_txt = str(cores)
    else:
        core_txt = f"{cores}/{host_logical}"
    if mem_bytes and host_mem_kb:
        mem_gb = mem_bytes / (1024 ** 3)
        host_gb = host_mem_kb / (1024 ** 2)
        mem_txt = f"{mem_gb:.1f}/{host_gb:.1f}" if abs(host_gb - mem_gb) > 0.2 else f"{mem_gb:.1f}"
    elif mem_bytes:
        mem_txt = f"{mem_bytes / (1024 ** 3):.1f}"
    else:
        mem_txt = "?"
    return core_txt, mem_txt
