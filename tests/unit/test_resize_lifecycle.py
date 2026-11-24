import threading

import pytest

from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.application.services.resize import ResizeService
from mediaforge.application.services.trim import TrimService
from mediaforge.application.services.crop import CropService
from mediaforge.features.media.resize import ResizeControl, ResizeError, ResizeErrorCode
from mediaforge.infrastructure.ffmpeg.resize import FfmpegResizeExecutor
from tests.unit.resize_support import Factory, finish, metadata, setup


@pytest.mark.parametrize("phase", range(4))
def test_cancel_during_every_child_phase_stops_later_spawns_and_cleans(tmp_path, phase):
    request, runtime = setup(tmp_path)
    factory = Factory(block=phase)
    service = ResizeService(FfmpegResizeExecutor(runtime, process_factory=factory, timeout_seconds=2,
                                                  probe_timeout_seconds=2))
    service.set_source(metadata(request.source)); assert service.start(320, 180)
    assert factory.entered.wait(1); assert service.cancel()
    job = finish(service)
    assert job.state == "Cancelled" and len(factory.calls) == phase + 1
    assert factory.children[phase].poll() is not None
    assert not list(tmp_path.glob(".mediaforge-resize-*"))


@pytest.mark.parametrize("phase", range(4))
def test_shutdown_during_every_child_phase_is_bounded(tmp_path, phase):
    request, runtime = setup(tmp_path)
    factory = Factory(block=phase)
    service = ResizeService(FfmpegResizeExecutor(runtime, process_factory=factory, timeout_seconds=2,
                                                  probe_timeout_seconds=2))
    service.set_source(metadata(request.source)); assert service.start(320, 180)
    assert factory.entered.wait(1); service.shutdown()
    assert service.snapshot().state == "Cancelled" and factory.children[phase].poll() is not None


def test_cancel_before_first_spawn_makes_zero_factory_calls(tmp_path):
    request, runtime = setup(tmp_path); factory = Factory(); control = ResizeControl()
    assert control.cancel()
    with pytest.raises(ResizeError) as caught:
        FfmpegResizeExecutor(runtime, process_factory=factory).execute(request, control)
    assert caught.value.code == ResizeErrorCode.CANCELLED and factory.calls == []


def test_unreaped_resize_child_poisons_gate_and_retains_staging(tmp_path):
    request, runtime = setup(tmp_path); gate = MediaProcessingGate(); factory = Factory(block=2, force=True)
    service = ResizeService(FfmpegResizeExecutor(runtime, process_factory=factory, timeout_seconds=2), gate=gate)
    service.set_source(metadata(request.source)); assert service.start(320, 180)
    assert factory.entered.wait(1)
    # Make both terminate and kill unable to prove reap.
    child = factory.children[2]
    child.kill = lambda: child.actions.append("kill")
    assert service.cancel()
    job = finish(service)
    assert job.error == "lifecycle_failed" and gate.busy
    assert gate.failure == "resize_child_cleanup_failed"
    assert factory.staged.exists()
    class Never:
        def __init__(self): self.calls = []
        def execute(self, *args): self.calls.append(args)
    for operation in (TrimService, CropService):
        executor = Never(); other = operation(executor, gate=gate); other.set_source(metadata(request.source))
        started = other.start(0, 1) if operation is TrimService else other.start(0, 0, 320, 180)
        assert not started and other.snapshot().error == "media_processing_busy" and not executor.calls
    with pytest.raises(RuntimeError): service.shutdown()
