"""Deterministic F1 regressions: cancellation and child ownership are ordered."""

import io
import json
from pathlib import Path
import subprocess
import threading

import pytest

from mediaforge.features.media.trim import (
    TrimControl, TrimError, TrimErrorCode, TrimRange, TrimRequest,
)
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegRuntime
from mediaforge.infrastructure.ffmpeg import trim


class Child:
    def __init__(self, staged, *, running=False, force=False, accepted=None):
        self.staged = staged
        self.returncode = None if running else 0
        self.force = force
        self.accepted = accepted
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.actions = []

    def communicate(self, timeout):
        if self.accepted is not None:
            assert self.accepted.wait(2), "Cancellation never returned"
            raise subprocess.TimeoutExpired("controlled-child", timeout)
        return json.dumps({
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 64, "height": 48}],
            "format": {"format_name": "mp4", "duration": "1"},
        }), ""

    def poll(self):
        return self.returncode

    def terminate(self):
        assert self.staged.is_file(), "Cleanup ran before termination"
        self.actions.append("terminate")
        if not self.force:
            self.returncode = -15

    def wait(self, timeout):
        assert self.staged.is_file(), "Cleanup ran before reaping"
        self.actions.append(("wait", timeout))
        if self.returncode is None:
            raise subprocess.TimeoutExpired("controlled-child", timeout)
        return self.returncode

    def kill(self):
        assert self.staged.is_file(), "Cleanup ran before kill"
        self.actions.append("kill")
        self.returncode = -9


def request_and_pair(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"SOURCE")
    return TrimRequest(source, TrimRange(0.0, 1.0)), FfmpegRuntime(
        tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe",
    )


@pytest.mark.parametrize("phase,check_number", [("ffmpeg", 2), ("ffprobe", 6)])
def test_cancel_wins_in_reviewer_check_to_spawn_gap(tmp_path, phase, check_number):
    request, pair = request_and_pair(tmp_path)

    class CancelAfterCheck(TrimControl):
        def __init__(self):
            super().__init__()
            self.checks = 0
            self.accepted = False

        def check(self):
            super().check()
            self.checks += 1
            if self.checks == check_number:
                # Exactly the reviewer's gap: return normally from the last
                # optimistic check after cancellation has already been accepted.
                self.accepted = self.cancel()

    control = CancelAfterCheck()
    calls = []
    children = []

    def factory(command, **kwargs):
        calls.append(command[0])
        staged = Path(command[-1])
        staged.write_bytes(b"STAGED")
        child = Child(staged)
        children.append(child)
        return child

    with pytest.raises(TrimError) as error:
        trim.FfmpegTrimExecutor(pair, process_factory=factory).execute(request, control)
    assert error.value.code == TrimErrorCode.CANCELLED
    assert control.accepted and not control.cancel()
    assert str(getattr(pair, phase)) not in calls
    assert len(calls) == (0 if phase == "ffmpeg" else 1)
    assert all(child.poll() is not None for child in children)
    assert list(tmp_path.iterdir()) == [request.source]


@pytest.mark.parametrize("phase", ["ffmpeg", "ffprobe"])
@pytest.mark.parametrize("force", [False, True])
def test_spawn_wins_and_is_owned_before_cancel_acceptance(tmp_path, monkeypatch, phase, force):
    request, pair = request_and_pair(tmp_path)
    control = TrimControl()
    factory_entered = threading.Event()
    cancel_attempted = threading.Event()
    factory_returning = threading.Event()
    owned = threading.Event()
    accepted = threading.Event()
    outputs, children, results, cancellations, errors = [], [], [], [], []
    real_output = trim.TrimOutput

    class ObservedOutput(real_output):
        def __enter__(self):
            outputs.append(self)
            return super().__enter__()

    monkeypatch.setattr(trim, "TrimOutput", ObservedOutput)
    real_gate = control._gate

    class ObservedGate:
        def __enter__(self):
            if threading.current_thread().name == "test-canceller":
                cancel_attempted.set()
            assert real_gate.acquire(timeout=2), "Gate deadlock"

        def __exit__(self, *args):
            try:
                if (threading.current_thread().name == "test-spawner"
                        and factory_returning.is_set() and not owned.is_set()):
                    # The live-child cleanup guard must already capture the
                    # child when the spawn critical section releases the gate.
                    assert not outputs[-1].cleanup_guard()
                    owned.set()
            finally:
                real_gate.release()

    control._gate = ObservedGate()

    def factory(command, **kwargs):
        staged = Path(command[-1])
        staged.write_bytes(b"STAGED")
        if command[0] == str(getattr(pair, phase)):
            factory_entered.set()
            assert cancel_attempted.wait(2), "Cancel did not attempt the gate"
            child = Child(staged, running=True, force=force, accepted=accepted)
            children.append(child)
            factory_returning.set()
            return child
        return Child(staged)

    adapter = trim.FfmpegTrimExecutor(pair, process_factory=factory)

    def execute():
        try:
            adapter.execute(request, control)
        except TrimError as error:
            results.append(error.code)
        except Exception as error:
            errors.append(error)

    def cancel():
        try:
            result = control.cancel()
            cancellations.append((result, owned.is_set()))
            cancellations.append((control.cancel(), owned.is_set()))
        except Exception as error:
            errors.append(error)
        finally:
            accepted.set()

    worker = threading.Thread(target=execute, name="test-spawner")
    canceller = threading.Thread(target=cancel, name="test-canceller")
    worker.start()
    assert factory_entered.wait(2)
    canceller.start()
    worker.join(3)
    canceller.join(3)
    assert not worker.is_alive() and not canceller.is_alive()
    assert not errors
    assert cancellations == [(True, True), (False, True)]
    assert results == [TrimErrorCode.CANCELLED]
    assert children[0].poll() is not None
    assert children[0].actions == (["terminate", ("wait", 2.0), "kill", ("wait", 2.0)]
                                    if force else ["terminate", ("wait", 2.0)])
    assert list(tmp_path.iterdir()) == [request.source]
