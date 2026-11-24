"""Qt-independent Resize API for Presentation."""

from dataclasses import dataclass
from typing import Protocol

from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.features.media.resize import (
    MAX_RESIZE_AXIS as RESIZE_MAX_AXIS,
    MAX_RESIZE_PIXELS as RESIZE_MAX_PIXELS,
    MIN_RESIZE_AXIS as RESIZE_MIN_AXIS,
)


@dataclass(frozen=True)
class ResizeJob:
    job_id: int = 0
    operation: str = "resize"
    state: str = "Pending"
    stage: str = ""
    progress: float | None = None
    message: str = ""
    result: str = ""
    width: int = 0
    height: int = 0
    error: str = ""


class ResizeUseCase(Protocol):
    def set_source(self, source: MediaMetadataDto | None) -> bool: ...
    def start(self, width: int, height: int) -> bool: ...
    def start_reformat(self, width: int, height: int, placement: str) -> bool: ...
    def cancel(self) -> bool: ...
    def snapshot(self) -> ResizeJob: ...
    def shutdown(self) -> None: ...
    @property
    def busy(self) -> bool: ...
