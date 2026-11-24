"""Narrow stream-copy Trim semantics and execution port (no Qt/runtime layout)."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from mediaforge.features.media.processing_control import ProcessingControl


@dataclass(frozen=True)
class TrimRange:
    start: float
    end: float


@dataclass(frozen=True)
class TrimRequest:
    source: Path
    interval: TrimRange


@dataclass(frozen=True)
class TrimResult:
    output: Path


class TrimErrorCode(str, Enum):
    BUSY = "media_processing_busy"
    NO_SOURCE = "no_source"
    INVALID_RANGE = "invalid_range"
    SOURCE_UNAVAILABLE = "source_unavailable"
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


class TrimError(RuntimeError):
    def __init__(self, code: TrimErrorCode, message: str = ""):
        super().__init__(message or code.value)
        self.code = code


class TrimControl(ProcessingControl):
    def __init__(self):
        super().__init__(lambda: TrimError(TrimErrorCode.CANCELLED))


class TrimExecutor(Protocol):
    def execute(self, request: TrimRequest, control: TrimControl) -> TrimResult:
        """Stream copy, validate, publish or raise a controlled TrimError."""
