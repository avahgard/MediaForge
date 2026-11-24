"""Managed exact Resize execution, preflight and staged validation."""

import json
import logging
import math
from pathlib import Path
import subprocess

from mediaforge.features.media.resize import (
    ResizeControl, ResizeError, ResizeErrorCode, ResizeRequest, ResizeResult,
)
from mediaforge.infrastructure.ffmpeg.child_process import ChildProcesses
from mediaforge.infrastructure.ffmpeg.encoder_capability import has_libx264
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegRuntime
from mediaforge.infrastructure.ffmpeg.video_duration import first_video_duration
from mediaforge.infrastructure.filesystem.resize_output import ResizeOutput
from mediaforge.features.media.reformat import GeometryError, plan, sample_aspect


def reformat_filters(geometry, pixel_format="yuv420p", working_format="yuv444p"):
    """Existing accepted rasterization, also used after Crop in combined Export."""
    size = geometry.output
    g = geometry.encoding_window()
    if geometry.placement == "stretch":
        return f"scale={size.width}:{size.height}:flags=lanczos,setsar=1/1"
    filters = "format=" + working_format
    if (g.source_width, g.source_height) != (geometry.input_width, geometry.input_height):
        filters += f",crop={g.source_width}:{g.source_height}:{g.source_x}:{g.source_y}:exact=1"
    for step in geometry.encoding_steps():
        if step[0] == "scale":
            filters += f",scale={step[1]}:{step[2]}:flags=lanczos"
        else:
            filters += f",crop={step[1]}:{step[2]}:{step[3]}:{step[4]}:exact=1"
    filters += ",setsar=1/1"
    if geometry.placement == "fit":
        filters += f",pad={size.width}:{size.height}:{g.x}:{g.y}:color=black"
    return filters + ",format=" + pixel_format


class FfmpegResizeExecutor:
    def __init__(self, runtime: FfmpegRuntime | None, *, process_factory=subprocess.Popen,
                 timeout_seconds=3600.0, probe_timeout_seconds=15.0, logger=None):
        self._runtime = runtime
        self._timeout = timeout_seconds
        self._probe_timeout = probe_timeout_seconds
        self._logger = logger or logging.getLogger("mediaforge")
        self._processes = ChildProcesses(process_factory, lambda code: ResizeError(ResizeErrorCode(code)), self._logger)

    def command(self, request: ResizeRequest, staged: Path) -> list[str]:
        assert self._runtime is not None
        size = request.dimensions
        filters = f"scale={size.width}:{size.height}:flags=lanczos,setsar=1/1"
        if request.reformat is not None:
            geometry = request.reformat
            expected = plan(request.source_width, request.source_height, size, geometry.placement, str(geometry.sar))
            if geometry != expected:
                raise ResizeError(ResizeErrorCode.INVALID_DIMENSIONS)
            try:
                geometry.encoding_window()
            except GeometryError as error:
                raise ResizeError(ResizeErrorCode.INVALID_DIMENSIONS, str(error)) from error
            filters = reformat_filters(geometry)
        return [str(self._runtime.ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-n",
                "-noautorotate", "-i", str(request.source), "-map", "0:v:0", "-map", "0:a?",
                "-vf", filters,
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "copy", "-fps_mode:v:0", "passthrough", "-map_chapters", "-1", str(staged)]

    def _probe_command(self, path):
        assert self._runtime is not None
        return [str(self._runtime.ffprobe), "-v", "error", "-of", "json", "-show_entries",
                "format=format_name:stream=codec_type,codec_name,width,height,duration,sample_aspect_ratio:"
                "stream_disposition=attached_pic:stream_tags=rotate,DURATION:stream_side_data",
                str(path)]

    def execute(self, request: ResizeRequest, control: ResizeControl) -> ResizeResult:
        try:
            control.check()
            self._processes.check_reaped()
            if self._runtime is None:
                raise ResizeError(ResizeErrorCode.RUNTIME_UNAVAILABLE)
            request.dimensions.validate()
            source_text = self._run(self._probe_command(request.source), control, self._probe_timeout,
                                    failure=ResizeErrorCode.PREFLIGHT_FAILED)
            audio_count, source_duration = self._source(source_text, request)
            inventory = self._run([str(self._runtime.ffmpeg), "-hide_banner", "-encoders"],
                                  control, self._probe_timeout, failure=ResizeErrorCode.ENCODER_UNAVAILABLE)
            if not has_libx264(inventory):
                raise ResizeError(ResizeErrorCode.ENCODER_UNAVAILABLE)
            control.check()
            with ResizeOutput(request.source, self._logger) as output:
                assert output.staged is not None
                self._run(self.command(request, output.staged), control, self._timeout, output=output)
                control.check()
                if not output.staged.is_file() or output.staged.stat().st_size == 0:
                    raise ResizeError(ResizeErrorCode.INVALID_OUTPUT)
                staged_text = self._run(self._probe_command(output.staged), control, self._probe_timeout,
                                        failure=ResizeErrorCode.VALIDATION_FAILED, output=output)
                self._validate_output(staged_text, request, audio_count, source_duration)
                control.check()
                size = request.dimensions
                return ResizeResult(output.publish(control), size.width, size.height)
        except Exception as error:
            self._logger.exception("Resize execution failed for %s", request.source)
            if isinstance(error, ResizeError) and error.code == ResizeErrorCode.LIFECYCLE_FAILED:
                raise
            control.check()
            raise

    def _run(self, command, control, timeout, *, failure=None, output=None):
        try:
            return self._processes.run(command, control, timeout, output=output)
        except ResizeError as error:
            if failure is not None and error.code not in (ResizeErrorCode.CANCELLED, ResizeErrorCode.LIFECYCLE_FAILED):
                raise ResizeError(failure) from error
            raise
        except (OSError, subprocess.SubprocessError) as error:
            if failure is not None:
                raise ResizeError(failure) from error
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
            raise ResizeError(ResizeErrorCode.UNSUPPORTED_ORIENTATION)
        for key, value in tags.items():
            if key.lower() == "rotate" and not zero(value):
                raise ResizeError(ResizeErrorCode.UNSUPPORTED_ORIENTATION)
        sides = video.get("side_data_list", [])
        if not isinstance(sides, list):
            raise ResizeError(ResizeErrorCode.UNSUPPORTED_ORIENTATION)
        for side in sides:
            if not isinstance(side, dict):
                raise ResizeError(ResizeErrorCode.UNSUPPORTED_ORIENTATION)
            if "rotation" in side and not zero(side["rotation"]):
                raise ResizeError(ResizeErrorCode.UNSUPPORTED_ORIENTATION)
            if side.get("side_data_type", "").lower() == "display matrix" or "displaymatrix" in side:
                matrix = side.get("displaymatrix")
                if not isinstance(matrix, str):
                    raise ResizeError(ResizeErrorCode.UNSUPPORTED_ORIENTATION)
                try:
                    rows = [line.split(":", 1)[1].split() for line in matrix.strip().splitlines()]
                    values = [int(value) for row in rows for value in row]
                    if len(rows) != 3 or any(len(row) != 3 for row in rows):
                        raise ValueError("Invalid matrix")
                except (ValueError, IndexError):
                    raise ResizeError(ResizeErrorCode.UNSUPPORTED_ORIENTATION) from None
                if values != [65536, 0, 0, 0, 65536, 0, 0, 0, 1073741824]:
                    raise ResizeError(ResizeErrorCode.UNSUPPORTED_ORIENTATION)

    @classmethod
    def _source(cls, text, request):
        try:
            payload, video, audio_count = cls._payload(text)
            aliases = {name.strip().lower() for name in payload["format"]["format_name"].split(",")}
            required = {".mp4": {"mp4", "mov"}, ".mov": {"mp4", "mov"}, ".mkv": {"matroska"}}
            if not aliases.intersection(required.get(request.source.suffix.lower(), set())):
                raise ResizeError(ResizeErrorCode.UNSUPPORTED_CONTAINER)
            cls._orientation(video)
            if (video["width"], video["height"]) != (request.source_width, request.source_height):
                raise ResizeError(ResizeErrorCode.SOURCE_CHANGED)
            if request.reformat is not None:
                if sample_aspect(video.get("sample_aspect_ratio")) != request.reformat.sar:
                    raise ResizeError(ResizeErrorCode.SOURCE_CHANGED)
                if request.reformat != plan(request.source_width, request.source_height, request.dimensions,
                                            request.reformat.placement, str(request.reformat.sar)):
                    raise ResizeError(ResizeErrorCode.INVALID_DIMENSIONS)
            return audio_count, first_video_duration(video)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
            raise ResizeError(ResizeErrorCode.PREFLIGHT_FAILED) from error

    @classmethod
    def _validate_output(cls, text, request, expected_audio, source_duration):
        try:
            _, video, audio_count = cls._payload(text)
            size = request.dimensions
            if request.reformat is not None:
                expected = plan(request.source_width, request.source_height, size,
                                request.reformat.placement, str(request.reformat.sar))
                if request.reformat != expected:
                    raise ValueError("Unexpected framing plan")
                expected.encoding_steps()
            if (video["codec_name"] != "h264" or video["width"] != size.width
                    or video["height"] != size.height or video.get("sample_aspect_ratio") != "1:1"
                    or audio_count != expected_audio):
                raise ValueError("Unexpected output streams")
            cls._orientation(video)
            output_duration = first_video_duration(video)
            if abs(output_duration - source_duration) > 0.250:
                raise ValueError("Video duration changed")
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, ResizeError) as error:
            raise ResizeError(ResizeErrorCode.VALIDATION_FAILED) from error
