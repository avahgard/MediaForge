"""One shared admission gate for Trim, Crop and Resize; no scheduling."""

from threading import Lock


class MediaProcessingGate:
    def __init__(self):
        self._lock = Lock()
        self._owner = None
        self._failure = ""
        self._external_resources = []

    def quarantine(self, resource: object, reason: str) -> None:
        """Retain an unresolved preview owner even outside an export job.

        Does not transfer or release any already-running operation's token.
        Subsequent release fails closed, just as for poison(owner, reason).
        """
        if resource is None or not reason:raise RuntimeError('Invalid retained resource')
        with self._lock:
            self._external_resources.append(resource)
            self._failure=reason
            if self._owner is None:self._owner=object()

    def poison(self, owner: object, reason: str) -> None:
        """Retain unresolved ownership for this application lifetime.

        No background recovery is attempted. Only a new application instance
        has fresh admission; callers must report failed shutdown in this one.
        """
        with self._lock:
            if owner is not self._owner or owner is None or not reason:
                raise RuntimeError("Invalid unresolved Media processing owner")
            self._failure = reason

    @property
    def failure(self) -> str:
        with self._lock:
            return self._failure

    def acquire(self) -> object | None:
        with self._lock:
            if self._owner is not None:
                return None
            self._owner = object()
            return self._owner

    def release(self, owner: object) -> None:
        with self._lock:
            if owner is not self._owner or owner is None:
                raise RuntimeError("Media processing gate released by non-owner")
            if self._failure:
                raise RuntimeError("Cannot release unresolved Media processing ownership")
            self._owner = None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._owner is not None

    def owned_by(self, owner: object) -> bool:
        """Trusted orchestration can lend its SAME token to a nested adapter."""
        with self._lock:
            return owner is not None and self._owner is owner and not self._failure
