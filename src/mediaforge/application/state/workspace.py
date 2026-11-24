"""Session-only media workspace state. No files are written by this module."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4
import os

from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.contracts.source_timeline import SourceSignature
from mediaforge.application.contracts.image_source import VideoSource, SequenceSource, StillSource
from fractions import Fraction
from mediaforge.features.media.resize import ResizeDimensions, ResizeError
from mediaforge.features.media.crop import CropRect, CropError
from mediaforge.application.contracts.reformat import GeometryError, dimensions, locked_dimension, plan, scaled_dimensions
from mediaforge.application.contracts import reformat as video_geometry,image_geometry


def canonical_path(path: str) -> str:
    return os.path.normcase(str(Path(path).resolve()))


@dataclass(slots=True)
class TrimSettings:
    enabled: bool = False
    in_ms: int = 0
    out_ms: int = 0
    in_picture: TrimPictureBoundary | None = None
    out_picture: TrimPictureBoundary | None = None

    @property
    def player_bounds_us(self):
        return tuple(picture.player_us if picture is not None else ms * 1000
                     for ms, picture in ((self.in_ms, self.in_picture), (self.out_ms, self.out_picture)))


@dataclass(frozen=True, slots=True)
class TrimPictureBoundary:
    signature: SourceSignature
    pts_us: int
    player_us: int


@dataclass(slots=True)
class SequenceSettings:
    rate: Fraction = Fraction(24, 1)
    confirmed: bool = False
    # Integer half-open ordinal range. Never filename numbers or rounded time.
    in_slot: int = 0
    out_slot: int = 0
    current_slot: int = 0


@dataclass(slots=True)
class VideoSession:
    position_ms: int = 0
    signature: object = None
    offset_us: int | None = None


@dataclass(slots=True)
class CropSettings:
    enabled: bool = False
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    preview: bool = False


@dataclass(slots=True)
class ResizeSettings:
    enabled: bool = False
    width: int = 0
    height: int = 0
    mode: str = "scale"
    scale: str = "1.0"
    placement: str = "fit"
    preset: str = "Custom"
    aspect_locked: bool = False
    error: str = ""
    pixel_aspect: str = "square"


@dataclass(slots=True)
class OutputSettings:
    kind: str = 'media'
    matte: str = ''
    webp_ack: bool = False
    jpeg_quality: int = 90
    jpeg_quality_normalized: str = '0.90'
    png_compression: int = 6
    tiff_compression: str = 'deflate'
    image_depth: str = '8'
    jpeg_subsampling: str = '444'
    webp_lossless: bool = True
    webp_quality: int = 90
    sequence_start: str = '0001'
    container: str = "source"
    codec: str = "source"
    h264_quality: int = 18
    prores_quality: int = 2
    quality_preset: str = "high"
    speed: str = "balanced"
    h264_profile: str = "auto"
    audio_format: str = "wav"
    audio_stream: int = 0
    audio_bitrate: int = 192
    folder: str = ""
    filename: str = ""


@dataclass(slots=True)
class MediaItem:
    id: str
    metadata: MediaMetadataDto
    canonical_path: str
    trim: TrimSettings = field(default_factory=TrimSettings)
    crop: CropSettings = field(default_factory=CropSettings)
    resize: ResizeSettings = field(default_factory=ResizeSettings)
    output: OutputSettings = field(default_factory=OutputSettings)
    sequence: SequenceSettings = field(default_factory=SequenceSettings)
    video: VideoSession = field(default_factory=VideoSession)

    @classmethod
    def create(cls, metadata: MediaMetadataDto) -> MediaItem:
        duration = max(0, round((metadata.duration_seconds or 0) * 1000))
        return cls(
            id=uuid4().hex,
            metadata=metadata,
            canonical_path=canonical_path(metadata.source_path),
            trim=TrimSettings(out_ms=duration),
            sequence=SequenceSettings(out_slot=metadata.source.manifest.count if isinstance(metadata.source,SequenceSource) else 0),
            crop=CropSettings(width=metadata.width // 2 * 2 if isinstance(metadata.source,VideoSource) else metadata.width,
                              height=metadata.height // 2 * 2 if isinstance(metadata.source,VideoSource) else metadata.height),
            resize=ResizeSettings(width=metadata.width, height=metadata.height),
            output=OutputSettings(kind='still' if isinstance(metadata.source,StillSource) else 'media',
                                  container='audio' if metadata.audio_only else 'png' if isinstance(metadata.source,StillSource) else 'mp4' if isinstance(metadata.source,SequenceSource) else 'source',
                                  codec='h264' if isinstance(metadata.source,SequenceSource) else 'source',
                                  folder=str(Path(metadata.source_path).parent),
                                  filename=Path(metadata.source_path).stem),
        )

    @property
    def duration_ms(self) -> int:
        return max(0, round((self.metadata.duration_seconds or 0) * 1000))


class MediaWorkspaceSession:
    """Owns ordered edit settings independently of playback and processing."""

    def __init__(self) -> None:
        self.items: list[MediaItem] = []
        self.active_id: str | None = None

    @property
    def active(self) -> MediaItem | None:
        return next((item for item in self.items if item.id == self.active_id), None)

    def original_reference(self):
        from mediaforge.application.contracts.media import ActiveSourceRef
        item = self.active
        if item is None:
            return None
        source = item.metadata.source
        kind = ('audio' if item.metadata.audio_only else 'sequence' if isinstance(source, SequenceSource)
                else 'still' if isinstance(source, StillSource) else 'video')
        try:
            facts = Path(item.metadata.source_path).stat()
        except OSError:
            return None
        return ActiveSourceRef(item.id, item.metadata.source_path, kind,
                               (facts.st_dev, facts.st_ino, facts.st_size, facts.st_mtime_ns))

    @property
    def active_index(self) -> int:
        return next((i for i, item in enumerate(self.items)
                     if item.id == self.active_id), -1)

    def find_path(self, path: str) -> MediaItem | None:
        key = canonical_path(path)
        return next((item for item in self.items if item.canonical_path == key), None)

    def add(self, metadata: MediaMetadataDto) -> tuple[MediaItem, bool]:
        key = canonical_path(metadata.source_path)
        existing = next((i for i in self.items if i.canonical_path == key
                         and type(i.metadata.source) is type(metadata.source)),None)
        if existing is not None:
            self.active_id = existing.id
            return existing, False
        item = MediaItem.create(metadata)
        self.items.append(item)
        self.active_id = item.id
        return item, True

    def select(self, item_id: str) -> MediaItem | None:
        item = next((item for item in self.items if item.id == item_id), None)
        if item is not None:
            self.active_id = item.id
        return item

    def duplicate(self, item_id: str) -> MediaItem | None:
        index = next((i for i, item in enumerate(self.items) if item.id == item_id), -1)
        if index < 0:
            return None
        source = self.items[index]
        clone = replace(source, id=uuid4().hex,
                        trim=replace(source.trim), crop=replace(source.crop),
                        resize=replace(source.resize), output=replace(source.output), sequence=replace(source.sequence), video=replace(source.video))
        self.items.insert(index + 1, clone)
        return clone

    def remove(self, item_id: str) -> MediaItem | None:
        index = next((i for i, item in enumerate(self.items) if item.id == item_id), -1)
        if index < 0:
            return self.active
        removed = self.items.pop(index)
        if self.active_id == removed.id:
            self.active_id = self.items[min(index, len(self.items) - 1)].id if self.items else None
        return self.active

    def move(self, item_id: str, direction: int) -> bool:
        index = next((i for i, item in enumerate(self.items) if item.id == item_id), -1)
        target = index + direction
        if index < 0 or target < 0 or target >= len(self.items) or abs(direction) != 1:
            return False
        self.items[index], self.items[target] = self.items[target], self.items[index]
        return True

    def set_enabled(self, operation: str, enabled: bool) -> bool:
        item = self.active
        if item is None or operation not in ("trim", "crop", "resize"):
            return False
        if item.metadata.audio_only and operation!='trim':return False
        if not isinstance(item.metadata.source, VideoSource) and not (operation in ('crop','resize') or isinstance(item.metadata.source,SequenceSource) and operation=='trim'):
            return False  # Image processing/export belongs to later stages.
        getattr(item, operation).enabled = bool(enabled)
        return True

    def set_sequence_trim(self, start, end):
        item = self.active
        if (item is None or not isinstance(item.metadata.source,SequenceSource) or not item.trim.enabled
                or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= item.metadata.source.manifest.count): return False
        item.sequence.in_slot, item.sequence.out_slot = start, end
        return True

    def set_trim(self, in_ms: int, out_ms: int) -> bool:
        return self._set_trim(in_ms, out_ms, None, None)

    def set_trim_boundary(self, is_out, ms, picture=None):
        if self.active is None:
            return False
        trim = self.active.trim
        if is_out:
            return self._set_trim(trim.in_ms, ms, trim.in_picture, picture)
        return self._set_trim(ms, trim.out_ms, picture, trim.out_picture)

    def _set_trim(self, in_ms, out_ms, in_picture, out_picture):
        item = self.active
        if (item is None or not item.trim.enabled or type(in_ms) is not int or type(out_ms) is not int
                or not (0 <= in_ms < out_ms <= item.duration_ms)):
            return False
        start = in_picture.player_us if in_picture is not None else in_ms * 1000
        end = out_picture.player_us if out_picture is not None else out_ms * 1000
        if start >= end:
            return False
        item.trim.in_ms, item.trim.out_ms = in_ms, out_ms
        item.trim.in_picture, item.trim.out_picture = in_picture, out_picture
        return True

    def set_crop(self, x: int, y: int, width: int, height: int) -> bool:
        item = self.active
        if item is None:
            return False
        try:
            if isinstance(item.metadata.source,VideoSource):CropRect(x, y, width, height).validate(item.metadata.width, item.metadata.height)
            elif not (all(type(v)is int for v in (x,y,width,height)) and 0<=x<item.metadata.width and 0<=y<item.metadata.height and width>0 and height>0 and x+width<=item.metadata.width and y+height<=item.metadata.height):return False
        except CropError:
            return False
        item.crop.x, item.crop.y = x, y
        item.crop.width, item.crop.height = width, height
        return True

    def reset_crop(self) -> bool:
        item = self.active
        if item is None:
            return False
        item.crop.x = item.crop.y = 0
        item.crop.width, item.crop.height = item.metadata.width, item.metadata.height
        item.crop.preview = item.crop.enabled = False
        return True

    def set_resize(self, width: int, height: int) -> bool:
        item = self.active
        if item is None or type(width) is not int or type(height) is not int:
            return False
        try:
            if isinstance(item.metadata.source,VideoSource):ResizeDimensions(width, height).validate()
            else:image_geometry.dimensions(width,height)
        except (ResizeError,GeometryError):
            return False
        item.resize.width, item.resize.height = width, height
        item.resize.mode = "format"
        item.resize.placement = "stretch"
        item.resize.preset = "Custom"
        return True

    def effective_input(self):
        item = self.active
        if item is None:
            raise GeometryError("Open a video first.")
        return ((item.crop.width, item.crop.height) if item.crop.enabled
                else (item.metadata.width, item.metadata.height))

    def output_summary(self,snapshot=None):
        from mediaforge.application.services.image_export import uses_image_export,describe
        if uses_image_export(self.active):return describe(self.active,snapshot)
        from mediaforge.application.services.export import summary
        return summary(self.active)

    def set_output(self, field, value):
        item = self.active
        if item is None:
            return False
        if field=='kind' and value in ('media','still','frame','sequence'):
            if isinstance(item.metadata.source,StillSource) and value!='still':return False
            if item.metadata.audio_only:
                if value != 'media':
                    return False
                item.output.kind, item.output.container = 'media', 'audio'
                return True
            item.output.kind=value
            item.output.container='mp4' if value=='media' and isinstance(item.metadata.source,SequenceSource) else 'source' if value=='media' else 'png'
            item.output.image_depth='8'
            if isinstance(item.metadata.source,SequenceSource):item.output.codec='h264'
        elif field=='matte' and value in ('','black','white'):item.output.matte=value
        elif field=='webp_ack' and value in ('true','false'):item.output.webp_ack=value=='true'
        elif field=='jpeg_quality' and value.isascii() and value.isdigit() and len(value)<=3 and 1<=int(value)<=100:
            item.output.jpeg_quality=int(value);item.output.jpeg_quality_normalized=f'{int(value)/100:.2f}'
        elif field=='png_compression' and value in ('0','6','9'):item.output.png_compression=int(value)
        elif field=='tiff_compression' and value in ('raw','packbits','lzw','deflate'):item.output.tiff_compression=value
        elif field=='image_depth' and value in ('8','16','float') and (value!='float' or item.output.container=='tiff'):item.output.image_depth=value
        elif field=='jpeg_subsampling' and value in ('411','422','444'):item.output.jpeg_subsampling=value
        elif field=='webp_lossless' and value in ('true','false'):item.output.webp_lossless=value=='true'
        elif field in ('jpeg_quality_normalized','webp_quality_normalized'):
            from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
            try:
                if len(value)>4:raise ValueError
                quality=Decimal(value)
                if not quality.is_finite() or not 0<=quality<=1:raise ValueError
                amount=int((quality*100).quantize(Decimal(1),rounding=ROUND_HALF_UP))
            except (ValueError,InvalidOperation):return False
            if field=='jpeg_quality_normalized':
                item.output.jpeg_quality=max(1,amount);item.output.jpeg_quality_normalized=f'{quality:.2f}'
            else:item.output.webp_quality=amount
        elif field=='sequence_start' and value.isascii() and value.isdigit() and len(value)<=19:
            item.output.sequence_start=value
        elif field == "container" and value in ("source", "mp4", "mov", "audio",'png','jpeg','tiff','webp'):
            if item.metadata.audio_only and value != 'audio':
                return False
            if item.output.container=='jpeg' and value in ('png','tiff','webp'):item.output.matte=''
            item.output.container = value
            if value not in ('png','tiff') or value=='png' and item.output.image_depth=='float':item.output.image_depth='8'
            if value=='jpeg' and not item.output.matte:item.output.matte='black'
        elif field == "quality_preset" and value in ("high", "balanced", "small", "custom"):
            item.output.quality_preset = value
            if value != "custom": item.output.h264_quality = {"high":18,"balanced":21,"small":24}[value]
        elif field == "speed" and value in ("fast", "balanced", "quality"):
            item.output.speed = value
        elif field == "h264_profile" and value in ("auto", "high", "main"):
            item.output.h264_profile = value
        elif field == "audio_format" and value in ("wav", "mp3"):
            item.output.audio_format = value
        elif field == "audio_stream" and value.isascii() and value.isdigit() and len(value) <= 3:
            item.output.audio_stream = int(value)
        elif field == "audio_bitrate" and value in ("128","192","256","320"):
            item.output.audio_bitrate = int(value)
        elif field == "codec" and value in ("source", "h264", "prores"):
            item.output.codec = value
        elif field in ("folder", "filename") and isinstance(value, str):
            setattr(item.output, field, value)
        elif field in ("h264_quality", "prores_quality"):
            if not value.isascii() or not value.isdigit() or len(value) > 2:
                return False
            number = int(value)
            if number > (51 if field == "h264_quality" else 5):
                return False
            setattr(item.output, field, number)
            if field == "h264_quality": item.output.quality_preset = "custom"
        else:
            return False
        return True

    def reformat_plan(self):
        item = self.active
        if item is None:
            raise GeometryError("Open a video first.")
        w, h = self.effective_input()
        settings = item.resize
        rules=video_geometry if isinstance(item.metadata.source,VideoSource) else image_geometry
        target = (rules.scaled_dimensions(w, h, settings.scale) if settings.mode == "scale"
                  else rules.dimensions(settings.width, settings.height))
        from mediaforge.application.contracts.export import output_aspect
        from mediaforge.application.contracts.reformat import sample_aspect
        sar = output_aspect(settings.pixel_aspect, target.width, target.height, item.metadata.sample_aspect_ratio)
        # Fit/Fill must preserve picture aspect on the final non-square canvas.
        ratio = sample_aspect(item.metadata.sample_aspect_ratio) / sar
        return rules.plan(w, h, target, "stretch" if settings.mode == "scale" else settings.placement, str(ratio))

    def set_reformat(self, field: str, value: str) -> bool:
        item = self.active
        if item is None:
            return False
        settings = item.resize
        rules=video_geometry if isinstance(item.metadata.source,VideoSource) else image_geometry
        try:
            if field == "mode" and value in ("scale", "format"):
                settings.mode = value
            elif field == "pixel_aspect":
                if len(value) > 64:
                    raise GeometryError("Pixel Aspect input exceeds 64 characters; enter a bounded exact value.")
                settings.pixel_aspect = value
            elif field == "scale":
                # Retain invalid entered multipliers, with a visible error and no approximate preview.
                settings.scale = value[:64]
            elif field == "placement" and value in ("fit", "fill", "stretch"):
                settings.placement = value
            elif field == "lock":
                settings.aspect_locked = value == "true"
            elif field in ("width", "height"):
                if not value.isascii() or not value.isdigit() or len(value) > 5:
                    raise GeometryError("Enter an exact integer dimension.")
                axis = int(value)
                w, h = self.effective_input()
                other = rules.locked_dimension(axis, w if field == "width" else h,
                                         h if field == "width" else w) if settings.aspect_locked else None
                nw = axis if field == "width" else (other if other is not None else settings.width)
                nh = axis if field == "height" else (other if other is not None else settings.height)
                rules.dimensions(nw, nh)
                settings.width, settings.height, settings.preset = nw, nh, "Custom"
            elif field == "preset":
                from mediaforge.application.contracts.reformat import PRESETS
                if value == "Custom":
                    settings.preset = value
                elif value == "Source":
                    # Original opened canvas, independently of effective Crop input.
                    rules.dimensions(item.metadata.width, item.metadata.height)
                    settings.preset = value
                    settings.width, settings.height = item.metadata.width, item.metadata.height
                else:
                    preset = next((p for p in PRESETS if p[0] == value), None)
                    if preset is None:
                        raise GeometryError("Unknown preset.")
                    settings.preset, settings.width, settings.height = preset
            else:
                return False
            settings.error = ""
            self.reformat_plan()
            return True
        except GeometryError as error:
            settings.error = str(error)
            return False
