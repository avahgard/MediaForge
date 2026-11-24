"""Typed, session-only image identity. No Qt values or export pixels from Viewer."""
from dataclasses import dataclass
from typing import Protocol
from mediaforge.application.contracts.sequence import SequenceManifest


@dataclass(frozen=True, slots=True)
class VideoSource:
    path: str


@dataclass(frozen=True, slots=True)
class AudioSource(VideoSource):
    """Owned AV transport without a fabricated video raster or frame clock."""


@dataclass(frozen=True, slots=True)
class ImageSignature:
    path: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ImageFacts:
    format: str
    width: int
    height: int
    orientation: int = 1
    alpha: bool = False
    depth: int = 8
    color_class: str = 'sRGB'
    variant: str = ''
    sample_type: str = 'uint'

    @property
    def decoded_bytes_per_pixel(self):
        # Native owned preview backing uses four aligned channels. Export never
        # reads this display backing; original sample precision stays separate.
        return 16 if self.sample_type == 'float' else 8 if self.depth > 8 else 4


@dataclass(frozen=True, slots=True)
class StillSource:
    signature: ImageSignature
    facts: ImageFacts

    @property
    def path(self):
        return self.signature.path


@dataclass(frozen=True, slots=True)
class SequenceSource:
    """Immutable original members; preview quality and editable FPS live elsewhere."""
    path: str
    manifest_id: str
    manifest: SequenceManifest

    def __post_init__(self):
        if not self.manifest.members or self.manifest_id != self.manifest.identity or self.path != self.manifest.members[0].path:
            raise ValueError('Sequence source identity disagrees with its immutable manifest.')


@dataclass(frozen=True, slots=True)
class ImageRaster:
    source: StillSource
    rgba: bytes


class ImageReader(Protocol):
    def discover_sequence(self, path, cancel, *, automatic=False) -> SequenceManifest: ...
    def validate_sequence(self, manifest: SequenceManifest, cancel) -> None: ...
    def is_image(self, path: str) -> bool: ...
    def probe(self, path: str) -> StillSource: ...
    def decode(self, source: StillSource) -> ImageRaster: ...
    def decode_preview(self, source: StillSource, plan: 'ImagePreviewPlan', cancel) -> tuple['ImagePreviewRaster', object]: ...


IMAGE_SUFFIXES = frozenset(('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.webp'))


@dataclass(frozen=True, slots=True)
class ImagePreviewPlan:
    """Display sampling only; source facts and processing geometry stay original."""
    divisor: int
    width: int
    height: int
    peak_bytes: int
    effective_quality: str
    region: tuple[int,int,int,int] | None = None
    full_source: bool = False
    reuse_source: bool = False  # Executable admission decision; never silently fall back to decode.


@dataclass(frozen=True, slots=True)
class ImagePreviewRaster:
    source: StillSource
    plan: ImagePreviewPlan
    # Opaque owned adapter value. Application never calls Qt or reads its pixels.
    display: object
    decode_ms: float
    normalize_ms: float
    # Optional immutable straight source raster retained only for channel views.
    # It is preview-owned adapter data, never authoritative processing input.
    straight: object = None
    channel: str = 'RGBA'
    cache_hit: bool = False


def preview_reuse_eligible(owner, source):
    """Shared planner/adapter contract for an independently retained source pin."""
    check = getattr(owner, 'reusable_for', None)
    return bool(callable(check) and check(source))
