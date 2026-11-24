"""Atomic native viewer window-state restoration, independent of playback ownership."""
import ctypes
from ctypes import wintypes
from PySide6.QtCore import QObject, Qt, Slot, QAbstractNativeEventFilter, QCoreApplication
from PySide6.QtGui import QWindow, QGuiApplication


class _MaximizedRestoreRectangle(QAbstractNativeEventFilter):
    """Keep Windows' transient fullscreen restore inside the target rectangle.

    Windows QPA restores its normal rectangle before issuing SW_MAXIMIZE, even
    for one setWindowStates(Maximized) call. Intercept only this owned window's
    synchronous restore transaction; never defer a paint or alter playback.
    """
    class WindowPos(ctypes.Structure):
        _fields_ = [('hwnd',wintypes.HWND),('insert_after',wintypes.HWND),
                    ('x',ctypes.c_int),('y',ctypes.c_int),('cx',ctypes.c_int),
                    ('cy',ctypes.c_int),('flags',wintypes.UINT)]

    class Placement(ctypes.Structure):
        _fields_ = [('length',wintypes.UINT),('flags',wintypes.UINT),
                    ('showCmd',wintypes.UINT),('minPosition',wintypes.POINT),
                    ('maxPosition',wintypes.POINT),('normalPosition',wintypes.RECT)]

    def __init__(self):
        super().__init__()
        self.hwnd = None
        self.target = None

    def snapshot(self, window):
        rectangle = wintypes.RECT()
        hwnd = int(window.winId())
        get_rect = ctypes.windll.user32.GetWindowRect
        get_rect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        get_rect.restype = wintypes.BOOL
        if not get_rect(hwnd,ctypes.byref(rectangle)):
            raise OSError('Cannot snapshot native viewer window geometry')
        return hwnd,(rectangle.left,rectangle.top,
                     rectangle.right-rectangle.left,rectangle.bottom-rectangle.top)

    def normal_placement(self, hwnd):
        placement = self.Placement()
        placement.length = ctypes.sizeof(placement)
        get_placement = ctypes.windll.user32.GetWindowPlacement
        get_placement.argtypes = [wintypes.HWND,ctypes.POINTER(self.Placement)]
        get_placement.restype = wintypes.BOOL
        if not get_placement(hwnd,ctypes.byref(placement)):
            raise OSError('Cannot snapshot native normal placement')
        return placement

    def retain_normal_placement(self, hwnd, saved):
        current = self.normal_placement(hwnd)
        # Only the stored restore rectangle changes. The current native window
        # stays Maximized; screen/work-area coordinates remain Windows-owned.
        if current.showCmd != 3:  # SW_SHOWMAXIMIZED
            raise RuntimeError('Viewer did not restore directly to Maximized')
        current.normalPosition = saved.normalPosition
        set_placement = ctypes.windll.user32.SetWindowPlacement
        set_placement.argtypes = [wintypes.HWND,ctypes.POINTER(self.Placement)]
        set_placement.restype = wintypes.BOOL
        if not set_placement(hwnd,ctypes.byref(current)):
            raise OSError('Cannot retain native normal placement')

    def nativeEventFilter(self, event_type, message):
        if self.target is None:
            return False,0
        event = wintypes.MSG.from_address(int(message))
        if event.hWnd != self.hwnd:
            return False,0
        if event.message == 0x0046:  # WM_WINDOWPOSCHANGING, before native geometry is applied.
            position = self.WindowPos.from_address(event.lParam)
            position.x,position.y,position.cx,position.cy = self.target
            position.flags &= ~0x0003  # Apply target size/position, not stale normal placement.
        elif event.message == 0x0005 and event.wParam == 0:  # Transient SIZE_RESTORED.
            # The native rectangle is already the saved Maximized rectangle.
            # Qt must not synchronize a Normal layout in the middle of its one
            # state transaction. The real SIZE_MAXIMIZED notification is kept.
            return True,0
        return False,0


class ViewerWindowController(QObject):
    def __init__(self, playback, parent=None):
        super().__init__(parent)
        self._playback = playback
        self.image_admission = None
        self._window = None
        self._saved_geometry = None
        self._normal_geometry = None
        self._saved_normal_geometry = None
        self._maximized = False
        self._restoring = False
        self._native_rect = None
        self._native_normal_placement = None
        self._rectangle_guard = None
        self._window_handlers = []
        if isinstance(QCoreApplication.instance(), QGuiApplication) and QGuiApplication.platformName() == 'windows':
            self._rectangle_guard = _MaximizedRestoreRectangle()
            QCoreApplication.instance().installNativeEventFilter(self._rectangle_guard)

    def _observe(self, window):
        if self._window is window:
            return
        if self._window is not None:
            raise RuntimeError('Viewer window ownership cannot be replaced')
        self._window = window
        for signal in (window.visibilityChanged, window.widthChanged,
                       window.heightChanged, window.xChanged, window.yChanged):
            handler = lambda *args: self._finish_restore()
            signal.connect(handler)
            self._window_handlers.append((signal,handler))
        self._finish_restore()

    @Slot(QObject)
    def attach(self, window):
        if isinstance(window, QWindow):
            self._observe(window)

    @Slot(QObject)
    def enter(self, window):
        if self._playback.fullscreen or not isinstance(window, QWindow):
            return
        if (getattr(self._playback,'imageMode',False) and self.image_admission is not None
                and not self.image_admission(window)):
            return
        self._observe(window)
        self._saved_geometry = window.geometry()
        self._saved_normal_geometry = self._normal_geometry
        self._maximized = bool(window.windowStates() & Qt.WindowMaximized)
        if self._maximized and self._rectangle_guard is not None:
            self._native_rect = self._rectangle_guard.snapshot(window)
            self._native_normal_placement = self._rectangle_guard.normal_placement(self._native_rect[0])
        self._restoring = False
        self._playback.enterFullscreen()
        # Retain Maximized while adding FullScreen. showFullScreen() discards
        # that flag; showMaximized() then normalizes the native window on exit.
        target = Qt.WindowFullScreen | (Qt.WindowMaximized if self._maximized else Qt.WindowNoState)
        window.setWindowStates(target)

    @Slot()
    def exit(self):
        if not self._playback.fullscreen or self._window is None or self._restoring:
            return
        self._restoring = True
        target = Qt.WindowMaximized if self._maximized else Qt.WindowNoState
        # One transition, never showNormal() before Maximized. Qt retains the
        # pre-fullscreen Normal rectangle, so no intermediate rectangle is set.
        guard = self._rectangle_guard if self._maximized else None
        if guard is not None:
            guard.hwnd,guard.target = self._native_rect
        try:
            self._window.setWindowStates(target)
        finally:
            if guard is not None:
                guard.target = None
        if guard is not None:
            guard.retain_normal_placement(self._native_rect[0],self._native_normal_placement)
        self._finish_restore()

    def _finish_restore(self, *args):
        if not self._playback.fullscreen and self._window.visibility() == QWindow.Windowed:
            self._normal_geometry = self._window.geometry()
        if not self._restoring:
            return
        target = QWindow.Maximized if self._maximized else QWindow.Windowed
        if self._window.visibility() != target or self._window.geometry() != self._saved_geometry:
            return
        # Recompose only after native state AND geometry have reached the saved
        # target. No elapsed-time assumption or hidden/opacity intermediate.
        self._restoring = False
        self._playback.exitFullscreen()

    def shutdown(self):
        if self._rectangle_guard is not None:
            self._rectangle_guard.target = None
            QCoreApplication.instance().removeNativeEventFilter(self._rectangle_guard)
        for signal,handler in self._window_handlers:
            try:
                signal.disconnect(handler)
            except RuntimeError:
                pass
        self._window_handlers.clear()
