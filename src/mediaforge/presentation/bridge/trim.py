"""Presentation formatting and polling of the Application-owned Trim job."""

import math

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from mediaforge.application.contracts.trim import TrimJob, TrimUseCase


class TrimViewModel(QObject):
    changed = Signal()

    def __init__(self, use_case: TrimUseCase | None, parent=None):
        super().__init__(parent)
        self._use_case = use_case
        self._start = "0"
        self._end = ""
        self._available = False
        self._closed = False
        self._shutdown_error = None
        self._job = TrimJob()
        self._was_busy = False
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self.refresh)

    def set_source(self, source):
        if self._use_case is not None and not self._use_case.set_source(source):
            return
        self._available = (source is not None and source.duration_seconds is not None
                           and math.isfinite(source.duration_seconds) and source.duration_seconds > 0)
        self._start = "0"
        self._end = str(source.duration_seconds) if self._available else ""
        self._job = TrimJob()
        self.changed.emit()

    @Property(str, notify=changed)
    def startText(self):
        return self._start

    @startText.setter
    def startText(self, value):
        if not self.busy:
            self._start = value
            self.changed.emit()

    @Property(str, notify=changed)
    def endText(self):
        return self._end

    @endText.setter
    def endText(self, value):
        if not self.busy:
            self._end = value
            self.changed.emit()

    @Property(bool, notify=changed)
    def busy(self):
        return self._use_case is not None and self._use_case.busy

    @Property(bool, notify=changed)
    def canTrim(self):
        return self._use_case is not None and self._available and not self.busy and not self._closed

    @Property(bool, notify=changed)
    def canCancel(self):
        return self.busy and self._job.state == "Running" and not self._closed

    @Property(str, notify=changed)
    def state(self):
        return self._job.state

    @Property(str, notify=changed)
    def message(self):
        return self._job.message

    @Property(str, notify=changed)
    def outputPath(self):
        return self._job.result

    @Slot(result=bool)
    def startTrim(self):
        if not self.canTrim:
            return False
        try:
            start, end = float(self._start), float(self._end)
        except ValueError:
            start = end = float("nan")
        started = self._use_case.start(start, end)
        self.refresh()
        if started:
            self._timer.start()
        return started

    @Slot()
    def cancelTrim(self):
        if self.canCancel:
            self._use_case.cancel()
            self.refresh()

    @Slot()
    def refresh(self):
        if self._closed or self._use_case is None:
            return
        job = self._use_case.snapshot()
        busy = self.busy
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
