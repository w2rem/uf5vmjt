"""uf5vmjt.lib.services.sysinfo — /proc readers and formatters."""
from __future__ import annotations



def read_meminfo() -> dict[str, int]:
    """Parse /proc/meminfo into {key: kB}. Empty dict when unavailable."""
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
    """Return (model_name, physical_cores, logical_cores) from /proc/cpuinfo."""
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


def fmt_mb(kb: int) -> str:
    return f"{kb / 1024:.0f} MB"


def fmt_gb(num_bytes: int) -> str:
    gb = num_bytes / (1024 ** 3)
    if gb >= 100:
        return f"{gb:.0f} GB"
    if gb >= 1:
        return f"{gb:.1f} GB"
    return f"{num_bytes / (1024 ** 2):.0f} MB"


