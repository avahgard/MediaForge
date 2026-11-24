"""Narrow PC2-F1-A/B: real QML sink origin and exceptional Qt teardown."""

import argparse
import json
import logging
import subprocess
from bisect import bisect_left
from pathlib import Path
from fractions import Fraction

from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoFrame
from PySide6.QtQml import QQmlApplicationEngine
import shiboken6

from native_window_policy import (load_native_shell as load_shell, resize_native_window, close_native_windows)
from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.resources.paths import qml_root
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
from native_workspace_regressions import wait, find, open_source, activate_native_input, DecodedFrameSettlement
from native_phase_diagnostics import NativePhaseDiagnostics


def run(fixtures, output):
    diagnostics = NativePhaseDiagnostics(output)
    try:
        _run(fixtures, output, diagnostics)
    finally:
        diagnostics.close()


def _run(fixtures, output, diagnostics):
    output.mkdir(parents=True, exist_ok=True)
    call = diagnostics.call
    app = call("application-create", lambda: QGuiApplication([]))
    diagnostics.bind(app=app)
    evidence = {}
    for case in ("nonzero-origin", "playing", "paused", "replacement-index-active", "replacement-during-scan"):
        diagnostics.case = case
        diagnostics.bind(playback=None, service=None, window=None, observer=None, sink=None, model=None)
        engine = call("engine-create", QQmlApplicationEngine)
        model = call("model-create", lambda: build_media_view_model(logging.getLogger("pc2-origin-shutdown")))
        diagnostics.bind(playback=model.playback, service=model.playback._timeline_service, model=model)
        diagnostics.track_factory(model.playback._timeline_service._indexer, "_factory")
        diagnostics.track_factory(model._use_case._probe, "_process_factory")
        engine.rootContext().setContextProperty("mediaViewModel", model)
        window = call("shell-load", lambda: load_shell(engine, qml_root(), logging.getLogger("pc2-origin-shutdown")))
        diagnostics.bind(window=window)
        p = model.playback
        service = p._timeline_service
        real_shutdown = service.shutdown
        observer = None
        try:
            resize_native_window(window, 1440, 900)
            sink = find(window, "viewerVideoOutput").property("videoSink")
            diagnostics.bind(sink=sink)
            if case == "nonzero-origin":
                # A stream-copy offset preserves the compressed pictures. Obtain
                # independent first-picture identity from the unshifted control;
                # do not assume Qt's origin equals video/container zero.
                from pc2_frame_native import frame_hash
                control_observer = call("control-source-open-prime-index", lambda: open_source(app, window, model, fixtures / "integral.mp4", rate=Fraction(25)))
                reference_first_hash = frame_hash(sink.videoFrame())
                control_observer.close()
                path = fixtures / "origin5.mp4"
                result = call("independent-origin-ffprobe", lambda: subprocess.run([str(resolve_ffmpeg_runtime().ffprobe), "-v", "error",
                    "-select_streams", "v:0", "-show_frames", "-show_entries",
                    "frame=best_effort_timestamp_time:frame_side_data=", "-of", "json", str(path)],
                    capture_output=True, text=True, encoding="utf-8", timeout=30))
                assert result.returncode == 0, result.stderr
                times = tuple(round(Fraction(f["best_effort_timestamp_time"]) * 1000000)
                              for f in json.loads(result.stdout)["frames"])
                assert len(times) == 100 and times[0] == 5000000
                assert call("nonzero-source-open", lambda: model.openMedia(QUrl.fromLocalFile(str(path))))
                call("nonzero-origin-settlement", lambda: wait(app, lambda: p.exactStepAvailable, "nonzero source timeline and actual origin", timeout=30))
                qt_origin = sink.videoFrame().startTime()
                assert qt_origin >= 0 and frame_hash(sink.videoFrame()) == reference_first_hash
                relative = tuple(t - times[0] + qt_origin for t in times)

                def expected_label(frame):
                    index = bisect_left(relative, frame.startTime())
                    matches = [i for i in (index - 1, index) if 0 <= i < len(relative)
                               and abs(relative[i] - frame.startTime()) <= 2]
                    assert len(matches) == 1, (frame.startTime(), relative[:3])
                    return f"Frame {matches[0] + 1} / {len(times)}"

                observer = DecodedFrameSettlement(sink, p, expected_label)
                diagnostics.bind(observer=observer)
                call("first-decoded-origin-proof", lambda: observer.wait_for("nonzero-origin first actual picture", predicate=lambda f: f.startTime() == qt_origin))
            else:
                observer = call("source-open-prime-index", lambda: open_source(app, window, model, fixtures / "integral.mp4", rate=Fraction(25)))
                diagnostics.bind(observer=observer)
            assert p.frameText == "Frame 1 / 100" and p._origin_established
            offset = p._timeline_offset
            if case == "nonzero-origin":
                assert p._timeline.timestamps[0] == 5000000 and offset == qt_origin - 5000000
                first_hash = frame_hash(sink.videoFrame())
                revision = observer.revision
                call("nonzero-adjacent-request", lambda: p.stepFrame(1))
                frame = call("nonzero-adjacent-decoded", lambda: observer.wait_for("nonzero source-origin next actual picture", after_revision=revision,
                    predicate=lambda f: f.startTime() == p._first_presented_start + 40000))
                assert p.frameText == "Frame 2 / 100" and p.framesReliable
                assert frame_hash(frame) != first_hash
                revision = observer.revision
                call("nonzero-arbitrary-seek", lambda: p.seek(1040))  # Interior of picture 26 with AAC preroll preserved.
                frame = call("nonzero-seek-decoded", lambda: observer.wait_for("arbitrary seek retains constant offset", after_revision=revision,
                    predicate=lambda f: f.startTime() == p._first_presented_start + 1000000))
                assert p.frameText == "Frame 26 / 100" and p._timeline_offset == offset
                evidence[case] = {"ffprobe_origin_us": 5000000, "qt_origin_us": p._first_presented_start,
                    "constant_offset_us": offset, "second_label": "Frame 2 / 100",
                    "seek_decoded_us": frame.startTime(), "seek_label": p.frameText}
                call("case-evidence-write", lambda: (output / "origin-shutdown.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8"))
                continue

            call("observer-close-before-shutdown", observer.close)
            observer = None
            diagnostics.bind(observer=None)
            retired = None
            child = control = request = None
            if case in ("replacement-index-active", "replacement-during-scan"):
                original_id = model.activeMediaId
                retired = p._player
                assert call("replace-with-owned-scan", lambda: model.openMedia(QUrl.fromLocalFile(str(fixtures / "practical.mp4"))))
                call("owned-scan-paused-first-frame", lambda: wait(app, lambda: model.state == "loaded" and p.canPlay and p._indexing
                     and service._current is not None and service._current.owns_process,
                     "replacement with owned active timeline and paused first picture", timeout=20))
                assert not shiboken6.isValid(retired)
                control, request = service._current, service._current_request
                with control._lock:
                    child = control._process
                assert child.poll() is None and p.state == "Paused"
                assert child in diagnostics.processes, "Owned timeline child missing from diagnostic inventory"
                if case == "replacement-during-scan":
                    scan_player = p._player
                    old_generation, old_index_generation = p._source_generation, p._timeline_generation
                    assert call("replace-active-owned-scan-with-successor", lambda: model.selectMedia(original_id))
                    call("successor-decoded-and-old-child-reaped", lambda: wait(app,
                        lambda: p.exactStepAvailable and child.poll() is not None and not control.owns_process,
                        "cached successor frame and actual cancellation", timeout=20))
                    assert not shiboken6.isValid(scan_player) and p.frameText == "Frame 1 / 100"
                    assert p.state == "Paused" and p._player is not scan_player
                    from mediaforge.application.contracts.source_timeline import TimelineResult
                    p._on_timeline_result(TimelineResult(request, None), old_generation, old_index_generation)
                    assert p.frameText == "Frame 1 / 100" and p.exactStepAvailable
            else:
                assert p.state == "Paused"
            if case == "playing":
                call("playback-start", p.togglePlayback)
                call("playing-decoded-delivery", lambda: wait(app, lambda: p.playing and p._player.playbackState() == QMediaPlayer.PlayingState
                     and sink.videoFrame().isValid() and sink.videoFrame().startTime() > p._first_presented_start,
                     "actual native playing decoded delivery"))

            player = p._player
            staging = p._staging_sink
            assert len(p.findChildren(QMediaPlayer)) == len(p.findChildren(QAudioOutput)) == 1
            stale_players = dict(p._player_signal_handlers)
            stale_frame = p._frame_handler
            generation, index_generation = p._source_generation, p._timeline_generation
            failure = RuntimeError("Source timeline cleanup failed; child ownership retained")
            calls = []

            def fail_shutdown():
                calls.append(True)
                raise failure

            service.shutdown = fail_shutdown
            before = {"state": p.state, "concrete": player.playbackState().name,
                      "source": player.source().toLocalFile(), "position": player.position(),
                      "indexing": p._indexing, "child_pid": child.pid if child else None}
            # The authorized final lifecycle fully disposes both native objects.
            # Preserve every stop/clear/detach assertion immediately BEFORE
            # disposal, then verify released ownership instead of querying a
            # destroyed C++ wrapper.
            released = {}
            original_disposer = p._player_disposer
            advances = []
            player.positionChanged.connect(lambda value: advances.append(value) if value != 0 else None)
            def checked_disposer(value):
                assert value is player
                assert value.playbackState() == QMediaPlayer.StoppedState
                assert value.source().isEmpty() and value.audioOutput() is None and value.videoSink() is None
                assert value.position() == 0
                released.update(concrete=value.playbackState().name,position=value.position(),
                    source_empty=value.source().isEmpty(),audio_detached=value.audioOutput() is None,
                    sink_detached=value.videoSink() is None)
                original_disposer(value)
            p._player_disposer = checked_disposer
            try:
                call("exceptional-playback-shutdown", p.shutdown)
            except RuntimeError as error:
                assert error is failure
            else:
                raise AssertionError("Ownership failure was suppressed")
            assert p._closed and p.state == "Empty" and not p.canPlay
            assert not shiboken6.isValid(player) and not shiboken6.isValid(staging)
            assert not p.findChildren(QMediaPlayer)
            assert not sink.videoFrame().isValid() and p._timeline is None
            assert not p._player_signal_handlers and p._frame_handler is None
            assert p._source_generation > generation and p._timeline_generation > index_generation
            assert not p._shutdown_pending
            # This is a bounded negative observation window AFTER native stop,
            # clear and detach were directly verified, not a readiness delay.
            advances.clear()
            loop = QEventLoop()
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            timer.start(250)
            call("stopped-position-negative-observation", loop.exec)
            assert not advances and p.position == 0
            stale_players["position"](9999)
            stale_players["playback_state"](QMediaPlayer.PlayingState)
            stale_players["media_status"](QMediaPlayer.EndOfMedia)
            stale_players["error"](QMediaPlayer.FormatError, "late decoder callback")
            stale_frame(QVideoFrame())
            if request is not None:
                from mediaforge.application.contracts.source_timeline import TimelineResult
                p._on_timeline_result(TimelineResult(request, None), generation, index_generation)
            call("stale-callback-event-drain", app.processEvents)
            assert p.state == "Empty" and p.position == p.duration == 0 and p._timeline is None
            try:
                call("repeated-exceptional-shutdown", p.shutdown)
            except RuntimeError as error:
                assert error is failure
            else:
                raise AssertionError("Repeated shutdown forgot the primary failure")
            assert len(calls) == 1
            evidence[case] = {"before": before, "after": {"state": p.state,
                **released, "player_disposed":not shiboken6.isValid(player),
                "staging_disposed":not shiboken6.isValid(staging),"sink_frame_cleared": not sink.videoFrame().isValid()},
                "observation_ms": 250, "position_advances": advances,
                "stale_callbacks_ignored": True, "repeated_same_exception": True,
                "retired_disposed": not shiboken6.isValid(retired) if retired else None}
        finally:
            if observer:
                call("observer-final-close", observer.close)
            # Undo ONLY test injection; reap any actual test scan. Production
            # Playback retains/re-raises the original failure even afterwards.
            service.shutdown = real_shutdown
            call("real-timeline-shutdown-and-reap", real_shutdown)
            try:
                call("model-final-shutdown", model.shutdown)
            except RuntimeError as error:
                assert p._shutdown_error is error
            call("window-close", lambda: close_native_windows(window))
            call("engine-delete", lambda: shiboken6.delete(engine))
            call("audio-delete", lambda: shiboken6.delete(p._audio))
            call("model-delete", lambda: shiboken6.delete(model))
        if child is not None:
            assert child.poll() is not None and not control.owns_process
            evidence[case]["test_owned_child_reaped_after_restoring_injection"] = child.returncode
        call("case-evidence-write", lambda: (output / "origin-shutdown.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8"))
    call("final-evidence-write", lambda: (output / "origin-shutdown.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.fixtures.resolve(), args.output.resolve())
