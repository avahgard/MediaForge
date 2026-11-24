"""One owned local observation at a time; no per-key unbounded work queue."""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import logging
from mediaforge.application.contracts.frame_step import LocalProbeControl, LocalAdjacencyProbe


class AdjacentFrameService:
    def __init__(self, probe: LocalAdjacencyProbe):
        self._probe = probe
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='local-frame-proof')
        self._lock = Lock()
        self._current = None
        self._closed = False
        self._idle_callback = None

    def start(self, request, completion) -> bool:
        with self._lock:
            if self._closed or self._current is not None:
                return False
            control = LocalProbeControl()
            self._current = control
            future = self._executor.submit(self._probe.resolve, request, control)
        def finished(job):
            try:
                result = job.result()
            except Exception:
                logging.getLogger('mediaforge').exception('Bounded local frame observation failed')
                result = None
            with self._lock:
                if self._current is control and not control.owns_process:
                    self._current = None
                idle, self._idle_callback = self._idle_callback, None
            if idle is not None and not control.owns_process:
                idle()
            if not control.cancelled.is_set():
                completion(result)
        future.add_done_callback(finished)
        return True

    def when_idle(self, callback):
        """One replacement observation may wait for cancelled ownership to reap."""
        with self._lock:
            if self._closed:
                return
            if self._current is not None:
                self._idle_callback = callback
                return
        callback()

    def cancel(self):
        with self._lock:
            current = self._current
        if current is not None:
            # Source replacement never blocks the GUI waiting for a child.
            # The worker observes cancellation and owns bounded stop/reap.
            current.request_cancel()

    def shutdown(self):
        with self._lock:
            self._closed = True
        self.cancel()
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            current = self._current
        if current is not None and current.owns_process:
            current.cancel()
            raise RuntimeError('Local frame probe cleanup failed; ownership retained')
