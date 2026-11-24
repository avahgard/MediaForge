from dataclasses import replace
import threading

import pytest

from crop_support import CountingGate, Factory, finish, metadata, setup
from mediaforge.application.services.crop import CropService
from mediaforge.features.media.crop import CropError, CropErrorCode, CropRect, CropResult
from mediaforge.infrastructure.ffmpeg.crop import FfmpegCropExecutor


@pytest.mark.parametrize("values", [(-2, 0, 16, 16), (0, -2, 16, 16), (0, 0, 14, 16),
    (0, 0, 16, 14), (1, 0, 16, 16), (0, 1, 16, 16), (0, 0, 17, 16), (0, 0, 16, 17),
    (626, 0, 16, 16), (0, 346, 16, 16), (0.0, 0, 16, 16), (True, 0, 16, 16),
    ("0", 0, 16, 16), (None, 0, 16, 16), (float("nan"), 0, 16, 16), (0, 0, float("inf"), 16)])
def test_strict_geometry_invalid_zero_children(tmp_path, values):
    request, pair = setup(tmp_path)
    gate, factory = CountingGate(), Factory()
    service = CropService(FfmpegCropExecutor(pair, process_factory=factory), gate=gate)
    service.set_source(metadata(request.source))
    assert not service.start(*values)
    assert service.snapshot().error == "invalid_rectangle"
    assert factory.calls == [] and list(tmp_path.iterdir()) == [request.source]
    assert not gate.busy and gate.admitted == gate.released == 1


@pytest.mark.parametrize("rect", [CropRect(0, 0, 16, 16), CropRect(624, 344, 16, 16), CropRect(0, 0, 640, 360), CropRect(100, 40, 320, 240)])
def test_boundary_full_frame_and_minimum_are_encoded(tmp_path, rect):
    request, pair = setup(tmp_path)
    from crop_support import payload
    factory = Factory(output=payload(rect.width, rect.height))
    service = CropService(FfmpegCropExecutor(pair, process_factory=factory))
    service.set_source(metadata(request.source))
    assert service.start(rect.x, rect.y, rect.width, rect.height)
    job = finish(service)
    assert job.state == "Completed" and (job.width, job.height) == (rect.width, rect.height)
    assert len(factory.calls) == 4 and "libx264" in factory.calls[2][0]


def test_source_preconditions_and_no_open_time_preflight(tmp_path):
    request, pair = setup(tmp_path)
    factory, gate = Factory(), CountingGate()
    service = CropService(FfmpegCropExecutor(pair, process_factory=factory), gate=gate)
    assert not service.start(0, 0, 16, 16) and service.snapshot().error == "no_source"
    for source in (replace(metadata(request.source), width=0), replace(metadata(request.source), video_codec="")):
        assert service.set_source(source)
        assert not service.start(0, 0, 16, 16) and service.snapshot().error == "no_source"
    service.set_source(metadata(tmp_path / "missing.mp4"))
    assert not service.start(0, 0, 16, 16) and service.snapshot().error == "source_unavailable"
    service.set_source(metadata(request.source))
    assert factory.calls == [] and gate.admitted == gate.released == 4
    service.shutdown()
    assert not service.start(0, 0, 16, 16) and not service.set_source(None)


@pytest.mark.parametrize("code", list(CropErrorCode))
def test_stable_error_mapping_and_gate_release(tmp_path, code):
    request, _ = setup(tmp_path)
    class Failed:
        def execute(self, request, control):
            raise CropError(code, "RAW secret stack diagnostic")
    gate = CountingGate()
    service = CropService(Failed(), gate=gate)
    service.set_source(metadata(request.source))
    assert service.start(100, 40, 320, 240)
    job = finish(service)
    assert job.error == code.value and "RAW" not in job.message
    assert job.state == ("Cancelled" if code == CropErrorCode.CANCELLED else "Failed")
    assert not gate.busy and gate.admitted == gate.released == 1


def test_source_generation_and_retry(tmp_path):
    request, pair = setup(tmp_path)
    factory = Factory(block=0)
    gate = CountingGate()
    service = CropService(FfmpegCropExecutor(pair, process_factory=factory), gate=gate)
    service.set_source(metadata(request.source))
    assert service.start(100, 40, 320, 240) and factory.entered.wait(2)
    old = service.snapshot()
    assert not service.set_source(None) and not service.start(0, 0, 16, 16)
    assert service.snapshot() == old
    assert service.cancel() and not service.cancel()
    assert finish(service).state == "Cancelled"
    fresh = Factory()
    service._executor = FfmpegCropExecutor(pair, process_factory=fresh)
    assert service.start(100, 40, 320, 240)
    assert finish(service).state == "Completed"
    assert service.set_source(metadata(request.source, 800, 600))
    assert service.snapshot().state == "Pending" and not service.snapshot().result
    assert service.snapshot().job_id > old.job_id and gate.admitted == gate.released == 2


def test_worker_start_failure_releases_gate(tmp_path, monkeypatch):
    import mediaforge.application.services.crop as module
    request, pair = setup(tmp_path)
    gate, factory = CountingGate(), Factory()
    service = CropService(FfmpegCropExecutor(pair, process_factory=factory), gate=gate)
    service.set_source(metadata(request.source))
    def failed(*args):
        raise RuntimeError("cannot start thread")
    monkeypatch.setattr(module.Thread, "start", failed)
    assert not service.start(100, 40, 320, 240)
    assert service.snapshot().error == "start_failed"
    assert gate.admitted == gate.released == 1 and not gate.busy and factory.calls == []


@pytest.mark.integration
def test_qt_free_crop_boundary(run_python):
    result = run_python("import sys; import mediaforge.application.services.crop; assert 'PySide6' not in sys.modules")
    assert result.returncode == 0, result.stderr
