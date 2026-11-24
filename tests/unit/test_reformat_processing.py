from dataclasses import replace

import pytest

from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.application.services.resize import ResizeService
from mediaforge.features.media.reformat import dimensions, plan
from mediaforge.features.media.resize import ResizeControl, ResizeError, ResizeErrorCode
from mediaforge.infrastructure.ffmpeg.resize import FfmpegResizeExecutor
from tests.unit.resize_support import Factory, finish, metadata, payload, setup


@pytest.mark.parametrize("mode,size,filters", [
    ("fit", (320, 240), "format=yuv444p,scale=320:180:flags=lanczos,setsar=1/1,pad=320:240:0:30:color=black,format=yuv420p"),
    ("fill", (480, 360), "format=yuv444p,crop=480:360:80:0:exact=1,scale=480:360:flags=lanczos,crop=480:360:0:0:exact=1,setsar=1/1,format=yuv420p"),
    ("stretch", (320, 240), "scale=320:240:flags=lanczos,setsar=1/1"),
])
def test_new_geometry_uses_same_safe_runtime_and_validation(tmp_path, mode, size, filters):
    request, runtime = setup(tmp_path)
    factory = Factory(output=payload(*size))
    service = ResizeService(FfmpegResizeExecutor(runtime, process_factory=factory))
    assert service.set_source(metadata(request.source))
    assert service.start_reformat(*size, mode)
    assert finish(service).state == "Completed"
    args = factory.calls[2][0]
    assert args[args.index("-vf") + 1] == filters
    assert args[args.index("-fps_mode:v:0") + 1] == "passthrough"
    assert args[args.index("-c:a") + 1] == "copy"
    assert request.source.read_bytes() == b"SOURCE"
    service.shutdown()


@pytest.mark.parametrize("phase", range(4))
@pytest.mark.parametrize("mode", ["fit", "fill", "stretch"])
def test_cancel_new_recipe_each_phase_reaps_before_release(tmp_path, phase, mode):
    request, runtime = setup(tmp_path)
    # Same-aspect modes are useful controls for all existing child phases.
    factory = Factory(block=phase)
    gate = MediaProcessingGate()
    service = ResizeService(FfmpegResizeExecutor(runtime, process_factory=factory), gate=gate)
    service.set_source(metadata(request.source))
    assert service.start_reformat(320, 180, mode)
    assert factory.entered.wait(1)
    assert service.cancel()
    assert finish(service).state == "Cancelled"
    assert len(factory.calls) == phase + 1 and factory.children[phase].poll() is not None
    assert not gate.busy and not list(tmp_path.glob(".mediaforge-resize-*"))
    service.shutdown()


def test_odd_final_canvas_or_poisoned_request_never_spawns(tmp_path):
    request, runtime = setup(tmp_path)
    factory = Factory()
    gate = MediaProcessingGate()
    service = ResizeService(FfmpegResizeExecutor(runtime, process_factory=factory), gate=gate)
    service.set_source(metadata(request.source))
    assert not service.start_reformat(320, 241, "fit")
    assert service.snapshot().error == "invalid_dimensions" and factory.calls == []
    owner = gate.acquire(); gate.poison(owner, "test_poison")
    assert not service.start_reformat(320, 180, "stretch")
    assert service.snapshot().error == "media_processing_busy" and factory.calls == []


def test_odd_internal_offset_is_admitted_without_rounding_canvas(tmp_path):
    request, runtime = setup(tmp_path)
    factory = Factory(output=payload(160, 120))
    service = ResizeService(FfmpegResizeExecutor(runtime, process_factory=factory))
    service.set_source(metadata(request.source))
    assert service.start_reformat(160, 120, "fit")
    assert finish(service).state == "Completed"
    args = factory.calls[2][0]
    assert "pad=160:120:0:15:" in args[args.index("-vf") + 1]
    service.shutdown()


def test_sar_plan_is_authenticated_against_source_and_output(tmp_path):
    request, runtime = setup(tmp_path)
    source = replace(metadata(request.source, 192, 108), sample_aspect_ratio="4:3")
    factory = Factory(source=payload(192, 108, sar="4:3"), output=payload(512, 288))
    service = ResizeService(FfmpegResizeExecutor(runtime, process_factory=factory))
    service.set_source(source)
    assert service.start_reformat(512, 288, "fit")
    assert finish(service).state == "Completed"
    args = factory.calls[2][0]
    assert "scale=512:216:" in args[args.index("-vf") + 1]
    assert "pad=512:288:0:36:" in args[args.index("-vf") + 1]
    service.shutdown()
    factory = Factory(source=payload(192, 108, sar="1:1"))
    service = ResizeService(FfmpegResizeExecutor(runtime, process_factory=factory))
    service.set_source(source)
    assert service.start_reformat(512, 288, "fill")
    assert finish(service).error == "source_changed"
    assert len(factory.calls) == 1
    service.shutdown()


def test_request_cannot_inject_unrelated_geometry(tmp_path):
    request, runtime = setup(tmp_path)
    p = plan(640, 360, dimensions(320, 180), "fit")
    bad = replace(p, x=p.x + 2)
    with pytest.raises(ResizeError) as caught:
        FfmpegResizeExecutor(runtime).command(replace(request, reformat=bad), tmp_path / "staged.mp4")
    assert caught.value.code == ResizeErrorCode.INVALID_DIMENSIONS
