import io
import json
from pathlib import Path
import subprocess
import threading

import pytest

from mediaforge.features.media.trim import TrimControl, TrimError, TrimErrorCode, TrimRange, TrimRequest
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegRuntime
from mediaforge.infrastructure.ffmpeg.trim import FfmpegTrimExecutor


def payload():
    return {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 64, "height": 48}],
            "format": {"format_name": "mp4", "duration": "2.0"}}


class Process:
    def __init__(self, stdout="", code=0, block=False, force=False, on_poll=None):
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.output = stdout
        self.returncode = None if block else code
        self.force = force
        self.on_poll = on_poll
        self.terminated = self.killed = False
        self.waits = []

    def communicate(self, timeout):
        if self.on_poll:
            self.on_poll()
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.output, "RAW process diagnostic"

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self.force:
            self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout):
        self.waits.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode


def setup(tmp_path):
    source = tmp_path / "Папка с пробелами" / "видео файл.mp4"
    source.parent.mkdir()
    source.write_bytes(b"SOURCE")
    runtime = FfmpegRuntime(tmp_path / "validated/ffmpeg.exe", tmp_path / "validated/ffprobe.exe")
    return source, runtime, TrimRequest(source, TrimRange(1.0, 3.0))


def test_exact_command_same_pair_validation_and_staging(tmp_path):
    source, pair, request = setup(tmp_path)
    calls = []
    def factory(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == str(pair.ffmpeg):
            Path(command[-1]).write_bytes(b"TRIM")
            return Process()
        assert command[0] == str(pair.ffprobe)
        return Process(json.dumps(payload()))
    result = FfmpegTrimExecutor(pair, process_factory=factory).execute(request, TrimControl())
    command, options = calls[0]
    assert isinstance(command, list) and options["shell"] is False
    assert command[command.index("-c") + 1] == "copy"
    assert [command[i + 1] for i, v in enumerate(command) if v == "-map"] == ["0:v:0", "0:a?"]
    assert command[command.index("-ss") + 1] == "1.0"
    assert command[command.index("-t") + 1] == "2.0"
    assert command.count(str(source)) == 1
    assert command[-1] == calls[1][0][-1] != str(result.output)
    assert Path(command[-1]).suffix == source.suffix
    assert "-nostdin" in command and "-n" in command
    assert not {"-y", "libx264", "libx265", "aac", "-crf", "-vf", "-b:v"}.intersection(command)
    assert all(c[1]["shell"] is False for c in calls)
    assert result.output.read_bytes() == b"TRIM"
    assert not list(source.parent.glob(".mediaforge-trim-*"))


@pytest.mark.parametrize("failure,expected", [
    ("start", "start_failed"), ("exit", "process_failed"),
    ("missing", "invalid_output"), ("empty", "invalid_output"),
    ("probe-exit", "validation_failed"), ("json", "validation_failed"),
    ("no-video", "validation_failed"), ("attached", "validation_failed"),
    ("dimensions", "validation_failed"), ("duration", "validation_failed"),
    ("nonfinite", "validation_failed"),
])
def test_failure_never_publishes_and_cleans(tmp_path, failure, expected):
    source, pair, request = setup(tmp_path)
    def factory(command, **kwargs):
        if command[0] == str(pair.ffmpeg):
            if failure == "start":
                raise OSError("start failed")
            if failure != "missing":
                Path(command[-1]).write_bytes(b"" if failure == "empty" else b"TRIM")
            return Process(code=2 if failure == "exit" else 0)
        data = payload()
        if failure == "no-video":
            data["streams"] = []
        if failure == "attached":
            data["streams"][0]["disposition"] = {"attached_pic": 1}
        if failure == "dimensions":
            data["streams"][0]["width"] = 0
        if failure == "duration":
            data["format"]["duration"] = "0"
        if failure == "nonfinite":
            data["format"]["duration"] = float("inf")
        return Process("invalid" if failure == "json" else json.dumps(data), code=1 if failure == "probe-exit" else 0)
    with pytest.raises(TrimError) as error:
        FfmpegTrimExecutor(pair, process_factory=factory).execute(request, TrimControl())
    assert error.value.code.value == expected
    assert list(source.parent.iterdir()) == [source]


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("phase", ["processing", "validation"])
def test_cancel_stops_before_cleanup(tmp_path, force, phase):
    source, pair, request = setup(tmp_path)
    control = TrimControl()
    processes = []
    stage = []
    def factory(command, **kwargs):
        staged = Path(command[-1])
        stage.append(staged)
        if command[0] == str(pair.ffmpeg):
            staged.write_bytes(b"PARTIAL")
            if phase == "validation":
                return Process()
        def cancel():
            assert staged.exists()
            control.cancel()
        process = Process(block=True, force=force, on_poll=cancel)
        original_wait = process.wait
        def wait(timeout):
            assert staged.exists(), "cleanup occurred while process was still owned"
            return original_wait(timeout)
        process.wait = wait
        processes.append(process)
        return process
    with pytest.raises(TrimError) as error:
        FfmpegTrimExecutor(pair, process_factory=factory).execute(request, control)
    assert error.value.code == TrimErrorCode.CANCELLED
    assert processes[0].terminated and processes[0].killed == force
    assert processes[0].returncode is not None
    assert all(v == 2 for v in processes[0].waits)
    assert list(source.parent.iterdir()) == [source]


def test_timeout_reaps_and_cleanup(tmp_path):
    source, pair, request = setup(tmp_path)
    process = Process(block=True, force=True)
    def factory(command, **kwargs):
        Path(command[-1]).write_bytes(b"PARTIAL")
        return process
    with pytest.raises(TrimError) as error:
        FfmpegTrimExecutor(pair, process_factory=factory, timeout_seconds=.001).execute(request, TrimControl())
    assert error.value.code == TrimErrorCode.TIMEOUT
    assert process.killed and process.returncode is not None
    assert list(source.parent.iterdir()) == [source]


def test_unavailable_and_cancel_before_spawn(tmp_path):
    source, pair, request = setup(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("Must not start a process")
    with pytest.raises(TrimError) as error:
        FfmpegTrimExecutor(None, process_factory=forbidden).execute(request, TrimControl())
    assert error.value.code == TrimErrorCode.RUNTIME_UNAVAILABLE
    control = TrimControl()
    control.cancel()
    with pytest.raises(TrimError) as error:
        FfmpegTrimExecutor(pair, process_factory=forbidden).execute(request, control)
    assert error.value.code == TrimErrorCode.CANCELLED
    assert list(source.parent.iterdir()) == [source]


def test_cancel_after_validation_before_publication(tmp_path, monkeypatch):
    from mediaforge.infrastructure.filesystem.trim_output import TrimOutput
    source, pair, request = setup(tmp_path)
    control = TrimControl()
    def factory(command, **kwargs):
        if command[0] == str(pair.ffmpeg):
            Path(command[-1]).write_bytes(b"TRIM")
            return Process()
        return Process(json.dumps(payload()))
    original = TrimOutput.publish
    def cancel_at_boundary(self, token):
        assert token.cancel()
        return original(self, token)
    monkeypatch.setattr(TrimOutput, "publish", cancel_at_boundary)
    with pytest.raises(TrimError) as error:
        FfmpegTrimExecutor(pair, process_factory=factory).execute(request, control)
    assert error.value.code == TrimErrorCode.CANCELLED
    assert list(source.parent.iterdir()) == [source]


def test_stop_failure_retains_staging_instead_of_deleting_live_output(tmp_path):
    source, pair, request = setup(tmp_path)
    control = TrimControl()
    process = Process(block=True, force=True, on_poll=control.cancel)
    def cannot_kill():
        raise OSError("simulated kernel refusal")
    process.kill = cannot_kill
    def factory(command, **kwargs):
        Path(command[-1]).write_bytes(b"PARTIAL")
        return process
    adapter = FfmpegTrimExecutor(pair, process_factory=factory)
    with pytest.raises(TrimError) as error:
        adapter.execute(request, control)
    assert error.value.code == TrimErrorCode.LIFECYCLE_FAILED
    assert process.poll() is None
    assert list(source.parent.glob(".mediaforge-trim-*/partial.mp4"))
    assert not list(source.parent.glob("*_trim*.mp4"))
    with pytest.raises(TrimError) as error:
        adapter.execute(request, TrimControl())
    assert error.value.code == TrimErrorCode.LIFECYCLE_FAILED


def test_unexpected_communication_error_still_reaps(tmp_path):
    source, pair, request = setup(tmp_path)
    process = Process(block=True)
    def broken(timeout):
        raise OSError("pipe failed")
    process.communicate = broken
    def factory(command, **kwargs):
        Path(command[-1]).write_bytes(b"PARTIAL")
        return process
    with pytest.raises(OSError):
        FfmpegTrimExecutor(pair, process_factory=factory).execute(request, TrimControl())
    assert process.terminated and process.poll() is not None
    assert list(source.parent.iterdir()) == [source]
