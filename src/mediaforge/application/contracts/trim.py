"""Stable, Qt-independent Trim API for Presentation."""

from dataclasses import dataclass
from typing import Protocol

from mediaforge.application.contracts.media import MediaMetadataDto


@dataclass(frozen=True)
class TrimJob:
    job_id: int = 0
    operation: str = "trim"
    state: str = "Pending"
    stage: str = ""
    progress: float | None = None
    message: str = ""
    result: str = ""
    error: str = ""


class TrimUseCase(Protocol):
    def set_source(self, source: MediaMetadataDto | None) -> bool: ...
    def start(self, start: float, end: float) -> bool: ...
    def cancel(self) -> bool: ...
    def snapshot(self) -> TrimJob: ...
    def shutdown(self) -> None: ...
    @property
    def busy(self) -> bool: ...
