"""PAR01: one encoder contract before UI capture, worker allocation and children."""
from dataclasses import replace
from fractions import Fraction
import pytest
from mediaforge.features.media.export import ExportControl,ExportError,validate_video_encoder
from mediaforge.application.services.image_export import capture,describe
from tests.unit.test_image_export import make_sequence,executor
from tests.unit.test_export_plan import item_at
from mediaforge.application.services.export import capture as capture_video,summary


@pytest.mark.parametrize('container',['mp4','mov'])
@pytest.mark.parametrize('profile',['main','high'])
def test_sequence_lossless_profile_refused_before_request_and_execution(tmp_path,monkeypatch,container,profile):
    item=make_sequence(tmp_path);item.output.container=container;item.output.codec='h264';item.output.h264_quality=0;item.output.h264_profile=profile
    facts=describe(item);assert not facts['valid'] and 'Choose Auto or CRF 1–51' in facts['message']
    with pytest.raises(ExportError,match='lossless'):capture(item)
    assert item.output.h264_quality==0 and item.output.h264_profile==profile
    item.output.h264_profile='auto';frozen=capture(item);assert frozen.rate==Fraction(24000,1001)
    bad=replace(frozen,h264_profile=profile);worker=executor()
    def forbidden(*args,**kwargs):raise AssertionError('Invalid recipe reached allocation/staging/child')
    monkeypatch.setattr(worker._processes,'run',forbidden)
    monkeypatch.setattr(worker._image_service,'check_pressure',forbidden)
    import mediaforge.infrastructure.ffmpeg.preview_job as jobs
    monkeypatch.setattr(jobs.PreviewJobs,'__init__',forbidden)
    with pytest.raises(ExportError,match='lossless'):worker.execute(bad,ExportControl())
    assert not frozen.destination.exists() and not list(frozen.destination.parent.iterdir())


@pytest.mark.parametrize('profile',['main','high'])
def test_video_frozen_recipe_uses_same_prechild_validator(tmp_path,monkeypatch,profile):
    _,item=item_at(tmp_path);item.output.codec='h264';item.output.h264_quality=0;item.output.h264_profile=profile
    assert not summary(item)['valid']
    with pytest.raises(ExportError,match='lossless'):capture_video(item)
    item.output.h264_profile='auto';frozen=capture_video(item);worker=executor()
    def forbidden(*args,**kwargs):raise AssertionError('Invalid video recipe reached child')
    monkeypatch.setattr(worker._processes,'run',forbidden)
    with pytest.raises(ExportError,match='lossless'):worker.execute(replace(frozen,h264_profile=profile),ExportControl())
    assert not frozen.destination.exists()


@pytest.mark.parametrize('quality',[0,1,51])
@pytest.mark.parametrize('profile',['auto','main','high'])
@pytest.mark.parametrize('speed',['fast','balanced','quality'])
def test_encoder_boundaries_are_shared_and_do_not_rewrite(quality,profile,speed):
    if quality==0 and profile!='auto':
        with pytest.raises(ExportError,match='Main/High'):validate_video_encoder('h264',quality,speed,profile)
    else:assert validate_video_encoder('h264',quality,speed,profile) is None


@pytest.mark.parametrize('codec,quality,speed,profile',[
 ('h264',-1,'fast','auto'),('h264',52,'fast','auto'),('h264',True,'fast','auto'),
 ('prores',6,'fast','auto'),('prores',-1,'fast','auto'),('h264',1,'other','auto'),('h264',1,'fast','baseline')])
def test_invalid_setting_domain_refuses(codec,quality,speed,profile):
    with pytest.raises(ExportError):validate_video_encoder(codec,quality,speed,profile)
