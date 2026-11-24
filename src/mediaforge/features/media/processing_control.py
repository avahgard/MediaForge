"""Media cancellation admission and atomic publication ordering."""

from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Callable


class ProcessingControl:
    """Per-operation cancellation, linearized against activity start and publication.

    The publication action must be one atomic non-replacing operation. A cancel
    accepted before it acquires the gate prevents publication; after commit it
    is too late to cancel a completed output. Never reuse this token.
    """

    def __init__(self, cancelled_error):
        self._cancelled_error = cancelled_error
        self._gate = Lock()
        self._cancelled = False
        self._published = False
        self._cleanup_failed = False

    def retain_unreaped_child(self) -> None:
        """Infrastructure could not prove child termination; admission stays unsafe."""
        with self._gate:
            self._cleanup_failed = True

    @property
    def cleanup_failed(self) -> bool:
        with self._gate:
            return self._cleanup_failed

    @property
    def published(self) -> bool:
        with self._gate:return self._published

    def cancel(self) -> bool:
        with self._gate:
            if self._published or self._cancelled:
                return False
            self._cancelled = True
            return True

    def check(self) -> None:
        with self._gate:
            if self._cancelled:
                raise self._cancelled_error()

    @contextmanager
    def starting(self):
        """Admit an activity and establish its ownership before accepting Cancel.

        Infrastructure creates and owns resources inside this scope. Feature
        only serializes admission; resource lifecycle remains in Infrastructure.
        """
        with self._gate:
            if self._cancelled:
                raise self._cancelled_error()
            yield

    def publish(self, action: Callable[[], Path]) -> Path:
        with self._gate:
            if self._cancelled:
                raise self._cancelled_error()
            result = action()
            self._published = True
            return result
