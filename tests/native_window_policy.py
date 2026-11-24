"""Secondary-screen placement for native test processes only.

No production imports this module. Qt window/available geometry uses logical
pixels; multiplying by DPR here would incorrectly move mixed-DPI windows.
"""
import atexit
import json
from pathlib import Path
import sys

import shiboken6
from PySide6.QtCore import QEvent, QLockFile, QMargins, QObject, QRect, QSize
from PySide6.QtGui import QGuiApplication, QWindow


ROOT = Path(__file__).resolve().parents[1]
SKIP_EXIT = 77


class NativeScreenUnavailable(SystemExit):
    def __init__(self, message):
        print("NATIVE SCREEN SKIP: " + message, file=sys.stderr, flush=True)
        super().__init__(SKIP_EXIT)


def screen_key(screen):
    g = screen.availableGeometry()
    return (screen.name(), g.x(), g.y(), g.width(), g.height())


def select_screen(screens, primary, size, margins, preference=None):
    """Prefer fitting non-primary screens; name/geometry breaks ties stably."""
    fits = [s for s in screens if
            s.availableGeometry().width() >= size.width() + margins.left() + margins.right()
            and s.availableGeometry().height() >= size.height() + margins.top() + margins.bottom()]
    secondary = [s for s in fits if s != primary]
    choices = secondary or fits
    if not choices:
        raise NativeScreenUnavailable(f"no screen fits {size.width()}x{size.height()} plus decorations")
    preferred = [s for s in secondary if s.name() == preference]
    return sorted(preferred or choices, key=screen_key)[0]


def client_geometry(available, size, margins):
    """Center the decorated frame in available desktop coordinates."""
    width = size.width() + margins.left() + margins.right()
    height = size.height() + margins.top() + margins.bottom()
    if width > available.width() or height > available.height():
        raise NativeScreenUnavailable("requested frame exceeds available geometry")
    return QRect(available.x() + (available.width() - width) // 2 + margins.left(),
                 available.y() + (available.height() - height) // 2 + margins.top(),
                 size.width(), size.height())


def local_preference(path=ROOT / ".local/native-tests.json"):
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get("screen_name")
        if value is not None and not isinstance(value, str):
            raise ValueError("screen_name must be a string")
        return value
    except (OSError, ValueError, AttributeError) as error:
        print(f"Native monitor preference ignored: {error}", file=sys.stderr)
        return None


class NativeWindowPolicy(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.preference = local_preference()
        self.windows = []
        self._owners = {}
        self.failure = None
        self._changing = False
        self._screens = []
        lock_path = ROOT / ".checks/native-gui.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = QLockFile(str(lock_path))
        self.lock.setStaleLockTime(0)
        if not self.lock.tryLock(0):
            raise RuntimeError("Another native GUI campaign owns .checks/native-gui.lock; run serially")
        atexit.register(self.lock.unlock)
        app.installEventFilter(self)
        app.screenRemoved.connect(self.reassess)
        app.screenAdded.connect(self.reassess)
        app.primaryScreenChanged.connect(self.reassess)
        self._watch_screens()

    def _watch_screens(self):
        for screen in self.app.screens():
            if screen not in self._screens:
                screen.availableGeometryChanged.connect(self.reassess)
                self._screens.append(screen)

    def owner(self, window):
        parent = window.transientParent()
        owner = parent if parent in self.windows else self._owners.get(window)
        if owner is not None and shiboken6.isValid(owner) and owner in self.windows:
            return owner
        return None

    def place(self, window, size=None, screen=None, owner=None):
        if self.failure is not None:
            raise self.failure
        if window not in self.windows:
            self.windows.append(window)
            window._native_window_policy = self
        if owner is not None:
            self._owners[window] = owner
        owner = self.owner(window)
        if owner is not None:
            screen = owner.screen()
        size = size or window.size()
        # Creating the native handle while hidden makes real decoration margins
        # available before exposure, including the target screen's DPI.
        margins = window.frameMargins()
        for _ in range(3):
            target = screen or select_screen(self.app.screens(), self.app.primaryScreen(),
                                             size, margins, self.preference)
            window.setScreen(target)
            window.setGeometry(client_geometry(target.availableGeometry(), size, margins))
            window.create()
            margins = window.frameMargins()
            if screen is None:
                fitting = select_screen(self.app.screens(), self.app.primaryScreen(),
                                        size, margins, self.preference)
                if fitting != target:
                    continue
            geometry = client_geometry(target.availableGeometry(), size, margins)
            window.setGeometry(geometry)
            if window.screen() == target and window.frameMargins() == margins:
                break
        else:
            raise AssertionError("Native screen/DPI geometry did not settle")
        if window.screen() != target or not target.availableGeometry().contains(window.frameGeometry()):
            raise AssertionError("Native decorated window is outside selected available geometry")
        print("NATIVE WINDOW: " + json.dumps({"screen": target.name(),
              "primary": target == self.app.primaryScreen(), "dpr": window.devicePixelRatio(),
              "available": rect_values(target.availableGeometry()),
              "frame": rect_values(window.frameGeometry())}), flush=True)
        return geometry

    def eventFilter(self, obj, event):
        # Companion QWindows inherit the test owner's screen. QML popups that
        # render inside the root already share its screen; native transients
        # are caught before their Show event is delivered.
        if event.type() == QEvent.Show and isinstance(obj, QWindow) and not self._changing:
            parent = self.owner(obj)
            if parent is not None:
                self._changing = True
                try:
                    self.place(obj, owner=parent)
                except BaseException as error:
                    self.failure = error
                    obj.close()
                finally:
                    self._changing = False
        return False

    def reassess(self, *_):
        """Re-discover on disconnect/work-area changes; never use removed screens."""
        self._watch_screens()
        if self._changing:
            return
        self._changing = True
        try:
            relocated, visiting = set(), set()
            def relocate(window):
                if window in relocated or not shiboken6.isValid(window):
                    return
                if window in visiting:
                    raise RuntimeError("Native companion ownership cycle")
                visiting.add(window)
                owner = self.owner(window)
                if owner is not None:
                    relocate(owner)
                if window.visibility() in (QWindow.Windowed, QWindow.Hidden):
                    self.place(window)
                else:
                    target = owner.screen() if owner is not None else select_screen(
                        self.app.screens(), self.app.primaryScreen(), QSize(1, 1), QMargins(), self.preference)
                    window.setScreen(target)
                    window.setPosition(target.geometry().topLeft())
                visiting.remove(window)
                relocated.add(window)
            for window in self.windows:
                if shiboken6.isValid(window) and window.isVisible():
                    relocate(window)
        except BaseException as error:
            self.failure = error
            print(f"NATIVE SCREEN REASSESSMENT FAILED: {error!r}", file=sys.stderr, flush=True)
            self.close_windows()
        finally:
            self._changing = False

    def close_windows(self):
        for window in reversed(self.windows):
            if shiboken6.isValid(window):
                window.close()
        self.windows.clear()
        self._owners.clear()


def rect_values(rect):
    return [rect.x(), rect.y(), rect.width(), rect.height()]


def policy():
    app = QGuiApplication.instance()
    if app is None:
        raise RuntimeError("Create QGuiApplication before native placement")
    if app.platformName() != "windows":
        return None  # Existing offscreen checks retain their original semantics.
    if not hasattr(app, "_native_window_policy"):
        app._native_window_policy = NativeWindowPolicy(app)
    return app._native_window_policy


def resize_native_window(window, width, height):
    manager = policy()
    if manager is None:
        window.resize(width, height)
        return window.geometry()
    return manager.place(window, QSize(width, height))


def place_companion(window, parent):
    manager = policy()
    if manager is not None:
        manager.place(window, owner=parent)


def load_native_shell(engine, source, logger, *, size=None):
    """Use the accepted loader, overriding visibility only inside this harness."""
    from mediaforge.bootstrap.application import load_shell
    app = QGuiApplication.instance()
    if app is not None and app.platformName() != "windows":
        return load_shell(engine, source, logger)
    manager = None
    try:
        manager = policy()
        engine.setInitialProperties({"visible": False,**({'width':size.width(),'height':size.height()} if size else {})})
        window = load_shell(engine, source, logger)
        assert not window.isVisible(), "Native QML root exposed before test placement"
        manager.place(window)
        window._native_placed_before_show = True
        window.show()
        return window
    except BaseException:
        if manager is not None:
            manager.close_windows()
        model = engine.rootContext().contextProperty("mediaViewModel")
        if model is not None:
            model.shutdown()
        shiboken6.delete(engine)
        raise
    finally:
        if manager is not None and shiboken6.isValid(engine):
            engine.setInitialProperties({})


def close_native_windows(window):
    manager = getattr(window, "_native_window_policy", None)
    if manager is not None:
        manager.close_windows()
        if manager.failure is not None and sys.exc_info()[0] is None:
            raise manager.failure
    else:
        window.close()
