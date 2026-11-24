from contextlib import contextmanager
import threading
import time

import pytest

from crop_support import CountingGate, Factory, finish, metadata, payload, setup
from mediaforge.application.services.crop import CropService
from mediaforge.application.services.trim import TrimService
from mediaforge.features.media.crop import CropControl
from mediaforge.infrastructure.ffmpeg.crop import FfmpegCropExecutor
from mediaforge.infrastructure.ffmpeg.trim import FfmpegTrimExecutor
from mediaforge.infrastructure.filesystem import staged_output


def assert_cancelled(service, factory, gate, request, count):
    assert finish(service).state == "Cancelled"
    assert len(factory.calls) == count
    assert all(child.poll() is not None for child in factory.children)
    assert all(child.stdout.closed and child.stderr.closed for child in factory.children)
    assert list(request.source.parent.iterdir()) == [request.source]
    assert not gate.busy and gate.admitted == gate.released == 1


@pytest.mark.parametrize("phase", range(4), ids=["source", "capability", "encode", "output"])
def test_cancel_before_each_spawn_in_optimistic_check_gap(tmp_path, monkeypatch, phase):
    import mediaforge.application.services.crop as service_module
    request, pair = setup(tmp_path)
    factory, gate = Factory(), CountingGate()
    class GapControl(CropControl):
        def __init__(self):
            super().__init__()
            self.spawns = 0

        @contextmanager
        def starting(self):
            # The last optimistic check has returned. Accept cancellation at
            # exactly the old F1 gap, before taking the spawn admission lock.
            if self.spawns == phase:
                assert self.cancel()
            self.spawns += 1
            with super().starting():
                yield
    monkeypatch.setattr(service_module, "CropControl", GapControl)
    service = CropService(FfmpegCropExecutor(pair, process_factory=factory), gate=gate)
    service.set_source(metadata(request.source))
    assert service.start(100, 40, 320, 240)
    assert_cancelled(service, factory, gate, request, phase)


@pytest.mark.parametrize("phase", range(4), ids=["source", "capability", "encode", "output"])
@pytest.mark.parametrize("shutdown", [False, True], ids=["cancel", "shutdown"])
@pytest.mark.parametrize("force", [False, True], ids=["terminate", "kill"])
def test_during_each_phase_cancel_shutdown_reap_before_cleanup(tmp_path, phase, shutdown, force):
    request, pair = setup(tmp_path)
    factory, gate = Factory(block=phase, force=force), CountingGate()
    service = CropService(FfmpegCropExecutor(pair, process_factory=factory), gate=gate)
    service.set_source(metadata(request.source))
    assert service.start(100, 40, 320, 240) and factory.entered.wait(2)
    assert gate.busy and service.snapshot().state == "Running"
    started = time.monotonic()
    if shutdown:
        service.shutdown()
    else:
        assert service.cancel() and not service.cancel()
    assert_cancelled(service, factory, gate, request, phase + 1)
    assert time.monotonic() - started < 2
    child = factory.children[-1]
    assert child.actions == (["terminate", ("wait", 2.0), "kill", ("wait", 2.0)] if force else ["terminate", ("wait", 2.0)])


@pytest.mark.parametrize("phase", range(4))
def test_spawn_first_owns_child_before_cancel_acceptance(tmp_path, phase):
    request, pair = setup(tmp_path)
    factory, gate = Factory(block=phase), CountingGate()
    entered, attempted, accepted = threading.Event(), threading.Event(), threading.Event()
    def wrapped(command, **kwargs):
        if len(factory.calls) == phase:
            entered.set()
            assert attempted.wait(2)
            assert not accepted.is_set()
        return factory(command, **kwargs)
    service = CropService(FfmpegCropExecutor(pair, process_factory=wrapped), gate=gate)
    service.set_source(metadata(request.source))
    assert service.start(100, 40, 320, 240) and entered.wait(2)
    outcomes = []
    def cancel():
        attempted.set()
        outcomes.append(service.cancel())
        # A returned accepted Cancel must observe an already owned child.
        outcomes.append(len(factory.children) == phase + 1)
        accepted.set()
    canceller = threading.Thread(target=cancel)
    canceller.start()
    canceller.join(3)
    assert not canceller.is_alive() and outcomes == [True, True]
    assert_cancelled(service, factory, gate, request, phase + 1)


@pytest.mark.parametrize("phase", range(4))
def test_bounded_timeout_all_phases(tmp_path, phase):
    request, pair = setup(tmp_path)
    factory, gate = Factory(block=phase, force=True), CountingGate()
    service = CropService(FfmpegCropExecutor(pair, process_factory=factory, timeout_seconds=.02, probe_timeout_seconds=.02), gate=gate)
    service.set_source(metadata(request.source))
    assert service.start(100, 40, 320, 240)
    job = finish(service)
    assert job.error == ["preflight_failed", "encoder_unavailable", "timeout", "validation_failed"][phase]
    assert all(c.poll() is not None for c in factory.children)
    assert gate.admitted == gate.released == 1 and not gate.busy
    assert list(tmp_path.iterdir()) == [request.source]


@pytest.mark.parametrize("failure", ["success", "source", "capability", "encode", "validation", "publication", "runtime", "unexpected", "staging", *[f"spawn{i}" for i in range(4)]])
def test_gate_release_matrix(tmp_path, monkeypatch, failure):
    request, pair = setup(tmp_path)
    gate = CountingGate()
    factory = Factory(exit_failure={"source": 0, "capability": 1, "encode": 2, "validation": 3}.get(failure),
                      spawn_failure=int(failure[-1]) if failure.startswith("spawn") else None)
    def denied(*args, **kwargs):
        raise PermissionError("controlled failure")
    if failure == "publication":
        monkeypatch.setattr(staged_output.os, "rename", denied)
    if failure == "staging":
        monkeypatch.setattr(staged_output.tempfile, "mkdtemp", denied)
    executor = FfmpegCropExecutor(None if failure == "runtime" else pair, process_factory=factory)
    if failure == "unexpected":
        executor.execute = denied
    service = CropService(executor, gate=gate)
    service.set_source(metadata(request.source))
    assert service.start(100, 40, 320, 240)
    job = finish(service)
    assert job.state == ("Completed" if failure == "success" else "Failed")
    assert gate.admitted == gate.released == 1 and not gate.busy
    assert not list(tmp_path.glob(".mediaforge-*"))
    if failure != "success":
        assert list(tmp_path.iterdir()) == [request.source]


@pytest.mark.parametrize("first", ["trim", "crop"])
def test_shared_gate_busy_zero_factory_zero_staging_keeps_active_state(tmp_path, first):
    request, pair = setup(tmp_path)
    gate = CountingGate()
    crop_factory, trim_factory = Factory(block=0), Factory(block=0)
    crop = CropService(FfmpegCropExecutor(pair, process_factory=crop_factory), gate=gate)
    trim = TrimService(FfmpegTrimExecutor(pair, process_factory=trim_factory), gate=gate)
    for service in (crop, trim):
        service.set_source(metadata(request.source))
    active, rejected = (trim, crop) if first == "trim" else (crop, trim)
    active_factory, rejected_factory = (trim_factory, crop_factory) if first == "trim" else (crop_factory, trim_factory)
    assert (trim.start(0, 1) if first == "trim" else crop.start(100, 40, 320, 240))
    assert active_factory.entered.wait(2)
    before, files = active.snapshot(), list(tmp_path.rglob("*"))
    assert not (crop.start(100, 40, 320, 240) if first == "trim" else trim.start(0, 1))
    assert rejected.snapshot().error == "media_processing_busy"
    assert active.snapshot() == before and rejected_factory.calls == []
    assert list(tmp_path.rglob("*")) == files and gate.admitted == 1
    active.shutdown()
    assert finish(active).state == "Cancelled" and gate.admitted == gate.released == 1


def test_cancel_before_publication_and_after_commit(tmp_path, monkeypatch):
    from mediaforge.infrastructure.filesystem.crop_output import CropOutput
    request, pair = setup(tmp_path)
    original = CropOutput.publish
    def cancel_before(self, control):
        assert control.cancel()
        return original(self, control)
    monkeypatch.setattr(CropOutput, "publish", cancel_before)
    factory, gate = Factory(), CountingGate()
    service = CropService(FfmpegCropExecutor(pair, process_factory=factory), gate=gate)
    service.set_source(metadata(request.source))
    assert service.start(100, 40, 320, 240)
    assert_cancelled(service, factory, gate, request, 4)
    def cancel_after(self, control):
        result = original(self, control)
        assert not control.cancel()
        return result
    monkeypatch.setattr(CropOutput, "publish", cancel_after)
    service._executor = FfmpegCropExecutor(pair, process_factory=Factory())
    assert service.start(100, 40, 320, 240) and finish(service).state == "Completed"
    assert gate.admitted == gate.released == 2


@pytest.mark.parametrize("operation", ["trim", "crop"])
def test_cancel_wins_before_terminal_error_commit(tmp_path, operation):
    from mediaforge.features.media.crop import CropError, CropErrorCode
    from mediaforge.features.media.trim import TrimError, TrimErrorCode
    request, _ = setup(tmp_path)
    error_type, codes = (CropError, CropErrorCode) if operation == "crop" else (TrimError, TrimErrorCode)
    class Failed:
        def execute(self, request, control):
            raise error_type(codes.PROCESS_FAILED)
    gate = CountingGate()
    service = (CropService if operation == "crop" else TrimService)(Failed(), gate=gate)
    entered, release = threading.Event(), threading.Event()
    original = service._error_job
    def before_commit(code, generation):
        job = original(code, generation)
        if code == codes.PROCESS_FAILED:
            entered.set()
            assert release.wait(2)
        return job
    service._error_job = before_commit
    service.set_source(metadata(request.source))
    assert (service.start(100, 40, 320, 240) if operation == "crop" else service.start(0, 1))
    assert entered.wait(2)
    assert service.cancel()
    release.set()
    assert finish(service).state == "Cancelled" and not service.cancel()
    assert gate.admitted == gate.released == 1 and not gate.busy


def test_gate_held_through_staging_cleanup(tmp_path, monkeypatch):
    request, pair = setup(tmp_path)
    gate = CountingGate()
    factory = Factory(exit_failure=2)
    service = CropService(FfmpegCropExecutor(pair, process_factory=factory), gate=gate)
    entered, release = threading.Event(), threading.Event()
    original = staged_output.StagedOutput.__exit__
    def paused_cleanup(self, *args):
        entered.set()
        assert release.wait(2)
        return original(self, *args)
    monkeypatch.setattr(staged_output.StagedOutput, "__exit__", paused_cleanup)
    service.set_source(metadata(request.source))
    assert service.start(100, 40, 320, 240) and entered.wait(2)
    assert gate.busy and service.snapshot().state == "Running"
    rejected_factory = Factory()
    trim = TrimService(FfmpegTrimExecutor(pair, process_factory=rejected_factory), gate=gate)
    trim.set_source(metadata(request.source))
    assert not trim.start(0, 1) and trim.snapshot().error == "media_processing_busy"
    assert rejected_factory.calls == []
    release.set()
    assert finish(service).state == "Failed"
    assert gate.admitted == gate.released == 1 and not gate.busy
    assert list(tmp_path.iterdir()) == [request.source]


def test_simultaneous_trim_crop_starts_admit_exactly_one(tmp_path):
    request, pair = setup(tmp_path)
    gate = CountingGate()
    factories = [Factory(block=0), Factory(block=0)]
    trim = TrimService(FfmpegTrimExecutor(pair, process_factory=factories[0]), gate=gate)
    crop = CropService(FfmpegCropExecutor(pair, process_factory=factories[1]), gate=gate)
    for service in (trim, crop):
        service.set_source(metadata(request.source))
    barrier = threading.Barrier(2)
    results = []
    def start(action):
        barrier.wait(2)
        results.append(action())
    threads = [threading.Thread(target=start, args=(action,))
               for action in (lambda: trim.start(0, 1), lambda: crop.start(100, 40, 320, 240))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
    assert sorted(results) == [False, True]
    active_index = 0 if trim.busy else 1
    assert factories[active_index].entered.wait(2)
    assert len(factories[active_index].calls) == 1 and factories[1 - active_index].calls == []
    trim.shutdown()
    crop.shutdown()
    assert gate.admitted == gate.released == 1 and not gate.busy
