"""Immutable combined export recipe and bounded output matrix; no Qt or subprocesses."""
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable, Protocol

from mediaforge.features.media.crop import CropRect
from mediaforge.features.media.processing_control import ProcessingControl
from mediaforge.features.media.reformat import GeometryError, ReformatPlan, sample_aspect


class ExportError(RuntimeError):
    def __init__(self, code, message="", *, retained_resources=(), reported_destination=None):
        self.code = code
        self.retained_resources = tuple(retained_resources)
        self.reported_destination = reported_destination
        super().__init__(message or code.replace("_", " "))


def require_audio_source_export(container, codec, stream_count):
    if stream_count != 1 or (container, codec) not in (('wav', 'pcm_s16le'), ('mp3', 'mp3')):
        raise ExportError('unsupported_audio_source',
                          'Audio-only export supports single-stream PCM 16-bit WAV or MP3 sources. This source can still play.')


def validate_video_encoder(codec, quality, speed, profile):
    """One admission contract for summaries, frozen capture and execution."""
    if codec not in ('h264','prores'):
        raise ExportError('unsupported_codec','Choose H.264 or ProRes.')
    if type(quality) is not int or not 0 <= quality <= (5 if codec=='prores' else 51):
        raise ExportError('invalid_quality','H.264 CRF must be 0..51; ProRes profile must be 0..5.')
    if speed not in ('fast','balanced','quality') or profile not in ('auto','main','high'):
        raise ExportError('invalid_encoder_setting','Choose a supported encoder speed and H.264 profile.')
    if codec=='h264' and quality==0 and profile!='auto':
        raise ExportError('unsupported_profile','Main/High profiles cannot encode lossless CRF 0. Choose Auto or CRF 1–51.')


def output_aspect(mode, width, height, source_sar):
    if mode == "source":
        sar = sample_aspect(source_sar)
    elif mode == "square":
        sar = Fraction(1)
    elif mode == "16:9":
        sar = Fraction(16 * height, 9 * width)
    else:
        import re
        if not isinstance(mode, str) or len(mode) > 64 or not re.fullmatch(r'(?:\d+(?:\.\d*)?|\d+/\d+)', mode.strip()):
            raise GeometryError("Enter a positive decimal Pixel Aspect or exact fraction such as 4/3.")
        try:
            sar = Fraction(mode.strip())
        except (ValueError, ZeroDivisionError) as error:
            raise GeometryError("Pixel Aspect requires a positive, nonzero fraction.") from error
        if sar <= 0:
            raise GeometryError("Pixel Aspect must be greater than zero.")
    # libx264's extended SAR is two unsigned 16-bit integers.
    if max(sar.numerator, sar.denominator) > 65535:
        raise GeometryError("Output SAR exceeds the supported exact signaling range (65535).")
    return sar


def ratio_text(ratio):
    return f"{ratio.numerator}:{ratio.denominator}"


@dataclass(frozen=True)
class ExportRequest:
    source: Path
    destination: Path
    source_width: int
    source_height: int
    source_sar: Fraction
    source_signature: tuple[int, int]
    codec: str
    quality: int
    width: int
    height: int
    sar: Fraction
    trim: tuple[int, int] | None = None
    crop: CropRect | None = None
    reformat: ReformatPlan | None = None
    exact_copy: bool = False
    speed: str = "balanced"
    h264_profile: str = "auto"
    audio_stream: int = 0
    audio_bitrate: int = 192
    # A confirmed request carries one identity, never a blanket overwrite flag.
    replacement: tuple[int, int, int, int] | None = None
    protected_sources: tuple[Path, ...] = ()
    trim_pts_us: tuple[int | None, int | None] = (None, None)

    @property
    def dar(self):
        return Fraction(self.width, self.height) * self.sar


class ExportControl(ProcessingControl):
    def __init__(self, on_stage=None, on_progress=None):
        super().__init__(lambda: ExportError("cancelled", "Export cancelled."))
        self._on_stage = on_stage
        self._on_progress = on_progress
        self._retained_resources = []
        self._published_destination: Path | None = None

    @property
    def published_destination(self) -> Path | None:
        with self._gate:
            return self._published_destination

    def publish(self, action: Callable[[], Path]) -> Path:
        def commit():
            destination = action()
            self._published_destination = destination
            return destination

        return super().publish(commit)

    def retain_resources(self, resources):
        """Own opaque unresolved Infrastructure resources for this job's lifetime."""
        with self._gate:
            for resource in resources:
                if not any(resource is held for held in self._retained_resources):
                    self._retained_resources.append(resource)
            self._cleanup_failed = True

    @property
    def retained_resources(self):
        with self._gate:
            return tuple(self._retained_resources)

    def progress(self, fraction, speed, fps):
        if self._on_progress is not None:
            self._on_progress(fraction, speed, fps)

    def report(self, stage, message):
        if self._on_stage is not None:
            self._on_stage(stage, message)


@dataclass(frozen=True)
class OutputFileIdentity:
    name: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class OutputIdentity:
    path: str
    files: tuple[OutputFileIdentity, ...]


@dataclass(frozen=True)
class ExportResult:
    output: Path
    identity: OutputIdentity | None = None


class ExportExecutor(Protocol):
    def execute(self, request: ExportRequest, control: ExportControl) -> ExportResult: ...


def effective_format(source, container, codec, processing):
    suffix = source.suffix.lower() if container == "source" else "." + container
    if suffix not in (".mp4", ".mov", ".mkv") and (processing or container != "source" or codec != "source"):
        raise ExportError("unsupported_container", "Encoding supports MP4, MOV and Same-as-Source MKV only.")
    if not processing and container == "source" and codec == "source":
        return suffix, "copy"
    actual = codec
    if actual not in ("h264", "prores"):
        raise ExportError("unsupported_codec", "Choose H.264 or ProRes for this source codec.")
    if actual == "prores" and suffix != ".mov":
        raise ExportError("unsupported_codec", "ProRes requires a MOV container. Select MOV explicitly.")
    return suffix, actual
