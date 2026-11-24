"""F-001: unresolved real ownership must survive worker completion and retry."""

import json
from pathlib import Path
import threading

import pytest

from crop_support import Child, CountingGate, finish, metadata, payload, setup
from mediaforge.application.services.crop import CropService
from mediaforge.application.services.trim import TrimService
from mediaforge.infrastructure.ffmpeg.crop import FfmpegCropExecutor
from mediaforge.infrastructure.ffmpeg.trim import FfmpegTrimExecutor


def make_service(operation, pair, factory, gate):
    service, executor = ((CropService, FfmpegCropExecutor) if operation == "crop"
                         else (TrimService, FfmpegTrimExecutor))
    return service(executor(pair, process_factory=factory), gate=gate)


def start(service, operation):
    return service.start(100, 40, 320, 240) if operation == "crop" else service.start(0, 1)


class RefusingChild(Child):
    def terminate(self):
        self.actions.append("terminate")
        raise OSError("controlled terminate refusal")

    def kill(self):
        self.actions.append("kill")
        raise OSError("controlled kill refusal")


def controlled_factory(operation, target, refuses):
    calls, children = [], []
    entered = threading.Event()
    staged = None
    def factory(command, **kwargs):
        nonlocal staged
        phase = len(calls)
        calls.append(command)
        if phase == (2 if operation == "crop" else 0):
            staged = Path(command[-1])
            staged.write_bytes(b"OWNED PARTIAL")
        data = payload(320, 240) if operation == "crop" and phase == 3 else payload()
        data["format"]["duration"] = "2"
        text = " V....D libx264 H.264" if operation == "crop" and phase == 1 else json.dumps(data)
        kind = RefusingChild if phase == target and refuses else Child
        child = kind(text, block=phase == target, staged=staged)
        children.append(child)
        if phase == target:
            entered.set()
        return child
    return factory, calls, children, entered


@pytest.mark.parametrize("operation,phase", [("crop", i) for i in range(4)] + [("trim", i) for i in range(2)])
def test_unreaped_child_poison_blocks_both_operations(tmp_path, operation, phase):
    request, pair = setup(tmp_path)
    gate = CountingGate()
    factory, calls, children, entered = controlled_factory(operation, phase, True)
    active = make_service(operation, pair, factory, gate)
    active.set_source(metadata(request.source))
    assert start(active, operation) and entered.wait(2)
    assert active.cancel()
    assert finish(active).error == "lifecycle_failed"
    assert children[-1].poll() is None and children[-1].actions == ["terminate", "kill"]
    assert gate.busy and gate.failure == operation + "_child_cleanup_failed"
    assert gate.admitted == 1 and gate.released == 0
    assert active._executor._processes._unreaped_process is children[-1]
    assert active._control.cleanup_failed
    owned_files = sorted(tmp_path.rglob("*"))
    if phase >= (2 if operation == "crop" else 0):
        assert list(tmp_path.glob(f".mediaforge-{operation}-*/partial.mp4"))
    rejected_calls = []
    def forbidden(command, **kwargs):
        rejected_calls.append(command)
        raise AssertionError("Poisoned gate admitted another child")
    other_operation = "trim" if operation == "crop" else "crop"
    other = make_service(other_operation, pair, forbidden, gate)
    other.set_source(metadata(request.source))
    assert not start(other, other_operation)
    assert other.snapshot().error == "media_processing_busy"
    assert "unavailable" in other.snapshot().message
    assert rejected_calls == [] and sorted(tmp_path.rglob("*")) == owned_files
    assert not start(active, operation)  # Retry cannot erase retained ownership.
    assert len(calls) == phase + 1
    active.set_source(None)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="termination failed"):
            active.shutdown()
    assert gate.busy and gate.released == 0 and children[-1].poll() is None


@pytest.mark.parametrize("operation", ["crop", "trim"])
def test_normal_shutdown_reaps_releases_exactly_once(tmp_path, operation):
    request, pair = setup(tmp_path)
    gate = CountingGate()
    phase = 2 if operation == "crop" else 0
    factory, calls, children, entered = controlled_factory(operation, phase, False)
    active = make_service(operation, pair, factory, gate)
    active.set_source(metadata(request.source))
    assert start(active, operation) and entered.wait(2)
    active.shutdown()
    active.shutdown()
    assert finish(active).state == "Cancelled"
    assert all(child.poll() is not None for child in children)
    assert gate.admitted == gate.released == 1 and not gate.busy and not gate.failure
    assert list(tmp_path.iterdir()) == [request.source]


def test_poison_is_explicit_sticky_and_cannot_be_released_as_clean():
    gate = CountingGate()
    owner = gate.acquire()
    gate.poison(owner, "crop_child_cleanup_failed")
    assert gate.busy and gate.failure == "crop_child_cleanup_failed"
    assert gate.acquire() is None
    with pytest.raises(RuntimeError, match="unresolved"):
        gate.release(owner)
    assert gate.busy and gate.released == 0
