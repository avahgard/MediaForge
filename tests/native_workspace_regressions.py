"""Native TASK-008 assertions with bounded decoded/presentation settlement."""

import argparse
from fractions import Fraction
import hashlib
import json
import logging
from pathlib import Path
import time

from PySide6.QtCore import QObject, QPoint, QPointF, QUrl, Qt, QEventLoop, QTimer, Slot
from PySide6.QtGui import QGuiApplication, QImage, QWindow
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoFrame
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow  # Register the native item wrappers.
from PySide6.QtTest import QTest
import shiboken6
from native_capture_geometry import bright_pixels, assert_capture_size
from native_input_clock import NativeInputClock

from native_window_policy import (load_native_shell as load_shell, resize_native_window, place_companion, close_native_windows)
from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.resources.paths import qml_root


def wait(app, predicate, label, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(5)
    raise AssertionError("Timeout: " + label)


class DecodedFrameSettlement(QObject):
    """Test-only readiness barrier for decoded delivery, observation and label.

    Queued slots and a queued check let the sink setter and Presentation adapter
    finish before reading them together. A caller supplies an independent label
    oracle; a coherent frame with a wrong public label fails immediately instead
    of waiting for it to heal. No quiet period, player clock or production seek
    formula establishes readiness. Strict caller accuracy assertions remain.
    """

    def __init__(self, sink, playback, expected_label):
        super().__init__()
        assert callable(expected_label), "Independent frame-label oracle required"
        self.sink, self.playback = sink, playback
        self._label_for_frame = expected_label
        self.revision = 0
        # A frame already delivered before attaching this observer is still
        # concrete sink evidence. Position is never a readiness input.
        self._delivered = QVideoFrame(sink.videoFrame())
        self._observed_start = playback._presented_start
        self._loop = None
        self._result = None
        self._failure = None
        self._closed = False
        sink.videoFrameChanged.connect(self._on_frame, Qt.QueuedConnection)
        playback.changed.connect(self._on_presentation, Qt.QueuedConnection)

    @Slot(QVideoFrame)
    def _on_frame(self, frame):
        if not self._closed:
            self.revision += 1
            self._delivered = QVideoFrame(frame)
            QTimer.singleShot(0, self._check)

    @Slot()
    def _on_presentation(self):
        if not self._closed:
            self._observed_start = self.playback._presented_start
            QTimer.singleShot(0, self._check)

    def _check(self):
        if self._loop is None or self._closed or self._result is not None or self._failure is not None:
            return
        frame = self.sink.videoFrame()
        if (frame.isValid() and frame == self._delivered
                and (self._after_revision is None or self.revision > self._after_revision)
                and self._observed_start == frame.startTime()
                and self.playback._presented_start == frame.startTime()):
            # A Qt callback exception alone does not propagate to wait_for().
            # Preserve a terminal failure and raise it from the waiting caller.
            try:
                expected = self._expected_label(frame)
                actual = self.playback.frameText
                assert isinstance(expected, str), "Expected frame label must be explicit"
                assert actual == expected, (
                    f"Incorrect public frame label ({self._wait_label}); "
                    f"sink_start={frame.startTime()}, observed_start={self._observed_start}, "
                    f"expected={expected!r}, actual={actual!r}"
                )
            except Exception as error:
                self._failure = error
                self._loop.quit()
                return
            if self._predicate(frame):
                self._result = frame
                self._loop.quit()

    def wait_for(self, label, predicate=lambda frame: True, *, after_revision=None,
                 expected_label=None, timeout_ms=10000):
        assert self._loop is None and not self._closed, "Non-reentrant native observation wait"
        self._loop = QEventLoop()
        self._result = None
        self._failure = None
        self._wait_label = label
        self._expected_label = (self._label_for_frame if expected_label is None
                                else lambda frame: expected_label)
        self._predicate, self._after_revision = predicate, after_revision
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(self._loop.quit)
        timer.start(timeout_ms)
        QTimer.singleShot(0, self._check)
        try:
            self._loop.exec()
            if self._failure is not None:
                raise self._failure
            if self._result is None:
                frame = self.sink.videoFrame()
                raise AssertionError(
                    f"Timeout: decoded/presentation settlement ({label}, {timeout_ms} ms); "
                    f"delivery={self.revision}, required_after={after_revision}, "
                    f"sink_valid={frame.isValid()}, sink_start={frame.startTime()}, "
                    f"sink_end={frame.endTime()}, delivered_start={self._delivered.startTime()}, "
                    f"observed_start={self._observed_start}, "
                    f"presentation_start={self.playback._presented_start}, "
                    f"label={self.playback.frameText!r}"
                )
            return self._result
        finally:
            timer.stop()
            self._loop = None

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.sink.videoFrameChanged.disconnect(self._on_frame)
        self.playback.changed.disconnect(self._on_presentation)
        # The native backend owns the frame buffers. Release the observer's
        # copies before the decoder and sinks are torn down.
        self._delivered = QVideoFrame()
        self._result = None


def visual_items(item):
    yield item
    for child in item.childItems():
        yield from visual_items(child)


def find(window, name):
    candidates = list(window.findChildren(QObject)) + list(visual_items(window.contentItem()))
    matches = [obj for obj in candidates if obj.objectName() == name]
    assert matches, name
    return next((obj for obj in matches if hasattr(obj, "isVisible") and obj.isVisible()), matches[0])


def rect(item):
    origin = item.mapToScene(QPointF())
    return [origin.x(), origin.y(), item.width(), item.height()]


def inside(inner, outer):
    x, y, w, h = inner
    ox, oy, ow, oh = outer
    assert w > 0 and h > 0 and x >= ox - .1 and y >= oy - .1, (inner, outer)
    assert x + w <= ox + ow + .1 and y + h <= oy + oh + .1, (inner, outer)


def separate(a, b):
    x, y, w, h = a
    bx, by, bw, bh = b
    assert x + w <= bx + .1 or bx + bw <= x + .1 or y + h <= by + .1 or by + bh <= y + .1, (a, b)


def click(window, item):
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(round(p.x()), round(p.y())))
    QTest.qWait(25)


_fixture_times_cache = {}


def open_source(app, window, model, path, *, rate):
    assert model.openMedia(QUrl.fromLocalFile(str(path)))
    wait(app, lambda: model.state == "loaded" and model.canOpen and model.playback.duration > 0, "source")
    # Independent test-only whole decoded presentation oracle. Nominal rate and
    # requested seek position never supply the product label expectation.
    import subprocess
    from bisect import bisect_left
    from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
    stat = path.stat()
    signature = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    if signature not in _fixture_times_cache:
        result = subprocess.run([str(resolve_ffmpeg_runtime().ffprobe), '-v', 'error',
            '-select_streams', 'v:0', '-show_frames', '-show_entries',
            'frame=best_effort_timestamp_time:frame_side_data=', '-of', 'json', str(path)],
            capture_output=True,text=True,encoding='utf-8',timeout=300)
        assert result.returncode == 0, result.stderr
        _fixture_times_cache[signature] = tuple(round(Fraction(f['best_effort_timestamp_time'])*1000000)
                      for f in json.loads(result.stdout)['frames'])
    times = _fixture_times_cache[signature]
    wait(app,lambda:model.playback.exactStepAvailable,'completed source presentation timeline',timeout=310)
    def label_for_frame(frame):
        position=bisect_left(times,frame.startTime())
        matches=[i for i in (position-1,position) if 0<=i<len(times) and abs(times[i]-frame.startTime())<=2]
        assert len(matches)==1,(frame.startTime(),matches)
        return f'Frame {matches[0]+1} / {len(times)}'
    settlement = DecodedFrameSettlement(
        find(window, "viewerVideoOutput").property("videoSink"), model.playback, label_for_frame)
    try:
        settlement.wait_for("paused first decoded frame", lambda frame: frame.startTime() == 0)
        assert model.playback.state == "Paused" and model.playback.position == 0
        return settlement
    except BaseException:
        settlement.close()
        raise


def activate_native_input(window):
    """A WindowShortcut requires an active native window, separate from decoding.

    Request activation once and wait for Qt's actual active-window condition.
    Failure is explicit; no key retry or decoded readiness delay is introduced.
    """
    window.requestActivate()
    assert QTest.qWaitForWindowActive(window, 2000), 'Native input window never became active'
    find(window, 'workspaceKeyboardFocus').forceActiveFocus()


def frame_checks(app, window, model, rate, output, settlement):
    playback = model.playback
    video = find(window, "viewerVideoOutput")
    sink = video.property("videoSink")
    assert playback._player.videoSink() is playback._staging_sink
    assert playback._staging_sink is not sink and playback._presentation_sink is sink
    # Complete independent decoded-frame oracle from open_source(), not the
    # superseded bounded metadata observation (which may reject B-frame timing).
    source = Path(model._workspace.active.metadata.source_path)
    stat = source.stat()
    count = len(_fixture_times_cache[(str(source.resolve()), stat.st_size, stat.st_mtime_ns)])
    assert count >= 90 and playback.framesReliable
    wait(app, lambda: playback.exactStepAvailable, "initial bounded local proof")
    player, audio = playback._player, playback._audio
    observations = []

    def snapshot(frame=None):
        if frame is None:
            frame = settlement.wait_for("current paused frame")
        assert frame.isValid()
        start = frame.startTime()
        index = round(Fraction(start, 1_000_000) * rate)
        assert abs(Fraction(start) - index * 1_000_000 / rate) <= 2, start
        assert 0 <= index < count
        assert playback.frameText == f"Frame {index+1} / {count}", (start, playback.frameText)
        image = frame.toImage().convertToFormat(QImage.Format_RGBA8888)
        assert not image.isNull()
        digest = hashlib.sha256(bytes(image.constBits())).hexdigest()
        return index, start, digest

    def seek_settled(target):
        def covers_target(frame):
            return frame.startTime() <= target * 1000 + 2 and frame.endTime() >= target * 1000 - 2

        revision = settlement.revision
        current = sink.videoFrame()
        already_settled = (target == playback.position and playback.exactStepAvailable
                           and current.isValid() and covers_target(current)
                           and round(Fraction(current.startTime(), 1_000_000) * rate)
                           == playback.requestedFrameIndex)
        del current
        playback.seek(target)
        # Use the decoded frame's own interval, not the production seek formula.
        # A repeated seek of the admitted picture is a no-op. All other seeks
        # must still deliver a newer frame, including exact boundary seeks.
        return settlement.wait_for(
            f"seek {target} ms", covers_target,
            after_revision=None if already_settled else revision,
        )

    def step(direction):
        wait(app, lambda: playback.exactStepAvailable, "local step capability")
        before, start, digest = snapshot()
        expected = min(count - 1, max(0, before + direction))
        revision = settlement.revision
        activate_native_input(window)
        serial = playback._step_serial
        QTest.keyClick(window, Qt.Key_Right if direction > 0 else Qt.Key_Left, Qt.ShiftModifier)
        if expected != before:
            assert playback._step_pending == direction or playback._step_serial > serial, (
                'Native shortcut did not dispatch an adjacent request', window.isActive(),
                before, direction, playback._step_pending, playback._step_serial, serial)
        frame = settlement.wait_for(
            "adjacent decoded frame", lambda frame: playback._step_pending is None,
            # First/last no-op clamps legitimately deliver no new frame.
            after_revision=revision if expected != before else None,
        )
        after, next_start, next_digest = snapshot(frame)
        assert after == expected, (before, direction, after)
        if expected != before:
            assert next_start != start and next_digest != digest
        else:
            assert next_start == start and next_digest == digest
        assert playback.framesReliable
        assert playback._player is player and playback._audio is audio
        assert find(window, "viewerVideoOutput") is video
        observations.append({"before": before, "direction": direction, "after": after,
                             "start_us": next_start, "image_sha256": next_digest,
                             "label": playback.frameText, "position_ms": playback.position})

    # Reviewer boundary seeks: position may deliberately refer to the preceding
    # decoded picture. Its label must describe that picture and stepping starts there.
    for target in (0, 40, 42, 80, 84, 1000, 1001, 1040, 1043, 2002):
        snapshot(seek_settled(target))
        for direction in (1, 1, -1, -1):
            step(direction)
    # First clamp and a long repeated walk through fractional integral-ms
    # boundaries at indexes 24/48 (1001/2002 ms), in both directions.
    assert snapshot(seek_settled(0))[0] == 0
    step(-1)
    for _ in range(52):
        step(1)
    for _ in range(52):
        step(-1)
    step(-1)
    # Last frame and end clamps, from an interior seek independent of product helper.
    assert snapshot(seek_settled(int((count - Fraction(1, 2)) * 1000 / rate)))[0] == count - 1
    step(1)
    step(-1)
    step(1)
    step(1)
    assert len(playback.findChildren(QMediaPlayer)) == 1
    assert len(playback.findChildren(QAudioOutput)) == 1
    output["decoded_steps"] = observations


def native_input_checks(app, window, model, output, settlement):
    """Adversarial inactive-window input at the exact failed 30-fps frame."""
    playback = model.playback
    from native_shortcut_admission import NavigationRequestProbe, admit_adjacent_shortcut
    requests = NavigationRequestProbe(playback)
    playback.seek(783)
    frame = settlement.wait_for('input ownership setup',
                                lambda frame: frame.startTime() == 766667)
    assert playback.frameText == 'Frame 24 / 120'
    image = frame.toImage().convertToFormat(QImage.Format_RGBA8888)
    assert not image.isNull()
    digest = hashlib.sha256(bytes(image.constBits())).hexdigest()
    other = QWindow()
    other.resize(180, 100)
    place_companion(other, window)
    other.show()
    other.requestActivate()
    try:
        assert QTest.qWaitForWindowActive(other, 2000) and not window.isActive()
        before = playback._step_serial, playback.position, playback._presented_start, playback.frameText
        count = len(requests.requests)
        # One deliberately undeliverable key, as a negative control. No decoded
        # readiness claim is made for an action Qt did not dispatch.
        QTest.keyClick(window, Qt.Key_Right, Qt.ShiftModifier)
        requests.require_delta(count, 0)
        assert playback._step_pending is None
        assert before == (playback._step_serial, playback.position, playback._presented_start, playback.frameText)
        output['active_dispatch_admission'] = admit_adjacent_shortcut(window, model)
        revision, serial = settlement.revision, playback._step_serial
        count = len(requests.requests)
        QTest.keyClick(window, Qt.Key_Right, Qt.ShiftModifier)
        output['active_key_requests'] = requests.require_delta(count)
        assert playback._step_pending == 1 or playback._step_serial > serial
        frame = settlement.wait_for('active input adjacent decoded frame',
                                    lambda frame: playback._step_pending is None,
                                    after_revision=revision)
        assert frame.startTime() == 800000 and playback.frameText == 'Frame 25 / 120'
        image = frame.toImage().convertToFormat(QImage.Format_RGBA8888)
        assert not image.isNull() and hashlib.sha256(bytes(image.constBits())).hexdigest() != digest
        assert playback.framesReliable
        output['inactive_window_no_dispatch'] = True
        output['bounded_activation_adjacent_delivery'] = True
    finally:
        requests.close()
        other.close()


def unreliable_checks(app, window, model, output, settlement):
    playback = model.playback
    sink = find(window, "viewerVideoOutput").property("videoSink")
    frame = settlement.wait_for("VFR indexed decoded observation")
    assert sink.videoFrame().isValid() and playback.framesReliable
    revision = settlement.revision
    playback.stepFrame(1)
    adjacent = settlement.wait_for('VFR actual indexed adjacency', lambda f: playback._step_pending is None, after_revision=revision)
    assert adjacent.startTime() > frame.startTime() and playback.framesReliable
    # Missing actual presentation timing stays unavailable despite a complete
    # timeline, metadata rates and an independently known total.
    unknown = QVideoFrame(QImage(16,16,QImage.Format_RGB32))
    # Raw decoder observations now enter staging, never the admitted Viewer.
    playback._staging_sink.setVideoFrame(unknown)
    wait(app,lambda:playback._presented_start is None,'unknown QVideoFrame timestamp')
    expected=f'Frame — / {playback._timeline.count}'
    assert playback.frameText == expected and not playback.framesReliable
    before=playback.position
    for direction in (-1,1):
        playback.stepFrame(direction)
        assert playback.position==before and not playback.exactStepAvailable and playback.frameText==expected
    playback._timeline=None;playback.changed.emit()
    assert playback.frameText=='Frame timeline unavailable' and not playback.exactStepAvailable
    output['vfr_supported_unknown_timestamp_fail_closed']=True


def layout_checks(app, window, model, output):
    window.showNormal()
    resize_native_window(window, 800, 560)
    QTest.qWait(250)
    assert window.minimumWidth() == 800 and window.minimumHeight() == 560
    bounds = [0, 0, 800, 560]
    panels = ("mediaSidebar", "compactMediaHeader", "windowedViewerHost", "windowedTransport",
              "operationSurface", "mediaTraySurface", "mediaInspector")
    output["minimum_panels"] = {name: rect(find(window, name)) for name in panels}
    for area in output["minimum_panels"].values():
        inside(area, bounds)
    viewer = find(window, "windowedViewerHost")
    assert viewer.width() >= 380 and viewer.height() >= 180
    labels = [find(window, name) for name in ("playbackTimeText", "frameReliabilityText", "playbackDurationText")]
    for i, label in enumerate(labels):
        inside(rect(label), rect(find(window, "mediaTimeline")))
        for other in labels[i + 1:]:
            separate(rect(label), rect(other))
    controls = [find(window, name) for name in ("playPauseButton", "mediaTimeline", "muteButton", "volumeSlider")]
    for i, control in enumerate(controls):
        inside(rect(control), rect(find(window, "windowedTransport")))
        for other in controls[i + 1:]:
            separate(rect(control), rect(other))
    viewport = find(window, "operationsViewport")
    for operation in ("Trim", "Crop", "Reformat", "Noise", "Output"):
        item = find(window, "operation" + operation)
        viewport.setProperty("contentX", min(max(0, item.x()), viewport.property("contentWidth") - viewport.width()))
        QTest.qWait(30)
        inside(rect(item), rect(viewport))
        toggle, body = find(window, "enable" + operation), find(window, "select" + operation)
        separate(rect(toggle), rect(body))
        label = body.property("contentItem")
        inside(rect(label), rect(body))
        assert not label.property("truncated"), operation
        if operation in ("Trim", "Crop", "Reformat"):
            click(window, body)
            assert model.selectedOperation == ("resize" if operation == "Reformat" else operation.lower())
            if operation == "Reformat": model.setReformat("mode", "format")
            QTest.qWait(220)
            scroll = find(window, "inspectorScroll")
            flick = scroll.property("contentItem")
            flick.setProperty("contentY", 0)
            QTest.qWait(25)
            for name in ({"Trim": ("trimInInput", "trimOutInput", "setTrimInButton", "setTrimOutButton"),
                          "Crop": ("cropXInput", "cropYInput", "cropWInput", "cropHInput", "cropPreviewToggle"),
                          "Reformat": ("resizeWidthInput", "resizeHeightInput", "keepAspectRatioControl")}[operation]):
                editor = find(window, name)
                inside(rect(editor), rect(scroll))
                if name in ("cropPreviewToggle", "keepAspectRatioControl"):
                    separate(rect(editor.property("contentItem")), rect(editor.property("indicator")))
            flick.setProperty("contentY", max(0, flick.property("contentHeight") - flick.height()))
            QTest.qWait(25)
            assert flick.property("contentY") > 0, "Inspector result must be reachable by scrolling"
    viewport.setProperty("contentX", 0)
    model.selectOperation("resize")
    QTest.qWait(220)
    window.grabWindow().save(str(output["directory"] / "minimum-800x560.png"))
    # All tray items can be brought into the clipped viewport, with Add retained.
    tray = find(window, "mediaTrayList")
    for index in range(len(model.mediaItems)):
        item = find(window, "trayItem" + str(index))
        tray.setProperty("contentX", min(item.x(), max(0, tray.property("contentWidth") - tray.width())))
        QTest.qWait(30)
        inside(rect(item), rect(tray))
    inside(rect(find(window, "mediaTrayAdd")), bounds)
    resize_native_window(window, 1440, 900)
    QTest.qWait(250)
    reference = {"windowedViewerHost": [242, 92, 804, 452], "windowedTransport": [242, 556, 804, 70],
                 "operationSurface": [242, 676, 804, 62], "mediaTraySurface": [242, 782, 804, 100],
                 "mediaInspector": [1070, 92, 342, 790]}
    for name, expected in reference.items():
        actual = rect(find(window, name))
        assert max(abs(a - b) for a, b in zip(actual, expected)) <= 6, (name, actual)
    output["reference_geometry"] = reference


def icon_checks(app, engine, window, output):
    owner = window
    engine.setInitialProperties({"visible": False})
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / "fixtures/NativeIconGallery.qml")))
    engine.setInitialProperties({})
    assert len(engine.rootObjects()) == 2
    window = engine.rootObjects()[-1]
    place_companion(window, owner)
    window.show()
    def painted():
        image = window.grabWindow()
        dpr = assert_capture_size(window, image)
        return all(bright_pixels(image, rect(find(window, 'testIcon' + name)), dpr)
                   for name in ('play', 'pause', 'mute', 'volume', 'fullscreen', 'info', 'add', 'exit'))
    wait(app, painted, 'actual painted icon glyphs')
    image = window.grabWindow()
    dpr = assert_capture_size(window, image)
    output['capture_geometry'] = {'dpr': dpr, 'logical': [window.width(), window.height()],
                                  'physical': [image.width(), image.height()]}
    checks = {}
    for name in ("play", "pause", "mute", "volume", "fullscreen", "info", "add", "exit"):
        button = find(window, "testIcon" + name)
        canvas = button.findChild(QObject, "iconGlyph")
        bx, by, bw, bh = rect(button)
        gx, gy, gw, gh = rect(canvas)
        assert gw == gh == 22 and bw == bh == 48
        assert abs(gx + gw / 2 - bx - bw / 2) < .01
        assert abs(gy + gh / 2 - by - bh / 2) < .01
        pixels = bright_pixels(image, [bx, by, bw, bh], dpr)
        assert pixels, name
        cx = sum(x for x, _ in pixels) / len(pixels)
        cy = sum(y for _, y in pixels) / len(pixels)
        assert abs(cx - bx - bw / 2) <= 2 and abs(cy - by - bh / 2) <= 2, (name, cx, cy)
        if name == "pause":
            assert abs(cx - bx - bw / 2) <= .5 and abs(cy - by - bh / 2) <= .5
        checks[name] = {"canvas": [gw, gh], "hit_area": [bw, bh],
                        "optical_offset": [cx - bx - bw / 2, cy - by - bh / 2]}
    assert image.save(str(output["directory"] / "icon-gallery.png"))
    output["icons"] = checks
    window.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.fixtures = args.fixtures.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication([])
    assert app.platformName() == "windows"
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(v.toString() for v in values))
    model = build_media_view_model(logging.getLogger("native-workspace"))
    engine.rootContext().setContextProperty("mediaViewModel", model)
    window = load_shell(engine, qml_root(), logging.getLogger("native-workspace"))
    window._native_input_clock = NativeInputClock(window)
    evidence = {"directory": args.output, "mode": args.mode}
    settlement = None
    try:
        resize_native_window(window, 1440, 900)
        name = "long60.mp4" if args.mode == 'wheel-fallback' else "fractional.mp4" if args.mode == "fractional" else "variable.mp4" if args.mode == "unreliable" else "thirty.mp4" if args.mode in ("thirty", "input") else "integral.mp4"
        rates = {"long60.mp4":Fraction(60),"integral.mp4": Fraction(25), "thirty.mp4": Fraction(30), "fractional.mp4": Fraction(24000, 1001), "variable.mp4": None}
        settlement = open_source(app, window, model, args.fixtures / name, rate=rates[name])
        if args.mode in ("integral", "thirty", "fractional"):
            frame_checks(app, window, model, rates[name], evidence, settlement)
        elif args.mode == 'input':
            native_input_checks(app, window, model, evidence, settlement)
        elif args.mode == "unreliable":
            unreliable_checks(app, window, model, evidence, settlement)
        elif args.mode == "layout":
            for name in ("fractional.mp4", "variable.mp4"):
                settlement.close()
                settlement = None
                settlement = open_source(app, window, model, args.fixtures / name, rate=rates[name])
            layout_checks(app, window, model, evidence)
        elif args.mode == "icons":
            icon_checks(app, engine, window, evidence)
        elif args.mode == "product":
            from product_check_native import product_checks
            product_checks(app, window, model, args.fixtures, evidence, settlement)
            settlement = None
        elif args.mode == 'late-timing':
            from reviewer_findings_native import late_timing_checks
            late_timing_checks(app, window, model, args.fixtures, evidence, settlement)
            settlement = None
        elif args.mode == 'trim-boundary':
            from reviewer_findings_native import trim_render_checks
            evidence['trim_render_boundary'] = trim_render_checks(app,window,model,settlement,args.output/'trim-paint')
        elif args.mode == 'wheel-fallback':
            from reviewer_findings_native import crop_wheel_fallback
            evidence['crop_wheel_fallback'] = crop_wheel_fallback(app,window,model)
        assert not warnings, warnings
        evidence.pop("directory")
        (args.output / (args.mode + ".json")).write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps(evidence))
    except Exception as error:
        partial={key:value for key,value in evidence.items() if key!='directory'}
        partial['failure']=repr(error)
        (args.output/'failed.json').write_text(json.dumps(partial,indent=2),encoding='utf-8')
        raise
    finally:
        if settlement is not None:
            settlement.close()
        # Match application shutdown: the native window closes before the
        # model retires its active decoder and presentation sinks.
        try:
            close_native_windows(window)
        finally:
            model.shutdown()
        shiboken6.delete(engine)
        shiboken6.delete(model.playback._audio)


if __name__ == "__main__":
    main()
