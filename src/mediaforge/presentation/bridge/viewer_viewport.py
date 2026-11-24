"""Viewer-only anchored zoom and bounded pan in Qt logical coordinates."""
import math
from PySide6.QtCore import QObject, Property, Signal, Slot


class ViewerViewport(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._size = (1., 1., 1., 1., 1.)
        self._zoom = 1.
        self._pan = [0., 0.]
        self._mode = "fit"

    def _fit(self):
        w, h, rw, rh, _ = self._size
        return min(w / rw, h / rh)

    @Property(float, notify=changed)
    def scale(self):
        fit = self._fit()
        # Physical source pixels are independent of Fit and preview quality.
        return 1 / self._size[4] if self._mode == "pixels" else fit * self._zoom

    @Property(float, notify=changed)
    def panX(self):
        return self._pan[0]

    @Property(float, notify=changed)
    def panY(self):
        return self._pan[1]

    @Property(str, notify=changed)
    def zoomText(self):
        return f"{self.scale / self._fit() * 100:.1f}%"

    @Property(str,notify=changed)
    def pixelZoomText(self):
        return f"{self.scale*self._size[4]*100:.1f}%"

    @Property(str, notify=changed)
    def mode(self):
        return self._mode

    def _clamp(self):
        w, h, rw, rh, _ = self._size
        for axis, (viewport, raster) in enumerate(((w, rw), (h, rh))):
            limit = max(0., (raster * self.scale - viewport) / 2)
            self._pan[axis] = max(-limit, min(limit, self._pan[axis]))

    @Slot(float, float, float, float, float)
    def configure(self, width, height, raster_width, raster_height, dpr):
        values = (width, height, raster_width, raster_height, dpr)
        if any(not math.isfinite(v) or v <= 0 for v in values):
            return
        if values != self._size:
            self._size = values
            self._clamp()
            self.changed.emit()

    @Slot()
    def fit(self):
        self._mode, self._zoom, self._pan = "fit", 1., [0., 0.]
        self.changed.emit()

    @Slot()
    def pixels(self):
        self._mode, self._pan = "pixels", [0., 0.]
        self.changed.emit()

    @Slot(float, float, float)
    def wheel(self, x, y, steps):
        if not all(math.isfinite(v) for v in (x, y, steps)):
            return
        before = self.scale
        w, h, _, _, _ = self._size
        # Product Check: Fit is the minimum; wheel never shrinks below it.
        lower, upper = self._fit(), self._fit() * 32
        after = max(lower, min(upper, before * 1.2 ** max(-32., min(32., steps))))
        self._pan = [x - w / 2 - (x - w / 2 - self._pan[0]) * after / before,
                     y - h / 2 - (y - h / 2 - self._pan[1]) * after / before]
        self._mode, self._zoom = ("fit" if after == lower else "zoom"), after / self._fit()
        self._clamp()
        self.changed.emit()

    @Slot(float, float)
    def pan(self, dx, dy):
        if not math.isfinite(dx) or not math.isfinite(dy):
            return
        self._pan[0] += dx
        self._pan[1] += dy
        self._clamp()
        self.changed.emit()
