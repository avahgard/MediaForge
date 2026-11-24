import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QUrl, Signal

from mediaforge.application.contracts.media import (
    MediaMetadataDto,
    OpenMediaErrorCode,
    OpenMediaResult,
)
from mediaforge.application.services.media import OpenMediaService
from mediaforge.infrastructure.ffmpeg import FfprobeMediaMetadataProbe
from mediaforge.presentation.bridge.media import MediaViewModel
from mediaforge.presentation.models.media import MediaMetadataView


@pytest.fixture(scope="module")
def qt_app():
    return QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture(autouse=True)
def retire_test_models(monkeypatch, qt_app):
    # Failed assertions must also retire probe QThreads. Otherwise Qt aborts the
    # entire campaign before pytest can report the actual failed assertion.
    models = []
    original = MediaViewModel.__init__
    def observed(model, *args, **kwargs):
        original(model, *args, **kwargs)
        models.append(model)
    monkeypatch.setattr(MediaViewModel, '__init__', observed)
    yield
    for model in reversed(models):
        model.shutdown()
    qt_app.processEvents()


def wait_for(qt_app, condition, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if condition():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for Qt state transition")


def success_result(path):
    return OpenMediaResult(
        metadata=MediaMetadataDto(
            source_name=path.name,
            source_path=str(path),
            format_name="matroska,webm",
            duration_seconds=62.25,
            video_codec="hevc",
            width=3840,
            height=2160,
            frame_rate=25.0,
        )
    )


class ResultUseCase:
    def __init__(self, *results):
        self.results = list(results)
        self.paths = []

    def open_media(self, source_path):
        self.paths.append(source_path)
        return self.results.pop(0)

    def cancel(self):
        pass


class RecordingPlayback(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.events = []
        # This video-only spy implements the current playback/session surface;
        # it does not claim to decode media or provide native lifecycle proof.
        self.sequence = SimpleNamespace(item=None)
        self.imageMode = False
        self.position = 0
        self._timeline_signature = None
        self._origin_established = False
        self.posters = None
        self._source = None

    def begin_loading(self): self.events.append(("loading",))
    def set_source(self, path):
        self._source = path
        self.events.append(("source", path))
    def clear_source(self):
        self._source = None
        self.events.append(("clear",))
    def shutdown(self): self.events.append(("shutdown",))
    def configure_timeline(self, item_id, source_path): pass
    def forget_timeline(self, item_id): pass
    def timeline_source_changed(self, item_id, path): return False
    def configure_trim_loop(self, enabled, in_ms, out_ms): pass


def test_workspace_bridge_switch_and_independent_edit_state(qt_app, tmp_path):
    first = tmp_path / "first кириллица.mp4"
    second = tmp_path / "second video.mp4"
    use_case = ResultUseCase(success_result(first), success_result(second))
    playback = RecordingPlayback()
    model = MediaViewModel(use_case, playback_controller=playback)
    assert model.openMedia(QUrl.fromLocalFile(str(first)))
    wait_for(qt_app, lambda: model.state == "loaded" and model.canOpen)
    first_id = model.activeMediaId
    assert model.selectedOperation == "trim"
    model.selectOperation("crop")
    assert model.selectedOperation == "crop" and not model.editState["cropEnabled"]
    model.setOperationEnabled("trim", True)
    assert model.editState["trimEnabled"] and model.selectedOperation == "crop"
    assert model.setTrimRange(1000, 5000)
    assert model.setCropRect(20, 22, 500, 400)
    assert model.setResizeSize(1280, 720)
    model.setCropPreview(True)
    assert model.openMedia(QUrl.fromLocalFile(str(second)))
    wait_for(qt_app, lambda: model.state == "loaded" and model.canOpen)
    second_id = model.activeMediaId
    assert second_id != first_id and len(model.mediaItems) == 2
    assert not model.editState["trimEnabled"] and model.editState["trimIn"] == 0
    assert model.selectMedia(first_id)
    assert model.editState["trimEnabled"] and model.editState["trimIn"] == 1000
    assert model.editState["cropX"] == 20 and model.editState["cropPreview"]
    assert model.editState["resizeWidth"] == 1280
    assert playback.events[-1] == ("source", str(first))
    assert model.openMedia(QUrl.fromLocalFile(str(first.parent / "." / first.name)))
    assert len(model.mediaItems) == 2 and [Path(path) for path in use_case.paths] == [first, second]
    assert model.duplicateMedia(first_id)
    duplicate_id = model.mediaItems[1]["id"]
    assert duplicate_id != first_id and model.mediaItems[1]["name"] == first.name
    count = len(playback.events)
    assert model.selectMedia(duplicate_id)
    assert len(playback.events) == count  # same source, no player replacement
    assert model.editState["cropX"] == 20
    assert model.moveMedia(duplicate_id, -1)
    assert model.mediaItems[0]["id"] == duplicate_id
    assert model.removeMedia(duplicate_id)
    assert model.activeMediaId == first_id
    assert model.removeMedia(first_id)
    assert model.activeMediaId == second_id
    assert model.removeMedia(second_id)
    assert model.state == "empty" and model.mediaItems == []
    model.shutdown()


def test_empty_loading_loaded_and_cancel_preserves_state(qt_app, tmp_path):
    source = tmp_path / "Видео с пробелом.mkv"
    use_case = ResultUseCase(success_result(source))
    view_model = MediaViewModel(use_case)

    assert view_model.state == "empty"
    assert not view_model.openMedia(QUrl())
    view_model.cancelSelection()
    assert view_model.state == "empty"

    assert view_model.openMedia(QUrl.fromLocalFile(str(source)))
    assert view_model.state == "loading"
    assert not view_model.canOpen
    wait_for(qt_app, lambda: view_model.state == "loaded" and view_model.canOpen)

    assert [Path(path) for path in use_case.paths] == [source]
    assert view_model.fileName == source.name
    assert view_model.localPath == str(source)
    assert view_model.duration == "1:02.250"
    assert view_model.resolution == "3840 × 2160"
    assert view_model.frameRate == "25 fps"
    view_model.cancelSelection()
    assert view_model.state == "loaded"


def test_failed_replacement_preserves_previous_loaded_result(qt_app, tmp_path):
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    error = OpenMediaResult(
        error_code=OpenMediaErrorCode.FFPROBE_UNAVAILABLE,
        error_message="FFprobe is not configured.",
    )
    entered, release = threading.Event(), threading.Event()
    class ReplacementUseCase(ResultUseCase):
        def open_media(self, path):
            if Path(path) == second:
                entered.set()
                assert release.wait(2), 'Test did not release the replacement probe'
            return super().open_media(path)
        def cancel(self): release.set()
    view_model = MediaViewModel(ReplacementUseCase(success_result(first), error))

    assert view_model.openMedia(QUrl.fromLocalFile(str(first)))
    wait_for(qt_app, lambda: view_model.state == "loaded" and view_model.canOpen)
    assert view_model.fileName == first.name
    first_id = view_model.activeMediaId

    # TASK-011 validate/prepare-before-activate preserves the active item on
    # failure; a candidate probe disables import but is not an empty Viewer.
    try:
        assert view_model.openMedia(QUrl.fromLocalFile(str(second)))
        assert entered.wait(2)
        assert not view_model.canOpen and view_model.state == "loaded"
        assert view_model.fileName == first.name and view_model.localPath == str(first)
        assert view_model.activeMediaId == first_id and view_model.errorMessage == ""
    finally:
        release.set()
    wait_for(qt_app, lambda: view_model.canOpen and bool(view_model.errorMessage))
    assert view_model.state == "loaded" and view_model.activeMediaId == first_id
    assert view_model.fileName == first.name and view_model.localPath == str(first)
    assert len(view_model.mediaItems) == 1
    assert view_model.errorMessage == "FFprobe is not configured."


def test_failed_candidate_never_replaces_or_clears_active_playback(qt_app, tmp_path):
    first = tmp_path / "first playable.mp4"
    second = tmp_path / "failed replacement.mp4"
    error = OpenMediaResult(
        error_code=OpenMediaErrorCode.PROBE_FAILED,
        error_message="Media metadata could not be read.",
    )
    playback = RecordingPlayback()
    view_model = MediaViewModel(
        ResultUseCase(success_result(first), error),
        playback_controller=playback,
    )

    assert view_model.openMedia(QUrl.fromLocalFile(str(first)))
    wait_for(qt_app, lambda: view_model.state == "loaded" and view_model.canOpen)
    assert playback.events == [("source", str(first))]
    first_id = view_model.activeMediaId

    assert view_model.openMedia(QUrl.fromLocalFile(str(second)))
    wait_for(qt_app, lambda: view_model.canOpen and bool(view_model.errorMessage))
    assert view_model.state == "loaded" and view_model.activeMediaId == first_id
    assert view_model.fileName == first.name and view_model.localPath == str(first)
    assert view_model.errorMessage == "Media metadata could not be read."
    assert playback.events == [("source", str(first))]  # no decoder teardown/clear
    assert len(view_model.mediaItems) == 1
    assert ("source", str(second)) not in playback.events
    view_model.shutdown()


def test_non_local_url_is_rejected_without_reaching_application(qt_app):
    use_case = ResultUseCase()
    view_model = MediaViewModel(use_case)

    assert not view_model.openMedia(QUrl("https://example.invalid/video.mp4"))
    assert view_model.state == "error"
    assert use_case.paths == []


def test_second_open_is_rejected_while_probe_is_running(qt_app, tmp_path):
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    release = threading.Event()

    class BlockingUseCase:
        def __init__(self):
            self.paths = []

        def open_media(self, source_path):
            self.paths.append(source_path)
            release.wait(1.0)
            return success_result(first)

    use_case = BlockingUseCase()
    view_model = MediaViewModel(use_case)

    assert view_model.openMedia(QUrl.fromLocalFile(str(first)))
    assert not view_model.openMedia(QUrl.fromLocalFile(str(second)))
    release.set()
    wait_for(qt_app, lambda: view_model.state == "loaded" and view_model.canOpen)
    assert [Path(path) for path in use_case.paths] == [first]


@pytest.mark.integration
def test_shutdown_terminates_blocking_probe_process_and_thread(qt_app, tmp_path):
    executable = tmp_path / "ffprobe.exe"
    executable.write_bytes(b"explicit fake executable")
    source = tmp_path / "blocking source.mp4"
    source.write_bytes(b"source")
    processes = []

    def blocking_process_factory(_command, **kwargs):
        process = subprocess.Popen(
            [sys._base_executable, "-c", "import time; time.sleep(60)"],
            **kwargs,
        )
        processes.append(process)
        return process

    probe = FfprobeMediaMetadataProbe(
        executable,
        process_factory=blocking_process_factory,
    )
    view_model = MediaViewModel(OpenMediaService(probe))

    assert view_model.openMedia(QUrl.fromLocalFile(str(source)))
    wait_for(qt_app, lambda: bool(processes) and processes[0].poll() is None)
    thread = view_model._thread
    assert thread is not None and thread.isRunning()

    started = time.monotonic()
    view_model.shutdown()
    elapsed = time.monotonic() - started

    assert elapsed < 5
    assert processes[0].poll() is not None
    assert not thread.isRunning()
    assert view_model._thread is None
    assert view_model.state == "error"


@pytest.mark.integration
def test_queued_success_cannot_restore_metadata_after_shutdown(qt_app, tmp_path):
    executable = tmp_path / "ffprobe.exe"
    executable.write_bytes(b"explicit fake executable")
    source = tmp_path / "queued success.mp4"
    source.write_bytes(b"source")
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "24/1",
                "disposition": {"attached_pic": 0},
            }
        ],
        "format": {"format_name": "mov", "duration": 10.0},
    }
    process_output = json.dumps(payload)
    processes = []

    def successful_process_factory(_command, **kwargs):
        process = subprocess.Popen(
            [
                sys._base_executable,
                "-c",
                f"import sys; sys.stdout.write({process_output!r})",
            ],
            **kwargs,
        )
        processes.append(process)
        return process

    probe = FfprobeMediaMetadataProbe(
        executable,
        process_factory=successful_process_factory,
    )
    service = OpenMediaService(probe)
    operation_completed = threading.Event()

    class ObservedUseCase:
        def open_media(self, source_path):
            result = service.open_media(source_path)
            operation_completed.set()
            return result

        def cancel(self):
            service.cancel()

    view_model = MediaViewModel(ObservedUseCase())

    assert view_model.openMedia(QUrl.fromLocalFile(str(source)))
    assert operation_completed.wait(2.0)
    time.sleep(0.05)
    assert view_model.state == "loading"
    thread = view_model._thread
    assert thread is not None

    view_model.shutdown()

    assert view_model.state == "error"
    assert view_model.fileName == ""
    assert view_model.localPath == ""
    assert view_model._metadata is None
    assert view_model._thread is None
    assert not view_model.canOpen
    assert not thread.isRunning()
    assert processes and processes[0].poll() is not None
    assert probe._process is None

    for _ in range(5):
        qt_app.processEvents()
        time.sleep(0.005)

    assert view_model.state == "error"
    assert view_model.fileName == ""
    assert view_model.localPath == ""
    assert view_model._metadata is None
    assert view_model._thread is None
    assert not view_model.canOpen
    assert processes[0].poll() is not None
    assert probe._process is None


def test_non_finite_ffprobe_output_reaches_terminal_bridge_error(qt_app, tmp_path):
    executable = tmp_path / "ffprobe.exe"
    executable.write_bytes(b"explicit fake executable")
    source = tmp_path / "non finite.mp4"
    source.write_bytes(b"source")
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "24/1",
                "disposition": {"attached_pic": 0},
            }
        ],
        "format": {"format_name": "mov", "duration": float("inf")},
    }

    class ImmediateProcess:
        returncode = 0

        def communicate(self, timeout=None):
            return json.dumps(payload), ""

        def poll(self):
            return self.returncode

    probe = FfprobeMediaMetadataProbe(
        executable,
        process_factory=lambda _command, **_kwargs: ImmediateProcess(),
    )
    view_model = MediaViewModel(OpenMediaService(probe))

    assert view_model.openMedia(QUrl.fromLocalFile(str(source)))
    wait_for(qt_app, lambda: view_model.state == "error" and view_model.canOpen)

    assert "metadata response was not valid" in view_model.errorMessage
    assert view_model.state != "loading"


def test_formatting_exception_still_leaves_loading_in_terminal_error(
    qt_app, tmp_path, monkeypatch
):
    source = tmp_path / "formatting.mp4"
    view_model = MediaViewModel(ResultUseCase(success_result(source)))

    def fail_formatting(_metadata):
        raise OverflowError("unexpected formatting failure")

    monkeypatch.setattr(MediaMetadataView, "from_dto", fail_formatting)

    assert view_model.openMedia(QUrl.fromLocalFile(str(source)))
    wait_for(qt_app, lambda: view_model.state == "error" and view_model.canOpen)

    assert "could not be displayed" in view_model.errorMessage
    assert view_model.state != "loading"
