"""Feature-owned port for obtaining metadata from a media source."""

from enum import Enum
from pathlib import Path
from typing import Protocol

from mediaforge.features.media.metadata.model import MediaMetadata


class MediaProbeErrorCode(str, Enum):
    UNAVAILABLE = "unavailable"
    PROCESS_FAILED = "process_failed"
    INVALID_OUTPUT = "invalid_output"
    NO_VIDEO_STREAM = "no_video_stream"
    TIMEOUT = "timeout"
    ABORTED = "aborted"


class MediaProbeError(RuntimeError):
    def __init__(self, code: MediaProbeErrorCode, message: str):
        super().__init__(message)
        self.code = code


class MediaMetadataProbe(Protocol):
    def probe(self, source: Path) -> MediaMetadata:
        """Return metadata or raise a controlled MediaProbeError."""

    def cancel(self) -> None:
        """Stop the current metadata probe, if one is running."""

