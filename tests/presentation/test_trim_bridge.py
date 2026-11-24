import logging
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QUrl

from mediaforge.application.contracts.media import MediaMetadataDto, OpenMediaResult
from mediaforge.application.services.trim import TrimService
from mediaforge.features.media.trim import TrimError, TrimErrorCode, TrimResult
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegRuntime
from mediaforge.infrastructure.ffmpeg.trim import FfmpegTrimExecutor
from mediaforge.presentation.bridge.media import MediaViewModel


@pytest.fixture(scope="module")
def app():
    return QCoreApplication.instance() or QCoreApplication([])


def wait(app, condition):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(.003)
    raise AssertionError("Qt transition timeout")


class Open:
    def open_media(self, path):
        return OpenMediaResult(metadata=MediaMetadataDto(Path(path).name, path, "mp4", 10, "h264", 64, 48, 25))
    def cancel(self):
        pass


class Executor:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.fail = False
    def execute(self, request, control):
        self.entered.set()
        while not self.release.wait(.002):
            control.check()
        control.check()
        if self.fail:
            raise TrimError(TrimErrorCode.PROCESS_FAILED)
        return TrimResult(request.source.with_name("result.mp4"))


def test_presentation_states_retry_and_source_reset(app, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"SOURCE")
    executor = Executor()
    model = MediaViewModel(Open(), trim_use_case=TrimService(executor))
    trim = model.trim
    try:
        assert not trim.canTrim and not trim.canCancel
        assert model.openMedia(QUrl.fromLocalFile(str(source)))
        wait(app, lambda: model.state == "loaded" and model.canOpen)
        assert trim.startText == "0" and float(trim.endText) == 10
        trim.startText, trim.endText = "3", "2"
        assert not trim.startTrim()
        assert trim.state == "Failed" and trim.message and trim.canTrim
        trim.startText = "not a time"
        assert not trim.startTrim()
        trim.startText, trim.endText = "1", "3"
        assert trim.startTrim() and executor.entered.wait(1)
        assert trim.state == "Running" and trim.canCancel and not trim.canTrim
        assert not model.canOpen and not model.openMedia(QUrl.fromLocalFile(str(tmp_path / "other.mp4")))
        assert not trim.startTrim()
        trim.cancelTrim()
        assert not trim.canCancel
        assert trim.state in ("Cancelling", "Cancelled")
        trim.cancelTrim()
        wait(app, lambda: trim.state == "Cancelled" and trim.canTrim)
        assert Path(model.localPath) == source
        executor.release.set()
        executor.fail = True
        assert trim.startTrim()
        wait(app, lambda: trim.state == "Failed" and trim.canTrim)
        executor.fail = False
        assert trim.startTrim()
        wait(app, lambda: trim.state == "Completed" and trim.canTrim)
        assert trim.outputPath == str(tmp_path / "result.mp4")
        assert Path(model.localPath) == source
        other = tmp_path / "other.mp4"
        other.write_bytes(b"OTHER")
        assert model.openMedia(QUrl.fromLocalFile(str(other)))
        wait(app, lambda: model.state == "loaded" and model.canOpen)
        assert trim.startText == "0" and float(trim.endText) == 10
        assert trim.state == "Pending" and trim.outputPath == ""
    finally:
        model.shutdown()


@pytest.mark.integration
def test_shutdown_reaps_real_controlled_child_and_cleans_staging(app, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"SOURCE")
    pair = FfmpegRuntime(tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe")
    processes = []
    def factory(command, **kwargs):
        Path(command[-1]).write_bytes(b"PARTIAL")
        child = subprocess.Popen([sys._base_executable, "-c", "import time; time.sleep(60)"], **kwargs)
        processes.append(child)
        return child
    service = TrimService(FfmpegTrimExecutor(pair, process_factory=factory))
    model = MediaViewModel(Open(), trim_use_case=service)
    assert model.openMedia(QUrl.fromLocalFile(str(source)))
    wait(app, lambda: model.canOpen and model.state == "loaded")
    assert model.trim.startTrim()
    wait(app, lambda: bool(processes))
    started = time.monotonic()
    model.shutdown()
    assert time.monotonic() - started < 6
    assert processes[0].poll() is not None and not service.busy
    assert service.snapshot().state == "Cancelled"
    assert list(tmp_path.iterdir()) == [source]
    for _ in range(5):
        app.processEvents()
    assert not model.canOpen and not model.trim.canTrim
    assert model.trim.outputPath == ""


def test_completed_queued_result_does_not_update_closed_presentation(app, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"SOURCE")
    executor = Executor()
    service = TrimService(executor)
    model = MediaViewModel(Open(), trim_use_case=service)
    assert model.openMedia(QUrl.fromLocalFile(str(source)))
    wait(app, lambda: model.canOpen and model.state == "loaded")
    assert model.trim.startTrim()
    executor.release.set()
    service._thread.join(2)
    assert service.snapshot().state == "Completed"
    model.shutdown()
    model.trim.refresh()
    assert model.trim.outputPath == ""


@pytest.mark.integration
def test_composition_resolves_once_and_injects_identical_pair(app, tmp_path, monkeypatch):
    from mediaforge.bootstrap import application
    pair = FfmpegRuntime(tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe")
    calls = []
    def resolve(**kwargs):
        calls.append(1)
        return pair
    monkeypatch.setattr(application, "resolve_ffmpeg_runtime", resolve)
    model = application.build_media_view_model(logging.getLogger("test"))
    assert calls == [1]
    assert model._use_case._probe._executable == pair.ffprobe
    assert model.trim._use_case._executor._runtime is pair
    model.shutdown()
