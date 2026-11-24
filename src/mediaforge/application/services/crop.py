"""A single asynchronous Crop operation covering preflight through publication."""

from dataclasses import replace
import logging
from pathlib import Path
from threading import RLock, Thread

from mediaforge.application.contracts.crop import CropJob
from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.features.media.crop import (
    CropControl, CropError, CropErrorCode, CropExecutor, CropRect, CropRequest,
)

SHUTDOWN_TIMEOUT_SECONDS = 6.0
_MESSAGES = {
    CropErrorCode.BUSY: "Media processing is busy. Wait for the current operation.",
    CropErrorCode.NO_SOURCE: "Open a video with valid frame dimensions first.",
    CropErrorCode.INVALID_RECTANGLE: "Use even integer pixels, at least 16 × 16, within the source frame.",
    CropErrorCode.SOURCE_UNAVAILABLE: "The source is unavailable. Reopen the media file.",
    CropErrorCode.SOURCE_CHANGED: "Source dimensions have changed. Reopen the media file.",
    CropErrorCode.UNSUPPORTED_CONTAINER: "Unsupported output container for Crop v1. Use matching MP4, MOV or MKV media.",
    CropErrorCode.UNSUPPORTED_ORIENTATION: "Unsupported display orientation for Crop v1. Choose an untransformed source.",
    CropErrorCode.PREFLIGHT_FAILED: "Crop could not inspect the source. Reopen the media file.",
    CropErrorCode.ENCODER_UNAVAILABLE: "Crop encoder unavailable. Verify the managed runtime.",
    CropErrorCode.RUNTIME_UNAVAILABLE: "The FFmpeg runtime is unavailable. Verify the managed runtime.",
    CropErrorCode.OUTPUT_UNAVAILABLE: "Cannot create output beside the source. Check folder permissions.",
    CropErrorCode.START_FAILED: "Could not start Crop processing. Verify the runtime and retry.",
    CropErrorCode.PROCESS_FAILED: "Crop failed for this source. Check the log or try another file.",
    CropErrorCode.TIMEOUT: "Crop processing did not finish in time. Try again.",
    CropErrorCode.LIFECYCLE_FAILED: "The media process could not be stopped. Check the application log.",
    CropErrorCode.INVALID_OUTPUT: "Crop produced no usable output. Try again.",
    CropErrorCode.VALIDATION_FAILED: "The cropped video could not be validated. Try again.",
    CropErrorCode.PUBLICATION_FAILED: "Could not publish the crop. Check the destination folder and retry.",
    CropErrorCode.CANCELLED: "Crop cancelled. You can retry.",
}


class CropService:
    def __init__(self, executor: CropExecutor, logger=None, *, gate=None):
        self._executor = executor
        self._processing_gate = gate if gate is not None else MediaProcessingGate()
        self._logger = logger or logging.getLogger("mediaforge")
        self._lock = RLock()
        self._source: MediaMetadataDto | None = None
        self._job = CropJob()
        self._thread: Thread | None = None
        self._control: CropControl | None = None
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
            self._job = CropJob(job_id=self._generation)
            return True

    def snapshot(self) -> CropJob:
        with self._lock:
            return self._job

    def start(self, x: int, y: int, width: int, height: int) -> bool:
        with self._lock:
            if self.busy or self._closed:
                return False
            self._generation += 1
            generation = self._generation
            owner = self._processing_gate.acquire()
            if owner is None:
                self._job = self._error_job(CropErrorCode.BUSY, generation)
                return False
            handed_to_worker = False
            try:
                source = self._source
                if (source is None or not source.video_codec
                        or type(source.width) is not int or type(source.height) is not int
                        or source.width < 16 or source.height < 16):
                    raise CropError(CropErrorCode.NO_SOURCE)
                rectangle = CropRect(x, y, width, height)
                rectangle.validate(source.width, source.height)
                path = Path(source.source_path)
                if not path.is_absolute() or not path.is_file():
                    raise CropError(CropErrorCode.SOURCE_UNAVAILABLE)
                with path.open("rb"):
                    pass
                request = CropRequest(path, rectangle, source.width, source.height)
                control = CropControl()
                self._control = control
                self._job = CropJob(job_id=generation, state="Running", stage="processing", message="Crop running…")
                try:
                    self._thread = Thread(target=self._run, args=(request, control, generation, owner), name="mediaforge-crop")
                    self._thread.start()
                except RuntimeError as error:
                    self._thread = None
                    raise CropError(CropErrorCode.START_FAILED) from error
                handed_to_worker = True
                return True
            except CropError as error:
                self._job = self._error_job(error.code, generation)
                return False
            except (OSError, ValueError, TypeError):
                self._job = self._error_job(CropErrorCode.SOURCE_UNAVAILABLE, generation)
                return False
            finally:
                if not handed_to_worker:
                    self._processing_gate.release(owner)

    def _run(self, request, control, generation, owner):
        try:
            try:
                result = self._executor.execute(request, control)
                control.check()
                job = CropJob(job_id=generation, state="Completed", stage="done", message="Crop completed.",
                              result=str(result.output), width=result.width, height=result.height)
            except Exception as error:
                self._logger.exception("Crop operation failed")
                code = error.code if isinstance(error, CropError) else CropErrorCode.PROCESS_FAILED
                if code != CropErrorCode.LIFECYCLE_FAILED:
                    try:
                        control.check()
                    except CropError:
                        code = CropErrorCode.CANCELLED
                job = self._error_job(code, generation)
            with self._lock:
                # Cancel and terminal snapshot commit use the same Application
                # lock; cancellation cannot be lost after the last error check.
                if job.error != CropErrorCode.LIFECYCLE_FAILED.value:
                    try:
                        control.check()
                    except CropError:
                        job = self._error_job(CropErrorCode.CANCELLED, generation)
                if generation == self._generation:
                    self._job = job
        finally:
            if control.cleanup_failed:
                self._processing_gate.poison(owner, "crop_child_cleanup_failed")
                self._logger.error("Media processing unavailable: Crop child cleanup unresolved")
            else:
                self._processing_gate.release(owner)

    def _error_job(self, code, generation):
        message = _MESSAGES[code]
        if code == CropErrorCode.BUSY and self._processing_gate.failure:
            message = "Media processing is unavailable after a child cleanup failure. Check the application log."
        return CropJob(job_id=generation, state="Cancelled" if code == CropErrorCode.CANCELLED else "Failed",
                       stage="done", message=message, error=code.value)

    def cancel(self) -> bool:
        with self._lock:
            if (not self.busy or self._job.state != "Running"
                    or self._control is None or not self._control.cancel()):
                return False
            self._job = replace(self._job, state="Cancelling", message="Cancelling Crop…")
            return True

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            self.cancel()
            thread = self._thread
        if thread is not None:
            thread.join(SHUTDOWN_TIMEOUT_SECONDS)
            if thread.is_alive():
                raise RuntimeError("Crop worker did not stop within shutdown deadline")
        if ((self._control is not None and self._control.cleanup_failed)
                or self.snapshot().error == CropErrorCode.LIFECYCLE_FAILED.value):
            raise RuntimeError("Crop process termination failed; see diagnostic log")
