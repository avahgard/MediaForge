"""Stream-copy execution with injected validated pair and staged validation."""

import json
import logging
from pathlib import Path
import subprocess

from mediaforge.features.media.trim import TrimControl, TrimError, TrimErrorCode, TrimRequest, TrimResult
from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe
from mediaforge.features.media.metadata import MediaProbeError
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegRuntime
from mediaforge.infrastructure.ffmpeg.child_process import ChildProcesses
from mediaforge.infrastructure.filesystem.trim_output import TrimOutput

class FfmpegTrimExecutor:
    def __init__(self, runtime: FfmpegRuntime | None, *, process_factory=subprocess.Popen,
                 timeout_seconds=3600.0, validation_timeout_seconds=15.0, logger=None):
        self._runtime = runtime
        self._factory = process_factory
        self._timeout = timeout_seconds
        self._validation_timeout = validation_timeout_seconds
        self._logger = logger or logging.getLogger("mediaforge")
        self._processes = ChildProcesses(process_factory, lambda code: TrimError(TrimErrorCode(code)), self._logger)

    def command(self, request: TrimRequest, staged: Path) -> list[str]:
        assert self._runtime is not None
        return [str(self._runtime.ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-n",
                "-ss", str(request.interval.start), "-i", str(request.source),
                "-t", str(request.interval.end - request.interval.start),
                "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
                "-map_chapters", "-1", "-avoid_negative_ts", "make_zero", str(staged)]

    def execute(self, request: TrimRequest, control: TrimControl) -> TrimResult:
        try:
            control.check()
            self._processes.check_reaped()
            if self._runtime is None:
                raise TrimError(TrimErrorCode.RUNTIME_UNAVAILABLE)
            with TrimOutput(request.source, self._logger) as output:
                assert output.staged is not None
                self._run(self.command(request, output.staged), control, self._timeout, output=output)
                control.check()
                if not output.staged.is_file() or output.staged.stat().st_size == 0:
                    raise TrimError(TrimErrorCode.INVALID_OUTPUT)
                command = [str(self._runtime.ffprobe), "-v", "error", "-print_format", "json",
                           "-show_format", "-show_streams", str(output.staged)]
                stdout = self._run(command, control, self._validation_timeout, validating=True, output=output)
                try:
                    payload = json.loads(stdout, parse_constant=self._reject_constant)
                    metadata = FfprobeMediaMetadataProbe._parse(payload, output.staged)
                    if metadata.duration_seconds is None or metadata.duration_seconds <= 0:
                        raise ValueError("Missing positive output duration")
                except (ValueError, TypeError, KeyError, OverflowError, MediaProbeError) as error:
                    raise TrimError(TrimErrorCode.VALIDATION_FAILED) from error
                control.check()
                return TrimResult(output.publish(control))
        except Exception as error:
            self._logger.exception("Trim execution failed for %s", request.source)
            if isinstance(error, TrimError) and error.code == TrimErrorCode.LIFECYCLE_FAILED:
                raise
            control.check()  # Accepted cancellation wins over process failure.
            raise

    @staticmethod
    def _reject_constant(value):
        raise ValueError(f"Non-finite JSON: {value}")

    def _run(self, command, control, timeout, validating=False, output=None):
        return self._processes.run(command, control, timeout, validating, output)
