"""Low-overhead process and host resource sampling for the diagnostics page."""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable


class ResourceMonitor:
    """Sample current resource use when requested; no polling thread or history buffer."""

    def __init__(
        self,
        proc_root: Path = Path("/proc"),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._proc_root = proc_root
        self._clock = clock
        self._lock = threading.Lock()
        self._previous_at: float | None = None
        self._previous_process_cpu: float | None = None
        self._previous_system_cpu: tuple[int, int] | None = None

    def sample(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            times = os.times()
            process_cpu = times.user + times.system
            system_cpu = self._system_cpu()
            elapsed = now - self._previous_at if self._previous_at is not None else 0.0
            process_percent = None
            if elapsed > 0 and self._previous_process_cpu is not None:
                process_percent = round(
                    (process_cpu - self._previous_process_cpu) * 100 / elapsed, 1
                )
            host_percent = None
            if elapsed > 0 and system_cpu is not None and self._previous_system_cpu is not None:
                total_delta = system_cpu[0] - self._previous_system_cpu[0]
                idle_delta = system_cpu[1] - self._previous_system_cpu[1]
                if total_delta > 0:
                    host_percent = round((total_delta - idle_delta) * 100 / total_delta, 1)

            self._previous_at = now
            self._previous_process_cpu = process_cpu
            self._previous_system_cpu = system_cpu

            memory_total, memory_available = self._memory()
            try:
                disk = shutil.disk_usage("/")
                disk_total, disk_used = disk.total, disk.used
            except OSError:
                disk_total = disk_used = None
            try:
                load_1m = round(os.getloadavg()[0], 2)
            except (AttributeError, OSError):
                load_1m = None

            return {
                "process": {
                    "pid": os.getpid(),
                    "cpu_percent": process_percent,
                    "rss_bytes": self._resident_bytes(),
                    "threads": self._thread_count(),
                },
                "host": {
                    "cpu_percent": host_percent,
                    "memory_used_bytes": memory_total - memory_available
                    if memory_total is not None and memory_available is not None else None,
                    "memory_total_bytes": memory_total,
                    "root_used_bytes": disk_used,
                    "root_total_bytes": disk_total,
                    "load_1m": load_1m,
                },
            }

    def _system_cpu(self) -> tuple[int, int] | None:
        try:
            line = next(
                line
                for line in (self._proc_root / "stat").read_text().splitlines()
                if line.startswith("cpu ")
            )
            values = [int(value) for value in line.split()[1:9]]
        except (OSError, StopIteration, ValueError):
            return None
        if len(values) < 4:
            return None
        return sum(values), values[3] + (values[4] if len(values) > 4 else 0)

    def _memory(self) -> tuple[int | None, int | None]:
        values: dict[str, int] = {}
        try:
            for line in (self._proc_root / "meminfo").read_text().splitlines():
                key, raw = line.split(":", 1)
                values[key] = int(raw.strip().split()[0]) * 1024
        except (OSError, ValueError, IndexError):
            return None, None
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if available is None:
            available = sum(values.get(key, 0) for key in ("MemFree", "Buffers", "Cached"))
        return total, available if total is not None else None

    def _resident_bytes(self) -> int | None:
        try:
            resident_pages = int((self._proc_root / "self/statm").read_text().split()[1])
            return resident_pages * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError, AttributeError):
            return None

    def _thread_count(self) -> int | None:
        try:
            return sum(1 for _ in (self._proc_root / "self/task").iterdir())
        except OSError:
            return None