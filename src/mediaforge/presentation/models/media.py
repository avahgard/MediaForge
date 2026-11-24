"""Presentation-only formatting for the minimal metadata view."""

from dataclasses import dataclass

from mediaforge.application.contracts.media import MediaMetadataDto


@dataclass(frozen=True, slots=True)
class MediaMetadataView:
    file_name: str
    local_path: str
    format_name: str
    duration: str
    codec: str
    resolution: str
    frame_rate: str

    @classmethod
    def from_dto(cls, metadata: MediaMetadataDto) -> "MediaMetadataView":
        from mediaforge.application.contracts.image_source import StillSource, SequenceSource
        still=isinstance(metadata.source,StillSource)
        return cls(
            file_name=metadata.source_name,
            local_path=metadata.source_path,
            format_name=metadata.format_name,
            duration=(f'{metadata.source.manifest.count} images' if isinstance(metadata.source,SequenceSource) else 'Still image' if still else _format_duration(metadata.duration_seconds)),
            codec=metadata.video_codec,
            resolution='Audio' if metadata.audio_only else f"{metadata.width} × {metadata.height}",
            frame_rate='' if metadata.audio_only or still or isinstance(metadata.source,SequenceSource) else _format_frame_rate(metadata.frame_rate),
        )


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "Unavailable"
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    if hours:
        return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{millis:03d}"
    return f"{minutes}:{whole_seconds:02d}.{millis:03d}"


def _format_frame_rate(frame_rate: float | None) -> str:
    if frame_rate is None:
        return "Unavailable"
    value = f"{frame_rate:.3f}".rstrip("0").rstrip(".")
    return f"{value} fps"
