"""Application use-case for opening and probing one local media file."""

import logging
from dataclasses import asdict
from pathlib import Path

from mediaforge.application.contracts.media import (
    MediaMetadataDto,
    MediaInfoFactsDto,
    OpenMediaErrorCode,
    OpenMediaResult,
)
from mediaforge.features.media.metadata import (
    MediaMetadataProbe,
    MediaProbeError,
    MediaProbeErrorCode,
)


_ERROR_MAPPING = {
    MediaProbeErrorCode.UNAVAILABLE: (
        OpenMediaErrorCode.FFPROBE_UNAVAILABLE,
        "FFprobe is not configured. Choose it explicitly before opening media.",
    ),
    MediaProbeErrorCode.PROCESS_FAILED: (
        OpenMediaErrorCode.PROBE_FAILED,
        "Media metadata could not be read. Try another file.",
    ),
    MediaProbeErrorCode.INVALID_OUTPUT: (
        OpenMediaErrorCode.INVALID_PROBE_OUTPUT,
        "The metadata response was not valid. Try another file.",
    ),
    MediaProbeErrorCode.NO_VIDEO_STREAM: (
        OpenMediaErrorCode.NO_VIDEO_STREAM,
        "The selected file does not contain a usable video stream.",
    ),
    MediaProbeErrorCode.TIMEOUT: (
        OpenMediaErrorCode.PROBE_TIMEOUT,
        "Reading media metadata timed out. Try another file.",
    ),
    MediaProbeErrorCode.ABORTED: (
        OpenMediaErrorCode.PROBE_ABORTED,
        "Reading media metadata was cancelled.",
    ),
}


class OpenMediaService:
    def __init__(
        self,
        probe: MediaMetadataProbe,
        logger: logging.Logger | None = None,
        result_preparer=None,
    ) -> None:
        self._probe = probe
        self._logger = logger or logging.getLogger("mediaforge")
        self._result_preparer=result_preparer

    def open_media(self, source_path: str) -> OpenMediaResult:
        try:
            source = Path(source_path)
        except (TypeError, ValueError):
            return self._invalid_source()
        if not source.is_absolute() or not source.is_file():
            return self._invalid_source()

        try:
            metadata = self._probe.probe(source)
        except MediaProbeError as error:
            code, message = _ERROR_MAPPING[error.code]
            return OpenMediaResult(error_code=code, error_message=message)
        except Exception:
            self._logger.exception("Unexpected media metadata probe failure")
            return OpenMediaResult(
                error_code=OpenMediaErrorCode.PROBE_FAILED,
                error_message="Media metadata could not be read. Try another file.",
            )

        return OpenMediaResult(
            metadata=MediaMetadataDto(
                source_name=metadata.source_name,
                source_path=str(metadata.source_path),
                format_name=metadata.format_name,
                duration_seconds=metadata.duration_seconds,
                video_codec=metadata.video_codec,
                width=metadata.width,
                height=metadata.height,
                frame_rate=metadata.frame_rate,
                navigation_frame_rate=metadata.navigation_frame_rate,
                navigation_frame_count=metadata.navigation_frame_count,
                navigation_rate_ratio=metadata.navigation_rate_ratio,
                navigation_time_base=metadata.navigation_time_base,
                navigation_end_us=metadata.navigation_end_us,
                info_facts=MediaInfoFactsDto(**asdict(metadata.info_facts)) if metadata.info_facts else None,
                sample_aspect_ratio=metadata.sample_aspect_ratio,
                audio_streams=metadata.audio_streams,
                audio_only=metadata.audio_only,
            )
        )

    def open_exported_result(self, result,cancel=None):
        from mediaforge.application.services.exported_result import result_path
        from dataclasses import replace
        guard=None
        try:
            if result.kind not in ('video','audio'):raise ValueError('This result is not supported media.')
            if self._result_preparer is None:raise ValueError('Trusted completed-result preparation is unavailable.')
            guard=self._result_preparer.prepare_result(result,cancel)
            path=result_path(result);before=path.stat()
            candidate=self.open_media(str(path))
            after=result_path(result).stat()
            if ((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)
                    !=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)):
                raise ValueError('Exported video changed during validation.')
            if candidate.succeeded and result.kind=='audio' and (not candidate.metadata.audio_only
                    or candidate.metadata.format_name not in ('wav','mp3')):
                raise ValueError('Exported audio no longer matches its validated type.')
            expected_container={'mov':{'mov'},'mp4':{'mp4'},'mkv':{'matroska'}}.get(result.format)
            if candidate.succeeded and result.kind=='video' and (not candidate.metadata.video_codec
                    or not candidate.metadata.format_name
                    or expected_container is not None and not set(candidate.metadata.format_name.split(',')).intersection(expected_container)
                    or (candidate.metadata.width,candidate.metadata.height)!=(result.width,result.height)):
                raise ValueError('Exported video no longer matches its validated geometry/type.')
            guard.check()
            if not candidate.succeeded:guard.close();guard=None;return candidate
            return replace(candidate,guard=guard)
        except (OSError,ValueError,RuntimeError) as error:
            if guard is not None:guard.close()
            return OpenMediaResult(error_code=OpenMediaErrorCode.PROBE_FAILED,error_message=str(error))

    def cancel(self) -> None:
        self._probe.cancel()
        if self._result_preparer is not None:self._result_preparer.cancel_result_preparation()

    @staticmethod
    def _invalid_source() -> OpenMediaResult:
        return OpenMediaResult(
            error_code=OpenMediaErrorCode.INVALID_SOURCE,
            error_message="Choose an existing local media file.",
        )
