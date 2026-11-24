"""Qt-independent Crop API for Presentation."""

from dataclasses import dataclass
from typing import Protocol

from mediaforge.application.contracts.media import MediaMetadataDto


@dataclass(frozen=True)
class CropJob:
    job_id: int = 0
    operation: str = "crop"
    state: str = "Pending"
    stage: str = ""
    progress: float | None = None
    message: str = ""
    result: str = ""
    width: int = 0
    height: int = 0
    error: str = ""


class CropUseCase(Protocol):
    def set_source(self, source: MediaMetadataDto | None) -> bool: ...
    def start(self, x: int, y: int, width: int, height: int) -> bool: ...
    def cancel(self) -> bool: ...
    def snapshot(self) -> CropJob: ...
    def shutdown(self) -> None: ...
    @property
    def busy(self) -> bool: ...
