"""A single asynchronous exact Resize operation."""

from dataclasses import replace
import logging
from pathlib import Path
from threading import RLock, Thread

from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.contracts.resize import ResizeJob
from mediaforge.application.contracts.reformat import GeometryError, plan
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.features.media.resize import (
    ResizeControl, ResizeDimensions, ResizeError, ResizeErrorCode, ResizeExecutor,
    ResizeRequest,
)

SHUTDOWN_TIMEOUT_SECONDS = 6.0
_MESSAGES = {
    ResizeErrorCode.BUSY: "Media processing is busy. Wait for the current operation.",
    ResizeErrorCode.NO_SOURCE: "Open a video with valid frame dimensions first.",
    ResizeErrorCode.INVALID_DIMENSIONS: "Use even integer dimensions from 16 to 7680 within the 8K pixel limit.",
    ResizeErrorCode.SOURCE_UNAVAILABLE: "The source is unavailable. Reopen the media file.",
    ResizeErrorCode.SOURCE_CHANGED: "Source dimensions have changed. Reopen the media file.",
    ResizeErrorCode.UNSUPPORTED_CONTAINER: "Unsupported output container for Resize v1. Use matching MP4, MOV or MKV media.",
    ResizeErrorCode.UNSUPPORTED_ORIENTATION: "Unsupported display orientation for Resize v1. Choose an untransformed source.",
    ResizeErrorCode.PREFLIGHT_FAILED: "Resize could not inspect the source. Reopen the media file.",
    ResizeErrorCode.ENCODER_UNAVAILABLE: "Resize encoder unavailable. Verify the managed runtime.",
    ResizeErrorCode.RUNTIME_UNAVAILABLE: "The FFmpeg runtime is unavailable. Verify the managed runtime.",
    ResizeErrorCode.OUTPUT_UNAVAILABLE: "Cannot create output beside the source. Check folder permissions.",
    ResizeErrorCode.START_FAILED: "Could not start Resize processing. Verify the runtime and retry.",
    ResizeErrorCode.PROCESS_FAILED: "Resize failed for this source. Check the log or try another file.",
    ResizeErrorCode.TIMEOUT: "Resize processing did not finish in time. Try again.",
    ResizeErrorCode.LIFECYCLE_FAILED: "The media process could not be stopped. Check the application log.",
    ResizeErrorCode.INVALID_OUTPUT: "Resize produced no usable output. Try again.",
    ResizeErrorCode.VALIDATION_FAILED: "The resized video could not be validated. Try again.",
    ResizeErrorCode.PUBLICATION_FAILED: "Could not publish the resize. Check the destination folder and retry.",
    ResizeErrorCode.CANCELLED: "Resize cancelled. You can retry.",
}


class ResizeService:
    def __init__(self, executor: ResizeExecutor, logger=None, *, gate=None):
        self._executor = executor
        self._processing_gate = gate if gate is not None else MediaProcessingGate()
        self._logger = logger or logging.getLogger("mediaforge")
        self._lock = RLock()
        self._source: MediaMetadataDto | None = None
        self._job = ResizeJob()
        self._thread: Thread | None = None
        self._control: ResizeControl | None = None
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
            self._job = ResizeJob(job_id=self._generation)
            return True

    def snapshot(self) -> ResizeJob:
        with self._lock:
            return self._job

    def start(self, width: int, height: int) -> bool:
        return self._start(width, height)

    def start_reformat(self, width: int, height: int, placement: str) -> bool:
        return self._start(width, height, placement)

    def _start(self, width: int, height: int, placement: str | None = None) -> bool:
        with self._lock:
            if self.busy or self._closed:
                return False
            self._generation += 1
            generation = self._generation
            owner = self._processing_gate.acquire()
            if owner is None:
                self._job = self._error_job(ResizeErrorCode.BUSY, generation)
                return False
            handed_to_worker = False
            try:
                source = self._source
                if (source is None or not source.video_codec
                        or type(source.width) is not int or type(source.height) is not int
                        or source.width <= 0 or source.height <= 0):
                    raise ResizeError(ResizeErrorCode.NO_SOURCE)
                dimensions = ResizeDimensions(width, height)
                dimensions.validate()
                geometry = None
                if placement is not None:
                    try:
                        geometry = plan(source.width, source.height, dimensions, placement, source.sample_aspect_ratio)
                        geometry.encoding_geometry()
                    except GeometryError as error:
                        raise ResizeError(ResizeErrorCode.INVALID_DIMENSIONS, str(error)) from error
                path = Path(source.source_path)
                if not path.is_absolute() or not path.is_file():
                    raise ResizeError(ResizeErrorCode.SOURCE_UNAVAILABLE)
                with path.open("rb"):
                    pass
                request = ResizeRequest(path, dimensions, source.width, source.height, geometry)
                control = ResizeControl()
                self._control = control
                self._job = ResizeJob(job_id=generation, state="Running", stage="processing", message="Resize running…")
                try:
                    self._thread = Thread(target=self._run, args=(request, control, generation, owner), name="mediaforge-resize")
                    self._thread.start()
                except RuntimeError as error:
                    self._thread = None
                    raise ResizeError(ResizeErrorCode.START_FAILED) from error
                handed_to_worker = True
                return True
            except ResizeError as error:
                self._job = self._error_job(error.code, generation)
                return False
            except (OSError, ValueError, TypeError):
                self._job = self._error_job(ResizeErrorCode.SOURCE_UNAVAILABLE, generation)
                return False
            finally:
                if not handed_to_worker:
                    self._processing_gate.release(owner)

    def _run(self, request, control, generation, owner):
        try:
            try:
                result = self._executor.execute(request, control)
                control.check()
                job = ResizeJob(job_id=generation, state="Completed", stage="done", message="Resize completed.",
                                result=str(result.output), width=result.width, height=result.height)
            except Exception as error:
                self._logger.exception("Resize operation failed")
                code = error.code if isinstance(error, ResizeError) else ResizeErrorCode.PROCESS_FAILED
                if code != ResizeErrorCode.LIFECYCLE_FAILED:
                    try:
                        control.check()
                    except ResizeError:
                        code = ResizeErrorCode.CANCELLED
                job = self._error_job(code, generation)
            with self._lock:
                if job.error != ResizeErrorCode.LIFECYCLE_FAILED.value:
                    try:
                        control.check()
                    except ResizeError:
                        job = self._error_job(ResizeErrorCode.CANCELLED, generation)
                if generation == self._generation:
                    self._job = job
        finally:
            if control.cleanup_failed:
                self._processing_gate.poison(owner, "resize_child_cleanup_failed")
                self._logger.error("Media processing unavailable: Resize child cleanup unresolved")
            else:
                self._processing_gate.release(owner)

    def _error_job(self, code, generation):
        message = _MESSAGES[code]
        if code == ResizeErrorCode.BUSY and self._processing_gate.failure:
            message = "Media processing is unavailable after a child cleanup failure. Check the application log."
        return ResizeJob(job_id=generation, state="Cancelled" if code == ResizeErrorCode.CANCELLED else "Failed",
                         stage="done", message=message, error=code.value)

    def cancel(self) -> bool:
        with self._lock:
            if (not self.busy or self._job.state != "Running"
                    or self._control is None or not self._control.cancel()):
                return False
            self._job = replace(self._job, state="Cancelling", message="Cancelling Resize…")
            return True

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            self.cancel()
            thread = self._thread
        if thread is not None:
            thread.join(SHUTDOWN_TIMEOUT_SECONDS)
            if thread.is_alive():
                raise RuntimeError("Resize worker did not stop within shutdown deadline")
        if ((self._control is not None and self._control.cleanup_failed)
                or self.snapshot().error == ResizeErrorCode.LIFECYCLE_FAILED.value):
            raise RuntimeError("Resize process termination failed; see diagnostic log")
