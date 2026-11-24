"""Small serial Windows C1 probe; no media generation or decoder campaign."""
import argparse
import json
import logging
from pathlib import Path
import subprocess
import sys

import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.resources.paths import qml_root
from native_window_policy import (NativeScreenUnavailable, close_native_windows, load_native_shell, place_companion,
                                  policy, rect_values, resize_native_window)


def run(output, lock_only=False, no_fit=False):
    app = QGuiApplication([])
    assert app.platformName() == "windows"
    if lock_only:
        engine = QQmlApplicationEngine()
        model = build_media_view_model(logging.getLogger("c1-lock-rejection"))
        engine.rootContext().setContextProperty("mediaViewModel", model)
        try:
            load_native_shell(engine, qml_root(), logging.getLogger("c1-lock-rejection"))
        except RuntimeError as error:
            assert "run serially" in str(error)
            assert not shiboken6.isValid(engine)
            assert not shiboken6.isValid(model.playback._player)
            return
        raise AssertionError("Parallel native campaign was admitted")
    output.mkdir(parents=True, exist_ok=True)
    engine = QQmlApplicationEngine()
    model = build_media_view_model(logging.getLogger("c1-probe"))
    engine.rootContext().setContextProperty("mediaViewModel", model)
    window = other = transient = None
    try:
        if no_fit:
            import native_window_policy
            def reject(*_, **__):
                raise NativeScreenUnavailable("injected no-fitting-screen discovery")
            native_window_policy.select_screen = reject
            try:
                load_native_shell(engine, qml_root(), logging.getLogger("c1-no-fit"))
            except NativeScreenUnavailable:
                assert not shiboken6.isValid(engine)
                assert not shiboken6.isValid(model.playback._player)
                assert not any(w.isVisible() for w in app.topLevelWindows())
                print("NATIVE NO-FIT CLEANUP VERIFIED", flush=True)
                raise
            raise AssertionError("No-fit discovery was admitted")
        window = load_native_shell(engine, qml_root(), logging.getLogger("c1-probe"))
        assert QTest.qWaitForWindowExposed(window, 3000)
        assert window._native_placed_before_show
        manager = window._native_window_policy
        screens = app.screens()
        rows = [{"name": s.name(), "primary": s == app.primaryScreen(),
                 "available": rect_values(s.availableGeometry()), "dpr": s.devicePixelRatio()}
                for s in screens]
        evidence = {"screens": rows, "placements": []}
        for width, height in ((800, 560), (1440, 900)):
            placement = resize_native_window(window, width, height)
            QTest.qWait(80)
            assert window.screen().availableGeometry().contains(window.frameGeometry())
            assert window.width() == width and window.height() == height
            evidence["placements"].append({"screen": window.screen().name(),
                                            "client": rect_values(placement),
                                            "frame": rect_values(window.frameGeometry())})
        other = QWindow()
        other.resize(180, 100)
        place_companion(other, window)
        other.show()
        assert QTest.qWaitForWindowExposed(other, 3000)
        assert other.screen() == window.screen()
        assert other.screen().availableGeometry().contains(other.frameGeometry())
        # A transient not explicitly registered is caught by the Show filter.
        transient = QWindow()
        transient.resize(160, 90)
        transient.setTransientParent(window)
        transient.show()
        assert QTest.qWaitForWindowExposed(transient, 3000)
        assert transient in manager.windows and transient.screen() == window.screen()
        assert transient.screen().availableGeometry().contains(transient.frameGeometry())
        transient.close()
        # Preserve genuine activation and inactive-window controls.
        other.requestActivate()
        assert QTest.qWaitForWindowActive(other, 2000) and not window.isActive()
        other.close()
        window.requestActivate()
        assert QTest.qWaitForWindowActive(window, 2000)
        before = window.geometry()
        window.showFullScreen()
        QTest.qWait(150)
        assert window.visibility() == QWindow.FullScreen
        window.showNormal()
        QTest.qWait(150)
        assert window.geometry() == before
        window.showMaximized()
        QTest.qWait(150)
        assert window.visibility() == QWindow.Maximized
        window.showNormal()
        QTest.qWait(150)
        assert window.geometry() == before
        # Exercise the reconnect/work-area handler with real current screens.
        manager.reassess()
        assert manager.failure is None
        assert window.screen().availableGeometry().contains(window.frameGeometry())
        # Simulate discovery losing the secondary while retaining real QScreen
        # objects. This tests relocation, without unplugging the user's display.
        discover = app.screens
        try:
            app.screens = lambda: [app.primaryScreen()]
            manager.reassess()
            assert manager.failure is None and window.screen() == app.primaryScreen()
            assert window.screen().availableGeometry().contains(window.frameGeometry())
        finally:
            app.screens = discover
            manager.reassess()
        assert manager.failure is None
        result = subprocess.run([sys.executable, __file__, "--lock-only", "--output", str(output)],
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr
        evidence.update(companion=True, transient=True, activation_controls=True,
                        fullscreen_restore=True, maximized_restore=True, serial_guard=True,
                        placed_before_show=True,
                        simulated_disconnect_relocation=True,
                        real_disconnect="not physically exercised")
        (output / "results.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps(evidence), flush=True)
    finally:
        model.shutdown()
        if window is not None:
            close_native_windows(window)
        if shiboken6.isValid(engine):
            shiboken6.delete(engine)
        if other is not None and shiboken6.isValid(other):
            shiboken6.delete(other)
        if transient is not None and shiboken6.isValid(transient):
            shiboken6.delete(transient)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lock-only", action="store_true")
    parser.add_argument("--no-fit", action="store_true")
    args = parser.parse_args()
    run(args.output.resolve(), args.lock_only, args.no_fit)
