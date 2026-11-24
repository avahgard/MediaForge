"""Stable Application results exposed to Presentation."""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from mediaforge.application.contracts.image_source import VideoSource, AudioSource, StillSource, SequenceSource


@dataclass(frozen=True, slots=True)
class ActiveSourceRef:
    """Selected physical original; never an edit recipe or a Viewer proxy.

    The stat signature detects ordinary changes. Dub still authenticates the
    protected file hash when inspecting and before processing.
    """
    item_id: str
    path: str
    kind: str
    signature: tuple[int, int, int, int]

    @property
    def physical_key(self):
        return (self.path, self.kind, self.signature)


class OpenMediaErrorCode(str, Enum):
    INVALID_SOURCE = "invalid_source"
    FFPROBE_UNAVAILABLE = "ffprobe_unavailable"
    PROBE_FAILED = "probe_failed"
    INVALID_PROBE_OUTPUT = "invalid_probe_output"
    NO_VIDEO_STREAM = "no_video_stream"
    PROBE_TIMEOUT = "probe_timeout"
    PROBE_ABORTED = "probe_aborted"


@dataclass(frozen=True, slots=True)
class MediaInfoFactsDto:
    container_long_name: str | None = None
    video_codec_long_name: str | None = None
    video_profile: str | None = None
    video_tag: str | None = None
    pixel_format: str | None = None
    bit_depth: int | None = None
    frame_rate_ratio: str | None = None
    audio_codec: str | None = None
    audio_codec_long_name: str | None = None
    audio_sample_rate: int | None = None
    audio_channels: int | None = None
    audio_layout: str | None = None
    frame_rate_basis: str | None = None


@dataclass(frozen=True, slots=True)
class MediaMetadataDto:
    source_name: str
    source_path: str
    format_name: str
    duration_seconds: float | None
    video_codec: str
    width: int
    height: int
    frame_rate: float | None
    # Stream eligibility and bounded prefix evidence; each step also needs local adjacency proof.
    navigation_frame_rate: float | None = None
    navigation_frame_count: int | None = None
    navigation_rate_ratio: str | None = None
    navigation_time_base: str | None = None
    navigation_end_us: int | None = None
    info_facts: MediaInfoFactsDto | None = None
    sample_aspect_ratio: str = "1:1"
    audio_streams: tuple[str, ...] = ()
    source: VideoSource | StillSource | SequenceSource | None = None
    audio_only: bool = False

    def __post_init__(self):
        if self.source is None:
            object.__setattr__(self, 'source', (AudioSource if self.audio_only else VideoSource)(self.source_path))


@dataclass(frozen=True, slots=True)
class OpenMediaResult:
    metadata: MediaMetadataDto | None = None
    error_code: OpenMediaErrorCode | None = None
    error_message: str = ""
    guard: object = None

    @property
    def succeeded(self) -> bool:
        return self.metadata is not None and self.error_code is None


class OpenMediaUseCase(Protocol):
    def open_media(self, source_path: str) -> OpenMediaResult:
        """Open and probe one local path without any Qt-specific values."""

    def cancel(self) -> None:
        """Cancel only the current metadata operation."""

    def open_exported_result(self, result,cancel=None) -> OpenMediaResult:
        """Revalidate a completed video result before ordinary activation."""

