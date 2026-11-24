"""Four managed child phases, strict Crop eligibility and staged validation."""

import json
import logging
import math
from pathlib import Path
import subprocess

from mediaforge.features.media.crop import CropControl, CropError, CropErrorCode, CropRequest, CropResult
from mediaforge.infrastructure.ffmpeg.child_process import ChildProcesses
from mediaforge.infrastructure.ffmpeg.encoder_capability import has_libx264
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegRuntime
from mediaforge.infrastructure.ffmpeg.video_duration import positive_duration, same_video_duration_tag
from mediaforge.infrastructure.filesystem.crop_output import CropOutput


class FfmpegCropExecutor:
    def __init__(self, runtime: FfmpegRuntime | None, *, process_factory=subprocess.Popen,
                 timeout_seconds=3600.0, probe_timeout_seconds=15.0, logger=None):
        self._runtime = runtime
        self._timeout = timeout_seconds
        self._probe_timeout = probe_timeout_seconds
        self._logger = logger or logging.getLogger("mediaforge")
        self._processes = ChildProcesses(process_factory, lambda code: CropError(CropErrorCode(code)), self._logger)

    def command(self, request: CropRequest, staged: Path) -> list[str]:
        assert self._runtime is not None
        rect = request.rectangle
        return [str(self._runtime.ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-n",
                "-noautorotate", "-i", str(request.source), "-map", "0:v:0", "-map", "0:a?",
                "-vf", f"crop={rect.width}:{rect.height}:{rect.x}:{rect.y}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "copy", "-map_chapters", "-1", str(staged)]

    def _probe_command(self, path):
        assert self._runtime is not None
        return [str(self._runtime.ffprobe), "-v", "error", "-of", "json", "-show_entries",
                "format=format_name:stream=codec_type,codec_name,width,height,duration:"
                "stream_disposition=attached_pic:stream_tags=rotate,DURATION:stream_side_data",
                str(path)]

    def execute(self, request: CropRequest, control: CropControl) -> CropResult:
        try:
            control.check()
            self._processes.check_reaped()
            if self._runtime is None:
                raise CropError(CropErrorCode.RUNTIME_UNAVAILABLE)
            request.rectangle.validate(request.source_width, request.source_height)
            source_text = self._run(self._probe_command(request.source), control, self._probe_timeout,
                                    failure=CropErrorCode.PREFLIGHT_FAILED)
            audio_count = self._source(source_text, request)
            inventory = self._run([str(self._runtime.ffmpeg), "-hide_banner", "-encoders"],
                                  control, self._probe_timeout, failure=CropErrorCode.ENCODER_UNAVAILABLE)
            # Six FFmpeg capability flags, then an exact encoder name token.
            if not has_libx264(inventory):
                raise CropError(CropErrorCode.ENCODER_UNAVAILABLE)
            control.check()
            with CropOutput(request.source, self._logger) as output:
                assert output.staged is not None
                self._run(self.command(request, output.staged), control, self._timeout, output=output)
                control.check()
                if not output.staged.is_file() or output.staged.stat().st_size == 0:
                    raise CropError(CropErrorCode.INVALID_OUTPUT)
                staged_text = self._run(self._probe_command(output.staged), control, self._probe_timeout,
                                       failure=CropErrorCode.VALIDATION_FAILED, output=output)
                self._validate_output(staged_text, request, audio_count)
                control.check()
                return CropResult(output.publish(control), request.rectangle.width, request.rectangle.height)
        except Exception as error:
            self._logger.exception("Crop execution failed for %s", request.source)
            if isinstance(error, CropError) and error.code == CropErrorCode.LIFECYCLE_FAILED:
                raise
            control.check()
            raise

    def _run(self, command, control, timeout, *, failure=None, output=None):
        try:
            return self._processes.run(command, control, timeout, output=output)
        except CropError as error:
            if failure is not None and error.code not in (CropErrorCode.CANCELLED, CropErrorCode.LIFECYCLE_FAILED):
                raise CropError(failure) from error
            raise
        except (OSError, subprocess.SubprocessError) as error:
            if failure is not None:
                raise CropError(failure) from error
            raise

    @staticmethod
    def _payload(text):
        def reject(value):
            raise ValueError(f"Non-finite JSON: {value}")
        payload = json.loads(text, parse_constant=reject)
        if not isinstance(payload, dict) or not isinstance(payload.get("streams"), list):
            raise ValueError("Missing streams")
        streams = payload["streams"]
        if any(not isinstance(stream, dict) for stream in streams):
            raise ValueError("Malformed stream")
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        # Never skip a first video that cannot match the product's exact 0:v:0 map.
        if video is None or video.get("disposition", {}).get("attached_pic"):
            raise ValueError("No usable first video")
        if not video.get("codec_name") or any(type(video.get(key)) is not int or video[key] <= 0
                                              for key in ("width", "height")):
            raise ValueError("Invalid first video")
        return payload, video, sum(stream.get("codec_type") == "audio" for stream in streams)

    @staticmethod
    def _orientation(video):
        def zero(value):
            try:
                number = float(value)
                return math.isfinite(number) and number == 0
            except (TypeError, ValueError, OverflowError):
                return False
        tags = video.get("tags", {})
        if not isinstance(tags, dict):
            raise CropError(CropErrorCode.UNSUPPORTED_ORIENTATION)
        for key, value in tags.items():
            if key.lower() == "rotate" and not zero(value):
                raise CropError(CropErrorCode.UNSUPPORTED_ORIENTATION)
        sides = video.get("side_data_list", [])
        if not isinstance(sides, list):
            raise CropError(CropErrorCode.UNSUPPORTED_ORIENTATION)
        for side in sides:
            if not isinstance(side, dict):
                raise CropError(CropErrorCode.UNSUPPORTED_ORIENTATION)
            if "rotation" in side and not zero(side["rotation"]):
                raise CropError(CropErrorCode.UNSUPPORTED_ORIENTATION)
            if side.get("side_data_type", "").lower() == "display matrix" or "displaymatrix" in side:
                matrix = side.get("displaymatrix")
                if not isinstance(matrix, str):
                    raise CropError(CropErrorCode.UNSUPPORTED_ORIENTATION)
                try:
                    rows = [line.split(":", 1)[1].split() for line in matrix.strip().splitlines()]
                    if len(rows) != 3 or any(len(row) != 3 for row in rows):
                        raise ValueError("Invalid matrix")
                    values = [int(value) for row in rows for value in row]
                except (ValueError, IndexError):
                    raise CropError(CropErrorCode.UNSUPPORTED_ORIENTATION) from None
                if values != [65536, 0, 0, 0, 65536, 0, 0, 0, 1073741824]:
                    raise CropError(CropErrorCode.UNSUPPORTED_ORIENTATION)

    @classmethod
    def _source(cls, text, request):
        try:
            payload, video, audio_count = cls._payload(text)
            aliases = {name.strip().lower() for name in payload["format"]["format_name"].split(",")}
            required = {".mp4": {"mp4", "mov"}, ".mov": {"mp4", "mov"}, ".mkv": {"matroska"}}
            if not aliases.intersection(required.get(request.source.suffix.lower(), set())):
                raise CropError(CropErrorCode.UNSUPPORTED_CONTAINER)
            cls._orientation(video)
            if (video["width"], video["height"]) != (request.source_width, request.source_height):
                raise CropError(CropErrorCode.SOURCE_CHANGED)
            request.rectangle.validate(video["width"], video["height"])
            return audio_count
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
            raise CropError(CropErrorCode.PREFLIGHT_FAILED) from error

    @staticmethod
    def _video_duration(video):
        # Accepted Crop-v1 selection: a present primary field is authoritative,
        # even when invalid. Tag fallback is only for an absent primary field.
        if "duration" in video:
            return positive_duration(video["duration"])
        return same_video_duration_tag(video)

    @classmethod
    def _validate_output(cls, text, request, expected_audio):
        try:
            _, video, audio_count = cls._payload(text)
            if (video["codec_name"] != "h264" or video["width"] != request.rectangle.width
                    or video["height"] != request.rectangle.height or audio_count != expected_audio):
                raise ValueError("Unexpected output streams")
            cls._video_duration(video)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
            raise CropError(CropErrorCode.VALIDATION_FAILED) from error
