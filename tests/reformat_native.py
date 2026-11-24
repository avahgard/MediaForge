"""Bounded TASK-009 native painted-scene and standalone processing gate.

Synthetic public fixtures, real persistent Qt renderer, actual wheel/button
events, independently sampled colors and FFprobe dimensions/audio/cadence.
"""
import argparse
from fractions import Fraction
import hashlib
from itertools import product
import json
import logging
import shutil
from pathlib import Path
import subprocess
import time

import shiboken6
from PySide6.QtCore import QPoint, QPointF, QUrl, Qt
from PySide6.QtGui import QGuiApplication, QWheelEvent
from PySide6.QtMultimedia import QAudioBufferOutput
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest

from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
from mediaforge.infrastructure.resources.paths import qml_root
from native_window_policy import load_native_shell, close_native_windows, resize_native_window
from native_workspace_regressions import find, open_source, wait, activate_native_input


def run(directory, fixtures=None):
    directory.mkdir(parents=True, exist_ok=True)
    runtime = resolve_ffmpeg_runtime()
    evidence = {"runtime": {"ffmpeg": str(runtime.ffmpeg), "ffprobe": str(runtime.ffprobe)},
                "combinations": [], "processing": [], "captures": []}

    def command(args):
        return subprocess.run(args, check=True, capture_output=True, timeout=90,
                              creationflags=subprocess.CREATE_NO_WINDOW)

    source = directory / "pillarboxes space Кириллица.mp4"
    source_command = [str(runtime.ffmpeg), "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
             "color=black:s=1920x1080:r=25:d=3,drawbox=x=240:y=0:w=1440:h=1080:color=yellow:t=fill,drawbox=x=800:y=300:w=320:h=480:color=red:t=fill",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-f", "lavfi", "-i",
             "sine=frequency=660:duration=3", "-map", "0:v", "-map", "1:a", "-map", "2:a",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)]
    portrait = directory / "portrait.mp4"
    portrait_command = [str(runtime.ffmpeg), "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
             "testsrc2=s=1080x1920:r=25:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(portrait)]
    if fixtures is None:
        command(source_command); command(portrait_command)
    else:
        evidence["fixture_reuse"] = []
        for path, args in ((source, source_command), (portrait, portrait_command)):
            original = fixtures / path.name
            digest = hashlib.sha256(original.read_bytes()).hexdigest()
            shutil.copyfile(original, path)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
            evidence["fixture_reuse"].append({"source":str(original), "sha256":digest, "recipe":args[:-1]})
    initial_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    app = QGuiApplication([])
    model = build_media_view_model(logging.getLogger("task009-native"))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("mediaViewModel", model)
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(v.toString() for v in values))
    window = load_native_shell(engine, qml_root(), logging.getLogger("task009-native"))
    settlement = None
    swaps = [0]
    window.frameSwapped.connect(lambda: swaps.__setitem__(0, swaps[0] + 1))
    p = model.playback

    def painted(name):
        inspector = find(window, "inspectorContent")
        wait(app, lambda: inspector.opacity() == 1 and inspector.x() == 0, "settled Inspector")
        before = swaps[0]
        window.update()
        wait(app, lambda: swaps[0] > before, "paint " + name)
        image = window.grabWindow()
        assert not image.isNull()
        path = directory / (name + ".png")
        assert image.save(str(path))
        evidence["captures"].append({"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                     "width": image.width(), "height": image.height()})
        return image

    def color(image, nx, ny):
        canvas = find(window, "videoViewport")
        point = canvas.mapToScene(QPointF(canvas.width() * nx, canvas.height() * ny))
        c = image.pixelColor(round(point.x() * window.devicePixelRatio()),
                             round(point.y() * window.devicePixelRatio()))
        return c.red(), c.green(), c.blue()

    def yellow(rgb):
        return rgb[0] > 150 and rgb[1] > 150 and rgb[2] < 70

    def black(rgb):
        return max(rgb) < 35

    try:
        resize_native_window(window, 1440, 900)
        activate_native_input(window)
        settlement = open_source(app, window, model, source, rate=Fraction(25))
        identities = (p._player, p._audio, find(window, "viewerVideoOutput"), p._presentation_sink)
        assert model.setCropRect(240, 0, 1440, 1080)
        model.selectOperation("resize")
        assert model.setReformat("mode", "format") and model.setReformat("preset", "FHD")
        assert model.setReformat("placement", "stretch")
        # Reject any processing child creation while exercising edits/preview/zoom.
        factories = []
        def forbidden(*args, **kwargs):
            raise AssertionError("Processing spawned by a preview/edit")
        for bridge in (model.trim, model.crop, model.resize):
            children = bridge._use_case._executor._processes
            factories.append((children, children._factory))
            children._factory = forbidden
        began = time.monotonic()
        for trim, crop, reformat in product((False, True), repeat=3):
            for op, enabled in (("trim", trim), ("crop", crop), ("resize", reformat)):
                model.setOperationEnabled(op, enabled)
            image = painted(f"combination-{int(trim)}{int(crop)}{int(reformat)}")
            sides = [color(image, x, .75) for x in (.05, .95)]
            assert all(yellow(v) if crop else black(v) for v in sides), (trim, crop, reformat, sides)
            if not crop:
                geometry = model.previewPlan.copy()
                model.selectOperation("crop")
                selected_image = painted(f"disabled-crop-selected-{int(trim)}{int(reformat)}")
                assert model.previewPlan == geometry
                assert [color(selected_image, x, .75) for x in (.05, .95)] == sides
                if reformat:
                    assert not find(window, "cropOverlay").isVisible()
                model.selectOperation("resize")
            evidence["combinations"].append({"enabled": [trim, crop, reformat], "sides": sides})
        model.setReformat("placement", "fit")
        image = painted("crop-fit")
        assert black(color(image, .05, .75)) and yellow(color(image, .2, .75))
        model.setReformat("placement", "fill")
        image = painted("crop-fill")
        assert yellow(color(image, .05, .75))
        model.setReformat("placement", "stretch")
        before_crop = tuple(model.editState[k] for k in ("cropX", "cropY", "cropWidth", "cropHeight"))
        model.selectOperation("crop")
        painted("crop-edit")
        model.setCropPreview(True)
        image = painted("crop-preview-stretch")
        assert yellow(color(image, .05, .75))
        model.selectOperation("resize")
        painted("reformat-stretch")
        model.selectOperation("crop"); model.setCropPreview(False)
        overlay = find(window, "cropOverlayRect")
        area = find(window, "viewerZoomInput")
        origin = area.mapToScene(QPointF(area.width() * .7, area.height() * .5))
        saved = model.editState.copy()
        QTest.mouseMove(window, origin.toPoint())
        event = QWheelEvent(origin, origin, QPoint(), QPoint(0, 480), Qt.NoButton,
                            Qt.NoModifier, Qt.NoScrollPhase, False)
        app.sendEvent(window, event); app.processEvents()
        assert event.isAccepted() and model.viewerViewport.mode == "zoom"
        assert model.editState == saved
        canvas = find(window, "videoViewport")
        assert abs(overlay.width() / canvas.width() - .75) < .001
        QTest.mousePress(window, Qt.MiddleButton, Qt.NoModifier, origin.toPoint())
        QTest.mouseMove(window, (origin + QPointF(-30, 15)).toPoint())
        QTest.mouseRelease(window, Qt.MiddleButton, Qt.NoModifier, (origin + QPointF(-30, 15)).toPoint())
        assert model.editState == saved
        painted("zoom-crop-edit")
        # Crop pointer motion under zoom maps back through the same canvas scale.
        start = overlay.mapToScene(QPointF(overlay.width() / 2, overlay.height() / 2))
        if area.contains(area.mapFromScene(start)):
            QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start.toPoint())
            QTest.mouseMove(window, (start + QPointF(12, 0)).toPoint())
            QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, (start + QPointF(12, 0)).toPoint())
            assert model.editState["cropWidth"] == 1440
            assert model.editState["cropX"] != 240
            assert model.setCropRect(*before_crop)
        else:
            raise AssertionError("Zoomed Crop center inaccessible")
        model.viewerViewport.pixels()
        assert abs(model.viewerViewport.scale * window.devicePixelRatio() - 1) < .0001
        model.viewerViewport.fit()
        p.enterFullscreen(); painted("fullscreen")
        assert model.previewPlan["editing"]
        p.exitFullscreen(); painted("windowed-restored")
        assert before_crop == tuple(model.editState[k] for k in ("cropX", "cropY", "cropWidth", "cropHeight"))
        assert identities == (p._player, p._audio, find(window, "viewerVideoOutput"), p._presentation_sink)
        model.selectOperation("resize")
        model.setOperationEnabled("trim", False)
        model.setOperationEnabled("crop", False)
        model.setOperationEnabled("resize", False)
        revision = settlement.revision
        p.stepFrame(1)
        settlement.wait_for("adjacent step", lambda f: f.startTime() == 40000, after_revision=revision)
        assert p.frameText == "Frame 2 / 75"
        monitor = QAudioBufferOutput(p._player); buffers = []
        monitor.audioBufferReceived.connect(lambda b: buffers.append(b.frameCount()) if b.isValid() else None)
        p._player.setAudioBufferOutput(monitor)
        p.togglePlayback()
        wait(app, lambda: p.position >= 240 and len(buffers) > 2, "live motion and decoded audio")
        model.setOperationEnabled("crop", True)
        model.setOperationEnabled("resize", True)
        at_edit = p.position
        image = painted("playing-crop-stretch")
        assert yellow(color(image, .05, .75))
        wait(app, lambda: p.position > at_edit, "transport advances through preview edit")
        p.togglePlayback(); p._player.setAudioBufferOutput(None)
        evidence["audio_buffers"] = len(buffers)
        evidence["interactive_seconds"] = time.monotonic() - began
        for children, factory in factories:
            children._factory = factory
        model.setOperationEnabled("crop", False)
        # Extend the same accepted executor/service/gate, never a composite chain.
        service = model.resize._use_case
        def probe(path):
            return json.loads(command([str(runtime.ffprobe), "-v", "error", "-show_streams", "-show_frames",
                                       "-of", "json", str(path)]).stdout)
        source_probe = probe(source)
        source_times = [f["best_effort_timestamp_time"] for f in source_probe["frames"] if f["media_type"] == "video"]
        for width, height, placement in ((960, 540, "stretch"), (960, 720, "fit"),
                                         (1440, 1080, "fill"), (640, 480, "stretch")):
            assert service.start_reformat(width, height, placement)
            wait(app, lambda: not service.busy, "standalone " + placement, timeout=90)
            job = service.snapshot()
            assert job.state == "Completed", job
            facts = probe(Path(job.result))
            video = next(s for s in facts["streams"] if s["codec_type"] == "video")
            assert (video["width"], video["height"], video["sample_aspect_ratio"]) == (width, height, "1:1")
            assert sum(s["codec_type"] == "audio" for s in facts["streams"]) == 2
            timestamps = [f["best_effort_timestamp_time"] for f in facts["frames"] if f["media_type"] == "video"]
            assert timestamps == source_times
            evidence["processing"].append({"size": [width, height], "placement": placement,
                                           "output": job.result, "frames": len(timestamps), "audio_streams": 2})
        # Portrait switches reset viewer-only state; the VideoOutput/audio persist.
        old_audio, old_video = p._audio, find(window, "viewerVideoOutput")
        model.viewerViewport.wheel(400, 200, 4)
        settlement.close(); settlement = None
        settlement = open_source(app, window, model, portrait, rate=Fraction(25))
        assert model.viewerViewport.mode == "fit" and p._audio is old_audio
        assert find(window, "viewerVideoOutput") is old_video
        painted("portrait-letterbox")
        assert model.setReformat("mode", "format") and model.setReformat("preset", "FHD")
        model.setOperationEnabled("resize", True)
        image = painted("portrait-fit")
        assert black(color(image, .05, .5))
        assert model.startStandaloneReformat()
        wait(app, lambda: not service.busy, "portrait standalone Fit", timeout=90)
        assert service.snapshot().state == "Completed", service.snapshot()
        portrait_facts = probe(Path(service.snapshot().result))
        portrait_video = next(s for s in portrait_facts["streams"] if s["codec_type"] == "video")
        assert (portrait_video["width"], portrait_video["height"], portrait_video["sample_aspect_ratio"]) == (1920,1080,"1:1")
        evidence["portrait_processing"] = service.snapshot().result
        assert hashlib.sha256(source.read_bytes()).hexdigest() == initial_hash
        assert not warnings, warnings
        evidence.update(dpr=window.devicePixelRatio(), screen=window.screen().name(),
                        source_sha256=initial_hash, verdict="PASS")
    finally:
        if settlement: settlement.close()
        model.shutdown(); close_native_windows(window); shiboken6.delete(engine)
        (directory / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path)
    args = parser.parse_args()
    run(args.output.resolve(), args.fixtures.resolve() if args.fixtures else None)
