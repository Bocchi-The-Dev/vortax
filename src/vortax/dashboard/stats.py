# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Host and per-VM resource stats from /proc, sampled in a background thread."""

from __future__ import annotations

import os
import shutil
import threading
import time

from .. import paths


def _read_cpu() -> tuple[int, int] | None:
    try:
        with open("/proc/stat", encoding="ascii") as fh:
            parts = fh.readline().split()[1:]
        vals = [int(x) for x in parts]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return sum(vals), idle
    except (OSError, ValueError, IndexError):
        return None


def _meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                out[key] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return out


def _proc_times(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            data = fh.read()
        fields = data[data.rindex(")") + 2:].split()
        return int(fields[11]) + int(fields[12])        # utime + stime (clock ticks)
    except (OSError, ValueError, IndexError):
        return None


def _proc_rss(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/statm", encoding="ascii") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return 0


class Sampler(threading.Thread):
    def __init__(self, interval: float = 2.0):
        super().__init__(daemon=True)
        self.interval = interval
        self._lock = threading.Lock()
        self._cpu_pct = 0.0
        self._prev = _read_cpu()
        self._vm_prev: dict[int, tuple[int, float]] = {}
        self._vm_cpu: dict[int, float] = {}
        self._pids: set[int] = set()
        self._ticks = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

    def watch(self, pids: list[int]) -> None:
        with self._lock:
            self._pids = set(pids)

    def run(self) -> None:
        while True:
            time.sleep(self.interval)
            self.sample()

    def sample(self) -> None:
        cur = _read_cpu()
        with self._lock:
            if cur and self._prev:
                dt, di = cur[0] - self._prev[0], cur[1] - self._prev[1]
                self._cpu_pct = round(100.0 * (dt - di) / dt, 1) if dt > 0 else 0.0
            self._prev = cur
            now = time.monotonic()
            fresh: dict[int, tuple[int, float]] = {}
            for pid in self._pids:
                t = _proc_times(pid)
                if t is None:
                    continue
                fresh[pid] = (t, now)
                if pid in self._vm_prev:
                    pt, pn = self._vm_prev[pid]
                    elapsed = now - pn
                    if elapsed > 0:
                        self._vm_cpu[pid] = round(100.0 * ((t - pt) / self._ticks) / elapsed, 1)
            self._vm_prev = fresh
            self._vm_cpu = {p: v for p, v in self._vm_cpu.items() if p in fresh}

    def snapshot(self) -> dict:
        mem = _meminfo()
        total = mem.get("MemTotal", 0)
        avail = mem.get("MemAvailable", mem.get("MemFree", 0))
        used = max(0, total - avail)
        data = paths.data_dir()
        probe = data
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            du = shutil.disk_usage(probe)
            disk = {"total": du.total, "free": du.free, "used": du.used}
        except OSError:
            disk = {"total": 0, "free": 0, "used": 0}
        try:
            load = list(os.getloadavg())
        except (OSError, AttributeError):
            load = []
        with self._lock:
            vm_cpu = dict(self._vm_cpu)
            cpu = self._cpu_pct
        return {
            "cpu_pct": cpu,
            "cpus": os.cpu_count() or 1,
            "ram_total": total, "ram_used": used,
            "ram_pct": round(100.0 * used / total, 1) if total else 0.0,
            "disk": disk,
            "load": load,
            "time": time.strftime("%H:%M:%S"),
            "vm": {str(pid): {"cpu_pct": vm_cpu.get(pid, 0.0), "rss": _proc_rss(pid)} for pid in self._pids},
        }
