"""Small, cached host resource samples; no subprocesses or blocking CPU probes."""
import os
from pathlib import Path
import shutil
import threading
import time


def host_counters():
    """Return cumulative (total, idle) CPU time and physical (total, available) RAM."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes

        class Memory(ctypes.Structure):
            _fields_ = [('length', wintypes.DWORD), ('load', wintypes.DWORD)] + [
                (name, ctypes.c_ulonglong) for name in
                ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]

        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        memory = Memory()
        memory.length = ctypes.sizeof(memory)
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            raise OSError('CPU counters unavailable')
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            raise OSError('Memory counters unavailable')
        ticks = lambda value: (value.dwHighDateTime << 32) | value.dwLowDateTime
        return (ticks(kernel) + ticks(user), ticks(idle)), (memory.total, memory.available)
    # Linux /proc exposes host-wide counters in the standard Pi Docker deployment.
    fields = Path('/proc/stat').read_text().splitlines()[0].split()
    if fields[0] != 'cpu' or len(fields) < 5:
        raise ValueError('CPU counters unavailable')
    values = [int(value) for value in fields[1:9]]  # guest times are already included in user/nice
    cpu = sum(values), values[3] + (values[4] if len(values) > 4 else 0)
    memory = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        if key in ('MemTotal', 'MemAvailable'):
            memory[key] = int(value.split()[0]) * 1024
    return cpu, (memory['MemTotal'], memory['MemAvailable'])


class SystemMonitor:
    def __init__(self, data_directory, counters=host_counters, disk=shutil.disk_usage,
                 clock=time.monotonic, wall_clock=time.time):
        self.directory = data_directory
        self.counters, self.disk, self.clock, self.wall_clock = counters, disk, clock, wall_clock
        self.lock = threading.Lock()
        self.previous = None
        self.cached = None
        self.sampled = None
        self.snapshot()  # Prime CPU counters without waiting for a measurement interval.

    def snapshot(self):
        with self.lock:
            now = self.clock()
            if self.cached is not None and now - self.sampled < 5:
                return self.cached
            cpu_percent = None
            memory = None
            storage = None
            try:
                cpu, (total, available) = self.counters()
                if self.previous is not None:
                    delta = cpu[0] - self.previous[0]
                    idle = cpu[1] - self.previous[1]
                    if delta > 0 and 0 <= idle <= delta:
                        cpu_percent = round(100 * (delta - idle) / delta, 1)
                self.previous = cpu
                if total > 0 and 0 <= available <= total:
                    memory = dict(total_bytes=total, used_bytes=total-available,
                                  available_bytes=available, percent=round(100*(total-available)/total, 1))
            except (OSError, ValueError, KeyError, IndexError, AttributeError):
                self.previous = None
            try:
                usage = self.disk(self.directory)
                if usage.total > 0:
                    storage = dict(total_bytes=usage.total, used_bytes=usage.used,
                                   available_bytes=usage.free, percent=round(100*usage.used/usage.total, 1))
            except (OSError, ValueError):
                pass
            self.cached = dict(sampled_at=self.wall_clock(), cpu_percent=cpu_percent,
                               memory=memory, storage=storage)
            self.sampled = now
            return self.cached
