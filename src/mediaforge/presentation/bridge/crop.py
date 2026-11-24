"""Crop numeric input, local validation and Application snapshot presentation."""

import re

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from mediaforge.application.contracts.crop import CropJob, CropUseCase


class CropViewModel(QObject):
    changed = Signal()

    def __init__(self, use_case: CropUseCase | None, parent=None):
        super().__init__(parent)
        self._use_case = use_case
        self._values = ["0", "0", "", ""]
        self._dimensions = (0, 0)
        self._available = False
        self._closed = False
        self._shutdown_error = None
        self._job = CropJob()
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
        self._values = ["0", "0", *(str(value - value % 2) if self._available else "" for value in self._dimensions)]
        self._job = CropJob()
        self.changed.emit()

    def _set_value(self, index, value):
        if not self.busy:
            self._values[index] = value
            self.changed.emit()

    xText = Property(str, lambda self: self._values[0], lambda self, value: self._set_value(0, value), notify=changed)
    yText = Property(str, lambda self: self._values[1], lambda self, value: self._set_value(1, value), notify=changed)
    widthText = Property(str, lambda self: self._values[2], lambda self, value: self._set_value(2, value), notify=changed)
    heightText = Property(str, lambda self: self._values[3], lambda self, value: self._set_value(3, value), notify=changed)

    def _rectangle(self):
        if any(not re.fullmatch(r"[+-]?[0-9]+", value.strip()) for value in self._values):
            return None
        try:
            values = tuple(int(value.strip()) for value in self._values)
        except ValueError:
            return None
        x, y, width, height = values
        if (x < 0 or y < 0 or width < 16 or height < 16 or any(value % 2 for value in values)
                or x + width > self._dimensions[0] or y + height > self._dimensions[1]):
            return None
        return values

    @Property(bool, notify=changed)
    def busy(self):
        return self._use_case is not None and self._use_case.busy

    @Property(bool, notify=changed)
    def canEdit(self):
        return self._use_case is not None and self._available and not self.busy and not self._closed

    @Property(bool, notify=changed)
    def canCrop(self):
        return self.canEdit and self._rectangle() is not None

    @Property(bool, notify=changed)
    def canCancel(self):
        return self.busy and self._job.state == "Running" and not self._closed

    @Property(str, notify=changed)
    def validationMessage(self):
        if not self._available:
            return "Open a video with frame dimensions of at least 16 × 16."
        if self._rectangle() is None:
            return "Use even integer pixels, at least 16 × 16, within the source frame."
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
    def startCrop(self):
        if not self.canCrop:
            return False
        started = self._use_case.start(*self._rectangle())
        self.refresh()
        if started:
            self._timer.start()
        return started

    @Slot()
    def cancelCrop(self):
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
