"""Long H.264 sequences must charge the preset's live encoder queue."""
from dataclasses import replace
import pytest
from tests.unit.test_image_export import make_sequence,executor
from mediaforge.application.services.image_export import capture
from mediaforge.infrastructure.ffmpeg.image_export import processing_peak,job_requirement,execute
from mediaforge.features.media.export import ExportControl,ExportError


@pytest.mark.parametrize('speed,lookahead,refs',[('fast',10,1),('balanced',40,3),('quality',50,5)])
def test_live_encoder_frames_are_bounded_and_charged_before_spawn(tmp_path,speed,lookahead,refs,monkeypatch):
    item=make_sequence(tmp_path);item.output.container='mp4';item.output.codec='h264';request=replace(capture(item),width=3840,height=2160,source_width=3840,source_height=2160,end=100,speed=speed)
    aligned=3840*2176;queued=lookahead+refs+3+4
    encoded=max(m.signature.size for m in request.source_identity.manifest.members)
    wanted=64*1024**2+((32*3840*2160+4*aligned*queued+encoded)*5+3)//4+100*1024
    assert processing_peak(request)==wanted and job_requirement(request)>=wanted>512*1024**2
    longer=replace(request,end=1000)
    assert processing_peak(longer)-processing_peak(request)==900*1024  # Control entries only; no eager 1000 rasters.
    worker=executor();sample=worker._image_service.resources.sample
    def low():
        value=sample();value['available_physical']=4*1024**3;value['available_commit']=4*1024**3;return value
    monkeypatch.setattr(worker._image_service.resources,'sample',low)
    def forbidden(*a,**kw):raise AssertionError('Child/allocation after refused admission')
    import mediaforge.infrastructure.ffmpeg.preview_job as jobs
    monkeypatch.setattr(jobs.PreviewJobs,'__call__',forbidden)
    with pytest.raises((ExportError,ValueError),match='headroom'):execute(worker,request,ExportControl())
    assert not request.destination.exists()
