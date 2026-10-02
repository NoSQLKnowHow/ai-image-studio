"""Memory figures for the Spark's unified memory (DESIGN.md §9a).

The GB10 has no separate VRAM figure, so NVML (nvidia-smi) memory numbers are unreliable.
System memory from /proc/meminfo is what actually limits whether the model fits. In a
container /proc/meminfo shows the host's memory, which is what we want next to Hermes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

MEMINFO = Path("/proc/meminfo")
GIB = 1024**3

MEMORY_HINT = (
    "Free memory on the Spark: Hermes' LLM server (vLLM) may be holding most of it, so lower its "
    "--gpu-memory-utilization or stop it. Then flush the page cache on the host: "
    "sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'"
)


def _kib_fields(path: Path, wanted: set[str]) -> dict[str, int]:
    values: dict[str, int] = {}
    with open(path, encoding="ascii", errors="replace") as fh:
        for line in fh:
            key, _, rest = line.partition(":")
            if key in wanted:
                values[key] = int(rest.split()[0])
    return values


def memory() -> Optional[dict[str, float]]:
    """{'total_gb', 'available_gb'} in GiB (as `free -g` counts), or None if unreadable."""
    try:
        fields = _kib_fields(MEMINFO, {"MemTotal", "MemAvailable"})
        return {
            "total_gb": round(fields["MemTotal"] * 1024 / GIB, 1),
            "available_gb": round(fields["MemAvailable"] * 1024 / GIB, 1),
        }
    except (OSError, KeyError, ValueError, IndexError):
        return None


def process_rss_gb(pid: Optional[int]) -> Optional[float]:
    """Resident memory of a process. On unified memory, GPU allocations may not all show here."""
    if pid is None:
        return None
    try:
        fields = _kib_fields(Path(f"/proc/{pid}/status"), {"VmRSS"})
        return round(fields["VmRSS"] * 1024 / GIB, 1)
    except (OSError, KeyError, ValueError, IndexError):
        return None
