"""Exact coded-frame Resize contracts, independent of Qt and FFmpeg."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mediaforge.features.media.reformat import ReformatPlan

from mediaforge.features.media.processing_control import ProcessingControl


MIN_RESIZE_AXIS = 16
MAX_RESIZE_AXIS = 7680
MAX_RESIZE_PIXELS = 33_177_600


class ResizeErrorCode(str, Enum):
    BUSY = "media_processing_busy"
    NO_SOURCE = "no_source"
    INVALID_DIMENSIONS = "invalid_dimensions"
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


class ResizeError(RuntimeError):
    def __init__(self, code: ResizeErrorCode, message: str = ""):
        super().__init__(message or code.value)
        self.code = code


@dataclass(frozen=True)
class ResizeDimensions:
    width: int
    height: int

    def validate(self) -> None:
        if (type(self.width) is not int or type(self.height) is not int
                or self.width < MIN_RESIZE_AXIS or self.height < MIN_RESIZE_AXIS
                or self.width > MAX_RESIZE_AXIS or self.height > MAX_RESIZE_AXIS
                or self.width % 2 or self.height % 2
                or self.width * self.height > MAX_RESIZE_PIXELS):
            raise ResizeError(ResizeErrorCode.INVALID_DIMENSIONS)


@dataclass(frozen=True)
class ResizeRequest:
    source: Path
    dimensions: ResizeDimensions
    source_width: int
    source_height: int
    reformat: "ReformatPlan | None" = None


@dataclass(frozen=True)
class ResizeResult:
    output: Path
    width: int
    height: int


class ResizeControl(ProcessingControl):
    def __init__(self):
        super().__init__(lambda: ResizeError(ResizeErrorCode.CANCELLED))


class ResizeExecutor(Protocol):
    def execute(self, request: ResizeRequest, control: ResizeControl) -> ResizeResult: ...
