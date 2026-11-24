"""Minimal cancellable FFprobe adapter for the TASK-002 metadata contract."""

from collections.abc import Callable
from fractions import Fraction
import json
import logging
import math
from pathlib import Path
import subprocess
import threading
from typing import Any

from mediaforge.features.media.metadata import (
    MediaMetadata,
    MediaProbeError,
    MediaProbeErrorCode,
)


from mediaforge.features.media.metadata.cadence import cfr_stream_basis
from mediaforge.features.media.metadata.model import MediaInfoFacts
from mediaforge.features.media.reformat import sample_aspect


ProcessFactory = Callable[..., subprocess.Popen[str]]
_MAX_DIMENSION = 1_000_000
_MAX_DURATION_SECONDS = 315_576_000.0
_PROCESS_STOP_TIMEOUT_SECONDS = 2.0
_NAVIGATION_PACKET_LIMIT = 65


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"Non-finite JSON number is not allowed: {value}")


class FfprobeMediaMetadataProbe:
    def __init__(
        self,
        executable: Path | None,
        *,
        timeout_seconds: float = 15.0,
        process_factory: ProcessFactory = subprocess.Popen,
        logger: logging.Logger | None = None,
    ) -> None:
        self._executable = executable
        self._timeout_seconds = timeout_seconds
        self._process_factory = process_factory
        self._logger = logger or logging.getLogger("mediaforge")
        self._cancel_requested = threading.Event()
        self._process_lock = threading.Lock()
        self._termination_lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    def probe(self, source: Path) -> MediaMetadata:
        process = None
        try:
            if self._executable is None or not self._executable.is_file():
                raise MediaProbeError(
                    MediaProbeErrorCode.UNAVAILABLE,
                    "The configured FFprobe executable is unavailable.",
                )

            command = self._command(source)
            try:
                with self._process_lock:
                    if self._cancel_requested.is_set():
                        raise MediaProbeError(
                            MediaProbeErrorCode.ABORTED,
                            "FFprobe was cancelled before it started.",
                        )
                    process = self._process_factory(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        shell=False,
                    )
                    self._process = process
            except MediaProbeError:
                raise
            except (FileNotFoundError, OSError) as error:
                self._logger.exception(
                    "FFprobe could not be started: %s", self._executable
                )
                raise MediaProbeError(
                    MediaProbeErrorCode.UNAVAILABLE,
                    "The configured FFprobe executable could not be started.",
                ) from error

            try:
                stdout, stderr = process.communicate(timeout=self._timeout_seconds)
            except subprocess.TimeoutExpired as error:
                self._stop_process(process)
                try:
                    process.communicate()
                except (OSError, subprocess.SubprocessError):
                    self._logger.exception("Could not drain stopped FFprobe pipes")
                if self._cancel_requested.is_set():
                    raise MediaProbeError(
                        MediaProbeErrorCode.ABORTED, "FFprobe was cancelled."
                    ) from error
                self._logger.error("FFprobe timed out for %s", source)
                raise MediaProbeError(
                    MediaProbeErrorCode.TIMEOUT, "FFprobe timed out."
                ) from error

            if self._cancel_requested.is_set():
                raise MediaProbeError(
                    MediaProbeErrorCode.ABORTED, "FFprobe was cancelled."
                )
            if process.returncode != 0:
                self._logger.error(
                    "FFprobe failed for %s with exit code %s: %s",
                    source,
                    process.returncode,
                    stderr.strip(),
                )
                raise MediaProbeError(
                    MediaProbeErrorCode.PROCESS_FAILED, "FFprobe returned an error."
                )

            try:
                payload = json.loads(
                    stdout,
                    parse_constant=_reject_non_finite_json,
                )
                return self._parse(payload, source)
            except MediaProbeError as error:
                self._logger.error(
                    "FFprobe metadata was rejected for %s (%s): %s",
                    source,
                    error.code.value,
                    error,
                )
                raise
            except (
                json.JSONDecodeError,
                KeyError,
                OverflowError,
                TypeError,
                ValueError,
            ) as error:
                self._logger.exception("FFprobe returned invalid JSON for %s", source)
                raise MediaProbeError(
                    MediaProbeErrorCode.INVALID_OUTPUT,
                    "FFprobe returned invalid metadata.",
                ) from error
        finally:
            with self._process_lock:
                if self._process is process:
                    self._process = None
            self._cancel_requested.clear()

    def cancel(self) -> None:
        self._cancel_requested.set()
        with self._process_lock:
            process = self._process
        if process is not None:
            self._stop_process(process)

    def _stop_process(self, process: subprocess.Popen[str]) -> None:
        with self._termination_lock:
            if process.poll() is not None:
                return
            try:
                process.terminate()
                process.wait(timeout=_PROCESS_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                self._logger.warning("FFprobe did not terminate; killing it")
                process.kill()
                process.wait()
            except OSError:
                self._logger.exception("FFprobe termination failed")
                if process.poll() is None:
                    process.kill()
                    process.wait()

    def _command(self, source: Path) -> list[str]:
        return [
            str(self._executable),
            '-protocol_whitelist','file','-format_whitelist','mov,matroska,webm,avi,wav,mp3,flac,ogg,aac',
            '-enable_drefs','0','-use_absolute_path','0','-probesize','8388608','-analyzeduration','10000000',
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_packets",
            "-show_entries",
            "stream:format:packet=stream_index,pts,duration",
            "-read_intervals",
            f"%+#{_NAVIGATION_PACKET_LIMIT}",
            str(source),
        ]

    @classmethod
    def _parse(cls, payload: Any, source: Path) -> MediaMetadata:
        if not isinstance(payload, dict):
            raise MediaProbeError(
                MediaProbeErrorCode.INVALID_OUTPUT, "FFprobe JSON root is not an object."
            )
        streams = payload.get("streams")
        format_data = payload.get("format")
        if not isinstance(streams, list) or not isinstance(format_data, dict):
            raise MediaProbeError(
                MediaProbeErrorCode.INVALID_OUTPUT, "FFprobe metadata is incomplete."
            )

        try:
            stream, width, height = cls._first_usable_video(streams)
        except MediaProbeError as error:
            if error.code != MediaProbeErrorCode.NO_VIDEO_STREAM:raise
            audio=[s for s in streams if isinstance(s,dict) and s.get('codec_type')=='audio']
            if (not 1<=len(audio)<=8 or any(s.get('codec_type') not in ('audio',) for s in streams)
                    or any(s.get('codec_name') not in {'aac','mp3','pcm_s16le','pcm_s24le','pcm_s32le','pcm_f32le','flac','vorbis','opus','alac'} for s in audio)):
                raise
            sound=audio[0]
            if not (0<int(sound.get('sample_rate',0))<=768000 and 0<int(sound.get('channels',0))<=32):raise
            duration=cls._duration(format_data.get('duration'),sound.get('duration'))
            if duration is None or duration<=0:raise
            return MediaMetadata(source.name,source,format_data['format_name'],duration,'',0,0,None,
                info_facts=cls._info_facts(format_data,{},streams),
                audio_streams=tuple(f"Audio {i+1} · {stream['codec_name']} · {stream.get('channels', '?')} ch"
                                    for i, stream in enumerate(audio)),audio_only=True)
        codec = stream.get("codec_name")
        format_name = format_data.get("format_name")
        if (
            not isinstance(codec, str)
            or not codec
            or not isinstance(format_name, str)
            or not format_name
        ):
            raise MediaProbeError(
                MediaProbeErrorCode.INVALID_OUTPUT,
                "Required codec or format fields are missing.",
            )

        navigation_rate, navigation_count = cls._navigation_basis(payload, stream)
        return MediaMetadata(
            source_name=source.name,
            source_path=source,
            format_name=format_name,
            duration_seconds=cls._duration(
                format_data.get("duration"), stream.get("duration")
            ),
            video_codec=codec,
            width=width,
            height=height,
            frame_rate=cls._frame_rate(stream),
            navigation_frame_rate=navigation_rate,
            navigation_frame_count=navigation_count,
            navigation_rate_ratio=str(Fraction(stream['avg_frame_rate'])) if navigation_rate else None,
            navigation_time_base=stream.get('time_base') if navigation_rate else None,
            navigation_end_us=round(Fraction(str(stream['duration'])) * 1_000_000) if navigation_rate else None,
            info_facts=cls._info_facts(format_data, stream, streams),
            sample_aspect_ratio=str(sample_aspect(stream.get("sample_aspect_ratio"))).replace("/", ":"),
            audio_streams=tuple(f"Audio {i+1} · {s.get('codec_name', 'unknown')} · {s.get('channels', '?')} ch"
                               + (" · " + s.get('tags', {}).get('title', '') if s.get('tags', {}).get('title') else '')
                               for i, s in enumerate(s for s in streams if isinstance(s, dict) and s.get('codec_type') == 'audio')),
        )

    @staticmethod
    def _info_facts(container, video, streams):
        def text(value):
            return value.strip() if isinstance(value, str) and value.strip() else None
        def positive(value):
            try:
                number = int(value)
                return number if number > 0 and str(number) == str(value) else None
            except (ValueError, TypeError, OverflowError):
                return None
        rate = None
        rate_basis = None
        for key in ('avg_frame_rate', 'r_frame_rate'):
            try:
                candidate = Fraction(video.get(key, ''))
                if candidate > 0:
                    rate = str(candidate)
                    if '/' not in rate: rate += '/1'
                    rate_basis = 'average' if key == 'avg_frame_rate' else 'nominal'
                    break
            except (ValueError, TypeError, ZeroDivisionError):
                pass
        audio = next((s for s in streams if isinstance(s, dict) and s.get('codec_type') == 'audio'
                      and text(s.get('codec_name'))), {})
        return MediaInfoFacts(
            container_long_name=text(container.get('format_long_name')),
            video_codec_long_name=text(video.get('codec_long_name')),
            video_profile=text(video.get('profile')),
            video_tag=text(video.get('codec_tag_string')),
            pixel_format=text(video.get('pix_fmt')),
            bit_depth=positive(video.get('bits_per_raw_sample')),
            frame_rate_ratio=rate,
            frame_rate_basis=rate_basis,
            audio_codec=text(audio.get('codec_name')),
            audio_codec_long_name=text(audio.get('codec_long_name')),
            audio_sample_rate=positive(audio.get('sample_rate')),
            audio_channels=positive(audio.get('channels')),
            audio_layout=text(audio.get('channel_layout')),
        )

    @classmethod
    def _navigation_basis(cls, payload: dict, stream: dict) -> tuple[float | None, int | None]:
        """Stage A: consistent stream basis plus a small prefix contradiction check.

        This establishes eligibility, never per-step proof. Local requests must
        independently establish current/adjacent decoded presentation timing.
        """
        basis = cfr_stream_basis(stream)
        packets = payload.get("packets")
        if basis is None or not isinstance(packets, list) or not 2 <= len(packets) <= _NAVIGATION_PACKET_LIMIT:
            return None, None
        rate, time_base, count = basis
        try:
            video = [p for p in packets if p.get('stream_index') == stream['index']]
            if len(video) < 2 or any(type(p.get(k)) is not int for p in video for k in ('pts', 'duration')):
                return None, None
            pts = sorted(p['pts'] for p in video)
            delta = 1 / rate / time_base
            if (pts[0] != 0 or any(b-a != delta for a,b in zip(pts, pts[1:]))
                    or any(p['duration'] != delta for p in video)):
                return None, None
            return float(rate), count
        except (AttributeError, KeyError, TypeError, ValueError, ZeroDivisionError):
            return None, None

    @classmethod
    def _first_usable_video(
        cls, streams: list[Any]
    ) -> tuple[dict[str, Any], int, int]:
        invalid_dimensions = False
        for stream in streams:
            if not isinstance(stream, dict) or stream.get("codec_type") != "video":
                continue
            disposition = stream.get("disposition")
            if isinstance(disposition, dict) and disposition.get("attached_pic"):
                continue
            try:
                width = cls._dimension(stream.get("width"))
                height = cls._dimension(stream.get("height"))
            except MediaProbeError:
                invalid_dimensions = True
                continue
            return stream, width, height
        if invalid_dimensions:
            raise MediaProbeError(
                MediaProbeErrorCode.INVALID_OUTPUT,
                "Video dimensions are invalid.",
            )
        raise MediaProbeError(
            MediaProbeErrorCode.NO_VIDEO_STREAM,
            "No usable video stream was reported.",
        )

    @staticmethod
    def _dimension(value: Any) -> int:
        try:
            number = float(value)
        except (OverflowError, TypeError, ValueError) as error:
            raise MediaProbeError(
                MediaProbeErrorCode.INVALID_OUTPUT,
                "Video dimensions are invalid.",
            ) from error
        if (
            not math.isfinite(number)
            or number <= 0
            or number > _MAX_DIMENSION
            or not number.is_integer()
        ):
            raise MediaProbeError(
                MediaProbeErrorCode.INVALID_OUTPUT,
                "Video dimensions are invalid.",
            )
        return int(number)

    @staticmethod
    def _duration(*values: Any) -> float | None:
        for value in values:
            try:
                number = float(value)
            except (OverflowError, TypeError, ValueError):
                continue
            if (
                math.isfinite(number)
                and 0 <= number <= _MAX_DURATION_SECONDS
            ):
                return number
        return None

    @classmethod
    def _frame_rate(cls, stream: dict[str, Any]) -> float | None:
        for name in ("avg_frame_rate", "r_frame_rate"):
            rate = cls._valid_rate(stream.get(name))
            if rate is not None:
                return rate
        try:
            return cls._bounded_rate(
                int(stream["nb_frames"]) / float(stream["duration"])
            )
        except (
            KeyError,
            OverflowError,
            TypeError,
            ValueError,
            ZeroDivisionError,
        ):
            return None

    @staticmethod
    def _valid_rate(value: Any) -> float | None:
        try:
            return FfprobeMediaMetadataProbe._bounded_rate(float(Fraction(str(value))))
        except (OverflowError, TypeError, ValueError, ZeroDivisionError):
            return None

    @staticmethod
    def _bounded_rate(rate: float) -> float | None:
        return rate if math.isfinite(rate) and 0 < rate <= 1000 else None
