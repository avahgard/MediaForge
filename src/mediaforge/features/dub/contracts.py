"""Immutable source facts and planning contracts. A plan never certifies an output."""

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from pathlib import Path

from mediaforge.features.media.processing_control import ProcessingControl

SOURCE_LANGUAGE = 'en'
TARGET_LANGUAGE = 'ru'


class Reason(str, Enum):
    INVALID_SOURCE = 'invalid_source'
    UNSUPPORTED_SOURCE = 'unsupported_source'
    NO_AUDIO = 'no_audio'
    EMPTY_AUDIO = 'empty_audio'
    AMBIGUOUS_TRACK = 'ambiguous_track'
    INVALID_TRACK = 'invalid_track'
    INVALID_TIMING = 'invalid_timing'
    UNQUALIFIED_COPY = 'unqualified_copy'
    EXTRA_STREAMS = 'extra_streams'
    UNSAFE_PATH = 'unsafe_path'
    SOURCE_BUSY = 'source_busy'
    SOURCE_CHANGED = 'source_changed'
    DESTINATION_EXISTS = 'destination_exists'
    SOURCE_ALIAS = 'source_alias'
    RUNTIME_MISSING = 'runtime_missing'
    PROBE_FAILED = 'probe_failed'
    TIMEOUT = 'timeout'
    CANCELLED = 'cancelled'
    LIFECYCLE_FAILED = 'lifecycle_failed'


@dataclass(frozen=True)
class Refusal:
    code: Reason
    message: str


class DubError(RuntimeError):
    def __init__(self, code: Reason, message: str, *, retained_resources=()):
        self.code = code
        self.retained_resources = tuple(retained_resources)
        super().__init__(message)


class DubControl(ProcessingControl):
    def __init__(self):
        super().__init__(lambda: DubError(Reason.CANCELLED, 'Dub inspection cancelled.'))
        self.retained_resources = []

    def retain(self, resource):
        self.retained_resources.append(resource)
        self.retain_unreaped_child()


@dataclass(frozen=True)
class SourceIdentity:
    path: Path
    device: int
    file_id: int
    size: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class ClockFacts:
    time_base: Fraction
    first: Fraction
    end: Fraction
    records: int
    interval_sha256: str
    samples: int | None = None

    @property
    def duration(self) -> Fraction:
        return self.end - self.first


@dataclass(frozen=True)
class AudioTrack:
    index: int
    codec: str
    sample_rate: int
    channels: int
    layout: str
    language: str | None
    title: str | None
    default: bool
    time_base: Fraction
    declared_start: Fraction | None
    declared_duration: Fraction | None
    clock: ClockFacts | None = None
    refusal: Refusal | None = None


@dataclass(frozen=True)
class VideoFacts:
    index: int
    codec: str
    profile: str
    pixel_format: str
    width: int
    height: int
    sar: Fraction
    frame_rate: Fraction | None
    time_base: Fraction
    field_order: str
    color: tuple[str, str, str, str]
    has_side_data: bool
    clock: ClockFacts | None = None
    refusal: Refusal | None = None
    packet_clock: 'PacketClock | None' = None


@dataclass(frozen=True)
class PacketClock:
    time_base: Fraction
    packets: int
    first_dts: Fraction
    last_dts: Fraction
    ledger_sha256: str


class SourceKind(str, Enum):
    VIDEO = 'video'
    AUDIO = 'audio'


@dataclass(frozen=True)
class DubSourceDescriptor:
    identity: SourceIdentity
    kind: SourceKind
    container: str
    extension: str
    audio_tracks: tuple[AudioTrack, ...]
    video: VideoFacts | None
    copy_refusal: Refusal | None
    runtime_id: str
    # Reacquire source/ancestor leases and reauthenticate before any future job.
    inspection_only: bool = True


class OutputMode(str, Enum):
    SAME_AS_SOURCE = 'same_as_source'
    COMPATIBLE_MP4 = 'compatible_mp4'
    VIDEO_AUDIO_ONLY = 'video_audio_only'
    AUDIO = 'audio'


class AudioFormat(str, Enum):
    MP3 = 'mp3'
    WAV = 'wav'


@dataclass(frozen=True)
class OutputRoute:
    mode: OutputMode
    container: str
    suffix: str
    audio_codec: str
    sample_rate: int = 48000
    channels: int = 1
    bitrate: int | None = None
    video_codec: str | None = None
    copy_class: str | None = None
    audio_language: str = 'rus'


@dataclass(frozen=True)
class OutputPlan:
    route: OutputRoute
    source: SourceIdentity
    audio_index: int
    origin: Fraction
    duration: Fraction
    sample_count: int
    excluded_audio_before: Fraction
    excluded_audio_after: Fraction
    # C4 must qualify mux/encoded essence/full decoded picture/audio/publication.
    requires_output_qualification: bool = True

    @property
    def requires_span_acknowledgment(self) -> bool:
        return bool(self.excluded_audio_before or self.excluded_audio_after)
