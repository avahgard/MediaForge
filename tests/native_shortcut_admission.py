"""Native test input admission; never repair or retry a dispatched key."""
import time
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
import shiboken6
from native_workspace_regressions import find


class NavigationRequestProbe:
    """Count actual Presentation calls into the current native backend."""
    def __init__(self, playback):
        self.playback, self.player = playback, playback._player
        self.requests = []
        self.original = self.player.setPosition

        def requested(position):
            self.requests.append({"position_ms": position, "monotonic": time.perf_counter(),
                                  "serial": playback._step_serial})
            return self.original(position)

        self.player.setPosition = requested

    def close(self):
        if shiboken6.isValid(self.player):
            self.player.setPosition = self.original

    def require_delta(self, before, expected=1):
        actual = len(self.requests) - before
        assert actual == expected, ("INPUT/ADMISSION FAILURE: dispatched key request count", actual, expected,
                                    self.requests[before:])
        return self.requests[before:]


def admit_adjacent_shortcut(window, model):
    """All activation/focus work precedes the benchmark measurement endpoint."""
    window.requestActivate()
    assert QTest.qWaitForWindowActive(window, 2000), "INPUT/ADMISSION: workspace activation failed"
    target = find(window, "workspaceKeyboardFocus")
    target.forceActiveFocus(Qt.OtherFocusReason)
    focus = window.activeFocusItem()
    p = model.playback
    assert window.isActive() and QGuiApplication.focusWindow() == window
    assert focus == target and target.property("activeFocus")
    assert focus.property("cursorPosition") is None, "INPUT/ADMISSION: editor focus"
    assert model.state == "loaded" and p.canPlay and p.exactStepAvailable
    assert p.state == "Paused" and p._step_pending is None and p._queued_step is None
    # These are the actual QML Shortcut guards: loaded source + non-editor
    # active focus. Native window ownership is separately proven above.
    return {"active": window.isActive(), "focus_window_is_workspace": QGuiApplication.focusWindow() == window,
        "focus_name": focus.objectName(), "focus_is_editor": False, "source_loaded": model.state == "loaded",
        "exact_step_available": p.exactStepAvailable, "pending": p._step_pending,
        "source_generation": p._source_generation, "request_serial": p._step_serial,
        "backend_cpp_id": shiboken6.getCppPointer(p._player)[0]}
