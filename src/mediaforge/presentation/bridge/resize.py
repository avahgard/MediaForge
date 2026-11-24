"""Resize numeric input and Application snapshot presentation."""

import re

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from mediaforge.application.contracts.resize import (
    RESIZE_MAX_AXIS, RESIZE_MAX_PIXELS, RESIZE_MIN_AXIS, ResizeJob, ResizeUseCase,
)


class ResizeViewModel(QObject):
    changed = Signal()

    def __init__(self, use_case: ResizeUseCase | None, parent=None):
        super().__init__(parent)
        self._use_case = use_case
        self._values = ["", ""]
        self._dimensions = (0, 0)
        self._available = False
        self._closed = False
        self._shutdown_error = None
        self._job = ResizeJob()
        self._was_busy = False
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self.refresh)

    def set_source(self, source):
        if self._use_case is not None and not self._use_case.set_source(source):
            return
        self._dimensions = (source.width, source.height) if source is not None else (0, 0)
        self._available = (source is not None and bool(source.video_codec)
                           and all(type(value) is int and value >= 16 for value in self._dimensions))
        self._values = [str(value - value % 2) if self._available else "" for value in self._dimensions]
        self._job = ResizeJob()
        self.changed.emit()

    def _set_value(self, index, value):
        if not self.busy:
            self._values[index] = value
            self.changed.emit()

    widthText = Property(str, lambda self: self._values[0], lambda self, value: self._set_value(0, value), notify=changed)
    heightText = Property(str, lambda self: self._values[1], lambda self, value: self._set_value(1, value), notify=changed)

    def _target(self):
        if any(not re.fullmatch(r"[+-]?[0-9]+", value.strip()) for value in self._values):
            return None
        try:
            width, height = (int(value.strip()) for value in self._values)
        except ValueError:
            return None
        if (width < RESIZE_MIN_AXIS or height < RESIZE_MIN_AXIS
                or width > RESIZE_MAX_AXIS or height > RESIZE_MAX_AXIS
                or width % 2 or height % 2 or width * height > RESIZE_MAX_PIXELS):
            return None
        return width, height

    @Property(bool, notify=changed)
    def busy(self):
        return self._use_case is not None and self._use_case.busy

    @Property(bool, notify=changed)
    def canEdit(self):
        return self._use_case is not None and self._available and not self.busy and not self._closed

    @Property(bool, notify=changed)
    def canResize(self):
        return self.canEdit and self._target() is not None

    @Property(bool, notify=changed)
    def canCancel(self):
        return self.busy and self._job.state == "Running" and not self._closed

    @Property(str, notify=changed)
    def validationMessage(self):
        if not self._available:
            return "Open a video with valid frame dimensions."
        if self._target() is None:
            return "Use even integer dimensions from 16 to 7680 within the 8K pixel limit."
        return ""

    @Property(str, notify=changed)
    def sourceDimensions(self):
        return f"{self._dimensions[0]} × {self._dimensions[1]}"

    @Property(str, notify=changed)
    def state(self):
        return self._job.state

    @Property(str, notify=changed)
    def message(self):
        return self._job.message

    @Property(str, notify=changed)
    def outputPath(self):
        return self._job.result

    @Property(str, notify=changed)
    def resultDimensions(self):
        return f"{self._job.width} × {self._job.height}" if self._job.result else ""

    @Slot(result=bool)
    def startResize(self):
        if not self.canResize:
            return False
        started = self._use_case.start(*self._target())
        self.refresh()
        if started:
            self._timer.start()
        return started

    def start_reformat(self, width, height, placement):
        if not self.canEdit:
            return False
        started = self._use_case.start_reformat(width, height, placement)
        self.refresh()
        if started:
            self._timer.start()
        return started

    @Slot()
    def cancelResize(self):
        if self.canCancel:
            self._use_case.cancel()
            self.refresh()

    @Slot()
    def refresh(self):
        if self._closed or self._use_case is None:
            return
        job, busy = self._use_case.snapshot(), self.busy
        if job != self._job or busy != self._was_busy:
            self._job, self._was_busy = job, busy
            self.changed.emit()
        if not busy:
            self._timer.stop()

    def shutdown(self):
        if self._closed:
            if self._shutdown_error is not None:
                raise self._shutdown_error
            return
        self._closed = True
        self._timer.stop()
        try:
            if self._use_case is not None:
                self._use_case.shutdown()
        except Exception as error:
            self._shutdown_error = error
            raise
        finally:
            self.changed.emit()
