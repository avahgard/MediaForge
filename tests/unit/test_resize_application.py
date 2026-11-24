import threading

import pytest

from mediaforge.application.services.crop import CropService
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.application.services.resize import ResizeService
from mediaforge.application.services.trim import TrimService
from mediaforge.features.media.resize import (
    MAX_RESIZE_PIXELS, ResizeDimensions, ResizeError, ResizeErrorCode, ResizeResult,
)
from mediaforge.features.media.crop import CropResult
from mediaforge.features.media.trim import TrimResult
from tests.unit.resize_support import finish, metadata, setup


class Executor:
    def __init__(self, *, block=False, error=None):
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = block
        self.error = error

    def execute(self, request, control):
        self.calls.append(request)
        self.entered.set()
        if self.block:
            while not self.release.wait(.002):
                control.check()
        if self.error:
            raise self.error
        output = request.source.with_name("result.mp4")
        return ResizeResult(output, request.dimensions.width, request.dimensions.height)


@pytest.mark.parametrize("width,height", [(16, 16), (7680, 4320), (4320, 7680), (320, 320), (1280, 720)])
def test_dimensions_accept_boundaries_aspect_change_and_upscale(width, height):
    ResizeDimensions(width, height).validate()


@pytest.mark.parametrize("width,height", [
    (15, 16), (16, 15), (7682, 16), (16, 7682), (17, 16), (16, 17),
    (7680, 7680), (True, 16), (16.0, 16),
])
def test_dimensions_reject_invalid_values(width, height):
    with pytest.raises(ResizeError) as caught:
        ResizeDimensions(width, height).validate()
    assert caught.value.code == ResizeErrorCode.INVALID_DIMENSIONS


def test_max_pixel_constant_is_exact():
    assert MAX_RESIZE_PIXELS == 33_177_600


def test_service_success_returns_exact_dimensions(tmp_path):
    request, _ = setup(tmp_path)
    service = ResizeService(Executor())
    assert service.set_source(metadata(request.source))
    assert service.start(320, 180)
    job = finish(service)
    assert (job.state, job.width, job.height) == ("Completed", 320, 180)


def test_service_requires_loaded_source_and_existing_file(tmp_path):
    service = ResizeService(Executor())
    assert not service.start(320, 180)
    assert service.snapshot().error == "no_source"
    missing = tmp_path / "missing.mp4"
    service.set_source(metadata(missing))
    assert not service.start(320, 180)
    assert service.snapshot().error == "source_unavailable"


def test_busy_rejection_has_zero_executor_calls_and_preserves_active(tmp_path):
    request, _ = setup(tmp_path)
    gate = MediaProcessingGate()
    active = Executor(block=True)
    first = ResizeService(active, gate=gate)
    rejected = ResizeService(Executor(), gate=gate)
    first.set_source(metadata(request.source)); rejected.set_source(metadata(request.source))
    assert first.start(320, 180) and active.entered.wait(1)
    before = first.snapshot()
    assert not rejected.start(320, 180)
    assert rejected.snapshot().error == "media_processing_busy"
    assert not rejected._executor.calls and first.snapshot() == before
    active.release.set(); finish(first)


@pytest.mark.parametrize("other", [TrimService, CropService])
def test_resize_active_rejects_trim_and_crop_without_calls(tmp_path, other):
    request, _ = setup(tmp_path)
    gate = MediaProcessingGate(); active = Executor(block=True); resize = ResizeService(active, gate=gate)
    class Other:
        calls = []
        def execute(self, *args): self.calls.append(args)
    executor = Other(); service = other(executor, gate=gate)
    resize.set_source(metadata(request.source)); service.set_source(metadata(request.source))
    assert resize.start(320, 180) and active.entered.wait(1)
    started = service.start(0, 1) if other is TrimService else service.start(0, 0, 320, 180)
    assert not started and service.snapshot().error == "media_processing_busy" and not executor.calls
    active.release.set(); finish(resize)


@pytest.mark.parametrize("other", [TrimService, CropService])
def test_trim_or_crop_active_rejects_resize_without_calls_or_state_change(tmp_path, other):
    request, _ = setup(tmp_path); gate = MediaProcessingGate()
    class Active:
        def __init__(self): self.entered = threading.Event(); self.release = threading.Event()
        def execute(self, operation, control):
            self.entered.set()
            while not self.release.wait(.002): control.check()
            output = operation.source.with_name("other.mp4")
            if other is TrimService: return TrimResult(output)
            return CropResult(output, operation.rectangle.width, operation.rectangle.height)
    active = Active(); current = other(active, gate=gate); rejected_executor = Executor()
    resize = ResizeService(rejected_executor, gate=gate)
    current.set_source(metadata(request.source)); resize.set_source(metadata(request.source))
    started = current.start(0, 1) if other is TrimService else current.start(0, 0, 320, 180)
    assert started and active.entered.wait(1); before = current.snapshot()
    assert not resize.start(320, 180) and resize.snapshot().error == "media_processing_busy"
    assert rejected_executor.calls == [] and current.snapshot() == before
    active.release.set(); finish(current)


def test_cancel_and_retry(tmp_path):
    request, _ = setup(tmp_path)
    executor = Executor(block=True); service = ResizeService(executor)
    service.set_source(metadata(request.source)); assert service.start(320, 180)
    assert executor.entered.wait(1) and service.cancel()
    assert finish(service).state == "Cancelled"
    executor.block = False; executor.release.set()
    assert service.start(320, 180) and finish(service).state == "Completed"


@pytest.mark.parametrize("code", list(ResizeErrorCode)[3:])
def test_failures_are_controlled_and_release_gate(tmp_path, code):
    if code == ResizeErrorCode.CANCELLED:
        return
    request, _ = setup(tmp_path); gate = MediaProcessingGate()
    service = ResizeService(Executor(error=ResizeError(code)), gate=gate)
    service.set_source(metadata(request.source)); assert service.start(320, 180)
    assert finish(service).state == "Failed"
    assert not gate.busy
