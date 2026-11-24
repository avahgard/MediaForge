"""Coded-frame Crop geometry and execution port, independent of Qt and FFmpeg."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from mediaforge.features.media.processing_control import ProcessingControl


class CropErrorCode(str, Enum):
    BUSY = "media_processing_busy"
    NO_SOURCE = "no_source"
    INVALID_RECTANGLE = "invalid_rectangle"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_CHANGED = "source_changed"
    UNSUPPORTED_CONTAINER = "unsupported_container"
    UNSUPPORTED_ORIENTATION = "unsupported_orientation"
    PREFLIGHT_FAILED = "preflight_failed"
    ENCODER_UNAVAILABLE = "encoder_unavailable"
    OUTPUT_UNAVAILABLE = "output_unavailable"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    START_FAILED = "start_failed"
    PROCESS_FAILED = "process_failed"
    TIMEOUT = "timeout"
    LIFECYCLE_FAILED = "lifecycle_failed"
    INVALID_OUTPUT = "invalid_output"
    VALIDATION_FAILED = "validation_failed"
    PUBLICATION_FAILED = "publication_failed"
    CANCELLED = "cancelled"


class CropError(RuntimeError):
    def __init__(self, code: CropErrorCode, message: str = ""):
        super().__init__(message or code.value)
        self.code = code


@dataclass(frozen=True)
class CropRect:
    x: int
    y: int
    width: int
    height: int

    def validate(self, source_width: int, source_height: int) -> None:
        values = (self.x, self.y, self.width, self.height, source_width, source_height)
        if any(type(value) is not int for value in values):
            raise CropError(CropErrorCode.INVALID_RECTANGLE)
        if (self.x < 0 or self.y < 0 or self.width < 16 or self.height < 16
                or any(value % 2 for value in values[:4])
                or self.x + self.width > source_width
                or self.y + self.height > source_height):
            raise CropError(CropErrorCode.INVALID_RECTANGLE)


@dataclass(frozen=True)
class CropRequest:
    source: Path
    rectangle: CropRect
    source_width: int
    source_height: int


@dataclass(frozen=True)
class CropResult:
    output: Path
    width: int
    height: int


class CropControl(ProcessingControl):
    def __init__(self):
        super().__init__(lambda: CropError(CropErrorCode.CANCELLED))


class CropExecutor(Protocol):
    def execute(self, request: CropRequest, control: CropControl) -> CropResult: ...
