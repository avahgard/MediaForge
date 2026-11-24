"""Bounded local timing observation, independent of Qt and UI structure."""
from dataclasses import dataclass
from threading import Lock, Event
from typing import Protocol, Callable


@dataclass(frozen=True)
class AdjacentFrameRequest:
    source_path: str
    rate_ratio: str
    time_base: str
    frame_count: int
    current_start_us: int
    direction: int
    source_end_us: int | None = None


@dataclass(frozen=True)
class AdjacentFrameResult:
    direction: int
    start_us: int
    end_us: int
    seek_ms: int
    observations: int
    previous_us: int | None = None
    next_us: int | None = None


class LocalProbeControl:
    """Cancellation owns spawn admission and the registered process until reaped."""
    def __init__(self):
        self.cancelled = Event()
        self._lock = Lock()
        self._stop_lock = Lock()
        self._process = None

    def spawn(self, factory: Callable, command: list[str], **options):
        with self._lock:
            if self.cancelled.is_set():
                raise RuntimeError('Local frame probe cancelled before spawn')
            self._process = factory(command, **options)
            return self._process

    def stop(self, process):
        # Infrastructure passes the concrete owned process; no process discovery.
        import subprocess
        with self._stop_lock:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)

    def release(self, process):
        with self._lock:
            if self._process is process:
                if process.poll() is None:
                    raise RuntimeError('Local probe still owns a live process')
                self._process = None

    def cancel(self):
        self.request_cancel()
        with self._lock:
            process = self._process
        if process is not None:
            self.stop(process)

    def request_cancel(self):
        self.cancelled.set()

    @property
    def owns_process(self):
        with self._lock:
            return self._process is not None


class LocalAdjacencyProbe(Protocol):
    def resolve(self, request: AdjacentFrameRequest, control: LocalProbeControl) -> AdjacentFrameResult | None: ...


class AdjacentFrameUseCase(Protocol):
    def start(self, request: AdjacentFrameRequest, completion: Callable[[AdjacentFrameResult | None], None]) -> bool: ...
    def cancel(self) -> None: ...
    def when_idle(self, callback: Callable[[], None]) -> None: ...
    def shutdown(self) -> None: ...
