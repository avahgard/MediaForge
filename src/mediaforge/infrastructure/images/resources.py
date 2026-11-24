"""Read-only Windows pressure observation; no Qt pointers or adapter selection.

GPU counters cover ALL adapters used by this process, conservatively. They are
not DXGI budgets or a per-texture retirement certificate.
"""
import ctypes
import os
import threading
import time


class _Memory(ctypes.Structure):
    _fields_ = [('length', ctypes.c_uint32), ('load', ctypes.c_uint32)] + [
        (n, ctypes.c_uint64) for n in ('total_phys', 'avail_phys', 'total_commit',
                                      'avail_commit', 'total_virtual', 'avail_virtual', 'extended')]


class _Process(ctypes.Structure):
    _fields_ = [('cb', ctypes.c_uint32), ('faults', ctypes.c_uint32)] + [
        (n, ctypes.c_size_t) for n in ('peak_ws', 'ws', 'peak_paged', 'paged',
                                      'peak_nonpaged', 'nonpaged', 'commit', 'peak_commit', 'private')]


class _Value(ctypes.Structure):
    _fields_ = [('status', ctypes.c_uint32), ('value', ctypes.c_int64)]


class _Item(ctypes.Structure):
    _fields_ = [('name', ctypes.c_wchar_p), ('counter', _Value)]


class WindowsPreviewResources:
    """One query, serialized on the single image preparation worker."""
    def __init__(self):
        self._lock = threading.RLock()
        self._query = None
        self._counters = []
        self._baseline = None
        self.last = None

    def _open(self):
        if os.name != 'nt':
            raise ValueError('Native Windows image resource monitoring is unavailable.')
        self._pdh = ctypes.WinDLL('pdh')
        self._pdh.PdhOpenQueryW.argtypes = [ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
        self._pdh.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
        self._pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
        self._pdh.PdhGetFormattedCounterArrayW.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        self._pdh.PdhCloseQuery.argtypes = [ctypes.c_void_p]
        query = ctypes.c_void_p()
        if self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)):
            raise ValueError('Windows GPU pressure monitoring could not start.')
        self._query = query
        try:
            for name in ('Dedicated Usage', 'Shared Usage'):
                counter = ctypes.c_void_p()
                status = self._pdh.PdhAddEnglishCounterW(query, '\\GPU Process Memory(*)\\' + name, 0, ctypes.byref(counter))
                if status:
                    raise ValueError(f'Windows GPU pressure telemetry unavailable (PDH 0x{status & 0xffffffff:08x}). Image preview is unavailable.')
                self._counters.append(counter)
        except BaseException:
            self.close()
            raise

    def sample(self):
        with self._lock:
            if self._query is None:
                self._open()
            status = self._pdh.PdhCollectQueryData(self._query)
            if status:
                raise ValueError(f'Windows GPU pressure sample failed (0x{status & 0xffffffff:08x}).')
            gpu = []
            observed = False
            for counter in self._counters:
                size, count = ctypes.c_uint32(), ctypes.c_uint32()
                status = self._pdh.PdhGetFormattedCounterArrayW(counter, 0x400, ctypes.byref(size), ctypes.byref(count), None)
                if status & 0xffffffff != 0x800007d2 or not 0 < size.value <= 4*1024*1024:
                    raise ValueError('GPU memory counters are missing or exceed the bounded telemetry buffer.')
                buffer = ctypes.create_string_buffer(size.value)
                status = self._pdh.PdhGetFormattedCounterArrayW(counter, 0x400, ctypes.byref(size), ctypes.byref(count), buffer)
                if status:
                    raise ValueError('GPU pressure observation changed during collection. Try again explicitly.')
                if count.value > size.value // ctypes.sizeof(_Item):
                    raise ValueError('Invalid GPU pressure counter array length.')
                values = ctypes.cast(buffer, ctypes.POINTER(_Item))
                total = 0
                for i in range(count.value):
                    row = values[i]
                    if row.name and row.name.startswith(f'pid_{os.getpid()}_'):
                        observed = True
                        if row.counter.status not in (0, 1) or row.counter.value < 0:
                            raise ValueError('Invalid process GPU memory telemetry.')
                        total += row.counter.value
                gpu.append(total)
            if not observed:
                raise ValueError('The initialized Qt process has no GPU pressure telemetry.')
            self.last = self.sample_host() | dict(dedicated=gpu[0], shared=gpu[1])
            return self.last.copy()

    def sample_host(self):
        with self._lock:
            if os.name != 'nt':
                raise ValueError('Native Windows RAM/commit monitoring is unavailable.')
            if not hasattr(self, '_kernel'):
                self._kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                self._ps = ctypes.WinDLL('psapi', use_last_error=True)
                self._kernel.GetCurrentProcess.restype = ctypes.c_void_p
                self._ps.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
            memory = _Memory(); memory.length = ctypes.sizeof(memory)
            process = _Process(); process.cb = ctypes.sizeof(process)
            if not self._kernel.GlobalMemoryStatusEx(ctypes.byref(memory)) or not self._ps.GetProcessMemoryInfo(
                    self._kernel.GetCurrentProcess(), ctypes.byref(process), process.cb):
                raise ValueError('Windows RAM/commit pressure observation failed.')
            if self._baseline is None:
                self._baseline = process.private
            return dict(time=time.monotonic(), available_physical=memory.avail_phys,
                        available_commit=memory.avail_commit, total_physical=memory.total_phys,
                        total_commit=memory.total_commit, private=process.private, working_set=process.ws,
                        image_private_growth=max(0, process.private-self._baseline),
                        peak_private=process.peak_commit, peak_working_set=process.peak_ws)

    def close(self):
        with self._lock:
            if self._query is not None:
                self._pdh.PdhCloseQuery(self._query)
                self._query = None
                self._counters.clear()
