"""One Trim job, numeric validation and lifecycle; no concrete Infrastructure."""

from dataclasses import replace
from decimal import Decimal
import logging
import math
from pathlib import Path
from threading import RLock, Thread

from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.contracts.trim import TrimJob
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.features.media.trim import (
    TrimControl, TrimError, TrimErrorCode, TrimExecutor, TrimRange, TrimRequest,
)

END_DURATION_EPSILON = Decimal("0.050")
SHUTDOWN_TIMEOUT_SECONDS = 6.0

_MESSAGES = {
    TrimErrorCode.BUSY: "Media processing is busy. Wait for the current operation.",
    TrimErrorCode.NO_SOURCE: "Open a video with a known duration first.",
    TrimErrorCode.INVALID_RANGE: "Enter a valid Start and End within the media duration.",
    TrimErrorCode.SOURCE_UNAVAILABLE: "The source is unavailable. Reopen the media file.",
    TrimErrorCode.OUTPUT_UNAVAILABLE: "Cannot create output beside the source. Check folder permissions.",
    TrimErrorCode.RUNTIME_UNAVAILABLE: "The FFmpeg runtime is unavailable. Verify the managed runtime.",
    TrimErrorCode.START_FAILED: "Could not start media processing. Verify the runtime and retry.",
    TrimErrorCode.PROCESS_FAILED: "Fast Trim failed for this source. Check the log or try another range.",
    TrimErrorCode.TIMEOUT: "Media processing did not finish in time. Try again.",
    TrimErrorCode.LIFECYCLE_FAILED: "The media process could not be stopped. Check the application log.",
    TrimErrorCode.INVALID_OUTPUT: "Fast Trim produced no usable output. Try another range.",
    TrimErrorCode.VALIDATION_FAILED: "The trimmed video could not be validated. Try another range.",
    TrimErrorCode.PUBLICATION_FAILED: "Could not publish the trim. Check the destination folder and retry.",
    TrimErrorCode.CANCELLED: "Fast Trim cancelled. You can retry.",
}


def normalize_range(start: float, end: float, duration: float) -> TrimRange:
    values = (start, end, duration)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise TrimError(TrimErrorCode.INVALID_RANGE)
    # Decimal strings avoid losing the inclusive 50 ms boundary to binary
    # subtraction (e.g. 10.05 - 10). Do not widen the tolerance with isclose.
    if end > duration:
        if Decimal(str(end)) - Decimal(str(duration)) > END_DURATION_EPSILON:
            raise TrimError(TrimErrorCode.INVALID_RANGE)
        end = duration
    if start < 0 or end <= start or duration <= 0:
        raise TrimError(TrimErrorCode.INVALID_RANGE)
    return TrimRange(float(start), float(end))


class TrimService:
    def __init__(self, executor: TrimExecutor, logger=None, *, gate=None):
        self._executor = executor
        self._processing_gate = gate if gate is not None else MediaProcessingGate()
        self._logger = logger or logging.getLogger("mediaforge")
        self._lock = RLock()
        self._source: MediaMetadataDto | None = None
        self._job = TrimJob()
        self._thread: Thread | None = None
        self._control: TrimControl | None = None
        self._closed = False
        self._generation = 0

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def set_source(self, source: MediaMetadataDto | None) -> bool:
        with self._lock:
            if self.busy or self._closed:
                return False
            self._source = source
            self._generation += 1
            self._job = TrimJob(job_id=self._generation)
            return True

    def snapshot(self) -> TrimJob:
        with self._lock:
            return self._job

    def start(self, start: float, end: float) -> bool:
        with self._lock:
            if self.busy or self._closed:
                return False
            self._generation += 1
            generation = self._generation
            self._job = TrimJob(job_id=generation)
            if self._processing_gate.busy:
                self._fail(TrimErrorCode.BUSY)
                return False
            try:
                source = self._source
                if (source is None or source.duration_seconds is None
                        or not source.video_codec or source.width <= 0 or source.height <= 0):
                    raise TrimError(TrimErrorCode.NO_SOURCE)
                interval = normalize_range(start, end, source.duration_seconds)
                path = Path(source.source_path)
                if not path.is_absolute() or not path.is_file():
                    raise TrimError(TrimErrorCode.SOURCE_UNAVAILABLE)
                with path.open("rb"):
                    pass
            except TrimError as error:
                self._fail(error.code)
                return False
            except (OSError, ValueError, TypeError):
                self._fail(TrimErrorCode.SOURCE_UNAVAILABLE)
                return False
            owner = self._processing_gate.acquire()
            if owner is None:
                self._fail(TrimErrorCode.BUSY)
                return False
            control = TrimControl()
            self._control = control
            self._job = replace(self._job, state="Running", stage="processing", message="Fast Trim running…")
            try:
                self._thread = Thread(target=self._run, args=(TrimRequest(path, interval), control, generation, owner), name="mediaforge-trim")
                self._thread.start()
            except RuntimeError:
                self._logger.exception("Could not start Trim worker")
                self._thread = None
                self._fail(TrimErrorCode.START_FAILED)
                self._processing_gate.release(owner)
                return False
            return True

    def _run(self, request, control, generation, owner):
        try:
            result = self._executor.execute(request, control)
            control.check()
            job = TrimJob(job_id=generation, state="Completed", stage="done", message="Fast Trim completed.", result=str(result.output))
        except TrimError as error:
            code = error.code
            if code != TrimErrorCode.LIFECYCLE_FAILED:
                try:
                    control.check()
                except TrimError:
                    code = TrimErrorCode.CANCELLED
            job = self._error_job(code, generation)
        except Exception:
            self._logger.exception("Unexpected Trim failure")
            job = self._error_job(TrimErrorCode.PROCESS_FAILED, generation)
        with self._lock:
            try:
                if job.error != TrimErrorCode.LIFECYCLE_FAILED.value:
                    try:
                        control.check()
                    except TrimError:
                        job = self._error_job(TrimErrorCode.CANCELLED, generation)
                if generation == self._generation:
                    self._job = job
            finally:
                if control.cleanup_failed:
                    self._processing_gate.poison(owner, "trim_child_cleanup_failed")
                    self._logger.error("Media processing unavailable: Trim child cleanup unresolved")
                else:
                    self._processing_gate.release(owner)

    def _error_job(self, code, generation):
        message = _MESSAGES[code]
        if code == TrimErrorCode.BUSY and self._processing_gate.failure:
            message = "Media processing is unavailable after a child cleanup failure. Check the application log."
        return TrimJob(job_id=generation, state="Cancelled" if code == TrimErrorCode.CANCELLED else "Failed", stage="done", message=message, error=code.value)

    def _fail(self, code):
        self._job = self._error_job(code, self._generation)

    def cancel(self) -> bool:
        with self._lock:
            if (not self.busy or self._job.state != "Running"
                    or self._control is None or not self._control.cancel()):
                return False
            self._job = replace(self._job, state="Cancelling", message="Cancelling Fast Trim…")
            return True

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            self.cancel()
            thread = self._thread
        if thread is not None:
            thread.join(SHUTDOWN_TIMEOUT_SECONDS)
            if thread.is_alive():
                raise RuntimeError("Trim worker did not stop within shutdown deadline")
        if ((self._control is not None and self._control.cleanup_failed)
                or self.snapshot().error == TrimErrorCode.LIFECYCLE_FAILED.value):
            raise RuntimeError("Trim process termination failed; see diagnostic log")
