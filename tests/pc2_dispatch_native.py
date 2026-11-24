"""PC2-F1-D exact failed-frame input control, including editor admission."""
import argparse
from fractions import Fraction
import json
import logging
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest, QSignalSpy
import shiboken6
from native_window_policy import (load_native_shell as load_shell, resize_native_window, place_companion, close_native_windows)
from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.resources.paths import qml_root
from native_workspace_regressions import open_source, find, wait
from native_shortcut_admission import NavigationRequestProbe, admit_adjacent_shortcut
from pc2_frame_native import frame_hash


def run(fixtures, output):
    output.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication([])
    engine = QQmlApplicationEngine()
    model = build_media_view_model(logging.getLogger("pc2-dispatch"))
    engine.rootContext().setContextProperty("mediaViewModel", model)
    window = load_shell(engine, qml_root(), logging.getLogger("pc2-dispatch"))
    observer = probe = None
    other = QWindow()
    evidence = {}
    try:
        resize_native_window(window, 1440, 900)
        observer = open_source(app, window, model, fixtures / "integral.mp4", rate=Fraction(25))
        p = model.playback
        sink = find(window, "viewerVideoOutput").property("videoSink")
        p.seek(460)
        before = observer.wait_for("review failed frame 12", lambda f: f.startTime() == 440000)
        assert p.frameText == "Frame 12 / 100"
        probe = NavigationRequestProbe(p)
        other.resize(180, 100)
        place_companion(other, window)
        other.show()
        other.requestActivate()
        assert QTest.qWaitForWindowActive(other, 2000) and not window.isActive()
        count, revision, serial = len(probe.requests), observer.revision, p._step_serial
        QTest.keyClick(window, Qt.Key_Right, Qt.ShiftModifier)
        probe.require_delta(count, 0)
        assert observer.revision == revision and p._step_serial == serial and sink.videoFrame() == before
        assert p.frameText == "Frame 12 / 100"
        evidence["inactive"] = {"workspace_active": False, "requests": 0, "new_delivery": False, "label": p.frameText}

        admit_adjacent_shortcut(window, model)
        editor = find(window, "trimInInput")
        editor.forceActiveFocus(Qt.OtherFocusReason)
        assert window.activeFocusItem() == editor and editor.property("cursorPosition") is not None
        count = len(probe.requests)
        QTest.keyClick(window, Qt.Key_Right, Qt.ShiftModifier)
        probe.require_delta(count, 0)
        assert p.frameText == "Frame 12 / 100" and observer.revision == revision
        evidence["editor"] = {"requests": 0, "focus_name": editor.objectName(), "label": p.frameText}

        evidence["active_admission"] = admit_adjacent_shortcut(window, model)
        count = len(probe.requests)
        QTest.keyClick(window, Qt.Key_Right, Qt.ShiftModifier)
        evidence["active_requests"] = probe.require_delta(count)
        assert evidence["active_requests"][0]["position_ms"] == 500
        frame = observer.wait_for("one admitted key adjacent actual picture",
            lambda f: f.startTime() == 480000 and p._step_pending is None, after_revision=revision)
        assert p._presented_start == frame.startTime() and p.frameText == "Frame 13 / 100"
        assert frame_hash(frame) != frame_hash(before) and p.framesReliable
        swaps = QSignalSpy(window.frameSwapped)
        window.update()
        wait(app, lambda: swaps.count() > 0, "one admitted key native render swap")
        assert sink.videoFrame() == frame
        evidence["active_decoded"] = {"timestamp_us": frame.startTime(), "label": p.frameText,
            "new_delivery": observer.revision > revision, "hash": frame_hash(frame), "render_swap": swaps.count()}
        (output / "dispatch-controls.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    finally:
        if probe: probe.close()
        if observer: observer.close()
        other.close()
        model.shutdown()
        close_native_windows(window)
        shiboken6.delete(engine)
        shiboken6.delete(model.playback._audio)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.fixtures.resolve(), args.output.resolve())
