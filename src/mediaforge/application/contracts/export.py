"""Application export interface consumed by Presentation."""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mediaforge.features.media.export import ExportRequest, ExportError, output_aspect, ratio_text
from mediaforge.application.contracts.image_export import ImageExportRequest,PictureSnapshot
from mediaforge.application.contracts.exported_result import ExportedResult,PreparedResult


_MEDIA_SUFFIXES = frozenset('.mp4 .mov .mkv .avi .webm .m4v .ts .png .jpg .jpeg .tif .tiff .exr .webp .wav .mp3'.split())
_OTHER_MEDIA_SUFFIXES = frozenset('.aac .aif .aiff .alac .flac .m2ts .m4a .mpeg .mpg .mts .ogg .opus .wma .wmv'.split())


def output_filename(value: str, extension: str = '', *, require_matching_suffix: bool = False) -> str:
    """Validate one Windows path component and apply the selected file format."""
    name = value
    planned = extension.lower() if require_matching_suffix else extension
    suffix = Path(name).suffix.lower()
    if require_matching_suffix:
        known_suffixes = _MEDIA_SUFFIXES | _OTHER_MEDIA_SUFFIXES | {planned}
        if value.lower() in known_suffixes or (suffix in known_suffixes and suffix != planned):
            raise ExportError('invalid_filename', f'Enter a basename or a filename ending in {extension}.')
    if extension and suffix in _MEDIA_SUFFIXES | {planned}:
        name = Path(name).stem
    while extension and Path(name).suffix.lower() == planned:
        name = Path(name).stem
    reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$',
                *(f'{prefix}{digit}' for prefix in ('COM', 'LPT') for digit in '123456789¹²³')}
    result = name + extension
    if (not name or (require_matching_suffix and name.lower() == planned)
            or value != value.strip() or name != name.strip() or name.endswith('.')
            or any(ord(char) < 32 or char in '<>:"/\\|?*' for char in value)
            or name.split('.')[0].rstrip().upper() in reserved
            or len(result.encode('utf-16-le')) > 510):
        raise ExportError('invalid_filename', 'Enter one valid Windows filename without path separators or reserved names.')
    return result


@dataclass(frozen=True)
class ExportJob:
    job_id: int = 0
    state: str = "Idle"
    stage: str = ""
    message: str = ""
    result: str = ""
    error: str = ""
    progress: float = -1.
    elapsed: float = 0.
    speed: str = ""
    fps: str = ""


class ExportUseCase(Protocol):
    @property
    def busy(self) -> bool: ...
    def start(self, item) -> bool: ...
    def prepare(self, item, protected_sources=(), snapshot: PictureSnapshot | None = None) -> ExportRequest | ImageExportRequest: ...
    def start_request(self, request: ExportRequest | ImageExportRequest) -> bool: ...
    def snapshot(self) -> ExportJob: ...
    def completed_result(self) -> ExportedResult | None: ...
    def prepare_result(self,result: ExportedResult,cancel=None) -> PreparedResult: ...
    def cancel_result_preparation(self) -> None: ...
    def cancel(self) -> bool: ...
    def shutdown(self) -> None: ...
