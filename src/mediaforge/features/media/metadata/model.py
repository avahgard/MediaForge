"""Typed values owned by the Media feature."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class MediaInfoFacts:
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
class MediaMetadata:
    source_name: str
    source_path: Path
    format_name: str
    duration_seconds: float | None
    video_codec: str
    width: int
    height: int
    frame_rate: float | None
    navigation_frame_rate: float | None = None
    navigation_frame_count: int | None = None
    navigation_rate_ratio: str | None = None
    navigation_time_base: str | None = None
    navigation_end_us: int | None = None
    info_facts: MediaInfoFacts | None = None
    sample_aspect_ratio: str = "1:1"
    audio_streams: tuple[str, ...] = ()
    audio_only: bool = False
