"""uf5vmjt.lib.services.sysinfo — /proc readers and formatters."""
from __future__ import annotations

import time
from typing import Any

# /proc/cpuinfo and /proc/meminfo's total are immutable for the life of the
# process: the CPU model, core count and RAM total cannot change under it.
# Re-reading them on every fragment tick (2s) is pure waste, so they are
# cached for the process lifetime. The volatile readers below (cpu times,
# free memory) are deliberately NOT cached.
_STATIC_TTL_S = 3600.0
_static_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, loader, ttl: float = _STATIC_TTL_S):
    now = time.monotonic()
    hit = _static_cache.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    value = loader()
    _static_cache[key] = (now, value)
    return value


def clear_cache() -> None:
    """Drop cached /proc reads. Used by tests and after a fork."""
    _static_cache.clear()



def read_meminfo() -> dict[str, int]:
    """Parse /proc/meminfo into {key: kB}. Empty dict when unavailable.

    Not cached: MemFree and Cached change constantly and every panel reads
    them. Only the immutable keys callers actually need (MemTotal) go through
    the static cache, via mem_total_kb().
    """
    out: dict[str, int] = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, val = line.partition(":")
                parts = val.strip().split()
                if not parts:
                    continue
                try:
                    out[key.strip()] = int(parts[0])
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def mem_total_kb() -> int:
    """MemTotal in kB, cached: it cannot change while the process runs."""
    def load() -> int:
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1])
        except (OSError, ValueError, IndexError):
            pass
        return 0
    return int(_cached("mem_total", load))


def memory_segments(mem: dict[str, int]) -> tuple[int, int, int, int]:
    """Return (total, used, cache, free) in kB.

    cache = Cached + Buffers + SReclaimable (reclaimable page cache).
    used  = total - free - cache, where free = MemFree.
    """
    total = mem.get("MemTotal", 0)
    free = mem.get("MemFree", 0)
    cache = mem.get("Cached", 0) + mem.get("Buffers", 0) + mem.get("SReclaimable", 0)
    used = max(total - free - cache, 0)
    return total, used, cache, free


def read_cpu_times() -> dict[str, tuple[int, int]]:
    """Return {cpuN: (total_jiffies, idle_jiffies)} from /proc/stat."""
    out: dict[str, tuple[int, int]] = {}
    try:
        with open("/proc/stat") as f:
            for line in f:
                if not line.startswith("cpu"):
                    continue
                parts = line.split()
                name = parts[0]
                if name == "cpu":
                    continue  # aggregate handled separately
                try:
                    nums = [int(x) for x in parts[1:]]
                except ValueError:
                    continue
                total = sum(nums)
                idle = (nums[3] if len(nums) > 3 else 0) + (nums[4] if len(nums) > 4 else 0)
                out[name] = (total, idle)
    except OSError:
        pass
    return out


def read_cpu_total() -> tuple[int, int]:
    """Return (total_jiffies, idle_jiffies) for the aggregate cpu line."""
    try:
        with open("/proc/stat") as f:
            for line in f:
                parts = line.split()
                if parts and parts[0] == "cpu":
                    nums = [int(x) for x in parts[1:]]
                    total = sum(nums)
                    idle = (nums[3] if len(nums) > 3 else 0) + (nums[4] if len(nums) > 4 else 0)
                    return total, idle
    except (OSError, ValueError):
        pass
    return 0, 0


def cpu_info() -> tuple[str, int, int]:
    """Return (model_name, physical_cores, logical_cores) from /proc/cpuinfo.

    Cached for the process lifetime: the model string and the core topology
    cannot change under a running process, and this was being re-parsed on
    every 2-second fragment tick.
    """
    def load() -> tuple[str, int, int]:
        model = ""
        physical: set[str] = set()
        logical = 0
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        if not model:
                            model = line.partition(":")[2].strip()
                    elif line.startswith("processor"):
                        logical += 1
                    elif line.startswith("core id"):
                        physical.add(line.partition(":")[2].strip())
        except OSError:
            pass
        return model or "unknown", len(physical) or logical or 1, logical or 1
    return _cached("cpu_info", load)


def fmt_mb(kb: int) -> str:
    return f"{kb / 1024:.0f} MB"


def fmt_gb(num_bytes: int) -> str:
    gb = num_bytes / (1024 ** 3)
    if gb >= 100:
        return f"{gb:.0f} GB"
    if gb >= 1:
        return f"{gb:.1f} GB"
    return f"{num_bytes / (1024 ** 2):.0f} MB"


