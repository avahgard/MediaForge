"""Owner correction: independent preview math, cache identity and logical input."""
import hashlib
import threading
import pytest
from PySide6.QtGui import QImage,QColor
from mediaforge.infrastructure.images.preview_channels import display_pixels
from mediaforge.infrastructure.images.resample import alpha_area
from mediaforge.infrastructure.images.reader import QtImageReader
from mediaforge.application.services.images import ImageSourceService
from test_image_preview import Resources


@pytest.mark.parametrize('alpha',[0,1,64,128,254,255])
def test_native_constant_alpha_and_hidden_rgb(alpha):
    image=QImage(32,16,QImage.Format_RGBA8888);image.fill(QColor(210,70,120,alpha))
    before=bytes(image.constBits())
    rgba=display_pixels(image,16,8);rgb=display_pixels(image,16,8,'RGB');a=display_pixels(image,16,8,'Alpha')
    assert rgba.pixelColor(4,4).alpha()==alpha
    assert rgb.pixelColor(4,4).getRgb()==(210,70,120,255)
    assert a.pixelColor(4,4).getRgb()==(alpha,alpha,alpha,255)
    assert bytes(image.constBits())==before


@pytest.mark.parametrize('size',[(16,8),(16,16),(8,4)])
def test_native_premultiplied_edge_against_exact_independent_area(size):
    image=QImage(32,16,QImage.Format_RGBA8888)
    for y in range(16):
        for x in range(32):image.setPixelColor(x,y,QColor(255,0,0,255) if x%2==0 else QColor(0,255,0,0))
    expected=alpha_area(image,*size,threading.Event())
    actual=display_pixels(image,*size)
    assert max(abs(a-b) for a,b in zip(bytes(actual.constBits()),bytes(expected.constBits())))<=1
    for y in range(size[1]):
        for x in range(size[0]):
            p=actual.pixelColor(x,y)
            assert p.red()==255 and p.green()==p.blue()==0 and abs(p.alpha()-128)<=1


def test_cache_reuse_revalidates_bytes_and_memory(tmp_path):
    path=tmp_path/'frame.png';im=QImage(1550,1400,QImage.Format_RGBA8888);im.fill(QColor(200,20,40,128));assert im.save(str(path))
    service=ImageSourceService(QtImageReader(),resources=Resources());before=hashlib.sha256(path.read_bytes()).hexdigest()
    first=service.prepare_preview(str(path),'1:2',threading.Event());source=first.metadata.source;first.close_source();first.release()
    service.channel='RGB';warm=service.prepare_preview(str(path),'1:2',threading.Event(),source)
    assert service.cache_hits==1 and warm.raster.display.pixelColor(2,2).getRgb()==(200,20,40,255)
    warm.release();assert service.cache_bytes<=service.cache_limit
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    path.write_bytes(b'changed')
    with pytest.raises(ValueError):service.prepare_preview(str(path),'1:2',threading.Event(),source)
    service.close_cache();assert service.budget.reserved==service.budget.overhead


def test_render_reservation_is_monotonic_and_separate_from_cpu():
    service=ImageSourceService(QtImageReader(),resources=Resources());cpu=service.budget.reserved
    service.render_surface(3840,2160);peak=service.render_reservation
    service.render_surface(800,560)
    assert service.render_reservation==peak and peak<384*1024**2
    assert service.budget.reserved==cpu
    with pytest.raises(ValueError):service.render_surface(16000,16000)


def host_ceiling(sample):
    # Independent reference for the approved current-host quarter policy.
    physical=sample['available_physical']-max(2*1024**3,sample['total_physical']*15//100)
    commit=sample['available_commit']-max(2*1024**3,sample['total_commit']//10)
    return max(0,min(physical,commit)//4)


def test_existing_video_private_commit_is_not_misclassified_as_image_cpu():
    service=ImageSourceService(QtImageReader(),resources=Resources());sample=service.resources.sample()
    ceiling=host_ceiling(sample);assert service.preview_capacity(sample)==ceiling
    assert ceiling>service.budget.limit
    sample['image_private_growth']=1400*1024**2
    service._check_sample(sample)  # Legitimate growth under the current contract.
    sample['image_private_growth']=ceiling+service.render_reservation
    with pytest.raises(ValueError):service._check_sample(sample,incoming_cpu=1)
    service.video_texture(3840,2160,12)
    charged=service.video_cpu_reservation
    assert charged==36*3840*2160*8
    sample['image_private_growth']=ceiling+service.render_reservation+charged
    service._check_sample(sample)
    with pytest.raises(ValueError):service._check_sample(sample,incoming_cpu=1)
    service.video_texture(1280,720,8)
    assert service.video_cpu_reservation==charged
    # CPU's separate renderer allowance does not waive the GPU pressure gate.
    sample['image_private_growth']=0;sample['dedicated']=service.render_reservation+1
    with pytest.raises(ValueError):service._check_sample(sample)


@pytest.mark.parametrize('field',['available_physical','available_commit'])
def test_lower_host_availability_reduces_real_admission(field):
    service=ImageSourceService(QtImageReader(),resources=Resources());sample=service.resources.sample()
    high=host_ceiling(sample);sample[field]=4*1024**3;low=host_ceiling(sample)
    assert service.preview_capacity(sample)==low<high
    sample['image_private_growth']=low+service.render_reservation
    service._check_sample(sample)
    with pytest.raises(ValueError):service._check_sample(sample,incoming_cpu=1)


def test_live_leases_and_future_processing_growth_are_not_free():
    from mediaforge.infrastructure.ffmpeg.image_export import _future_parent,host_job_capacity
    service=ImageSourceService(QtImageReader(),resources=Resources());sample=service.resources.sample()
    live=service.budget.reserve_peak(128*1024**2)
    try:
        service.preview_cpu_high_water=512*1024**2
        service.video_texture(1920,1080,12)
        future=512*1024**2+service.video_cpu_reservation+service.render_reservation
        assert service.budget.owns(live) and service.resident_reservation==512*1024**2
        assert _future_parent(service,sample)==future
        available=min(sample['available_physical']-max(2*1024**3,sample['total_physical']*15//100),sample['available_commit']-max(2*1024**3,sample['total_commit']//10))
        control=8*1024**2
        assert host_job_capacity(service,sample,control)==max(0,available-future-control)//4
        # Already resident commit must not be subtracted for a second time.
        sample['image_private_growth']=200*1024**2
        assert _future_parent(service,sample)==future-200*1024**2
        assert service.budget.owns(live)
    finally:service.budget.release(live)


def test_failed_preview_preadmission_never_allocates_decoder_or_lease(tmp_path,monkeypatch):
    path=tmp_path/'uncharged.png';im=QImage(32,32,QImage.Format_RGB32);im.fill(QColor('red'));assert im.save(str(path))
    service=ImageSourceService(QtImageReader(),resources=Resources());sample=service.resources.sample()
    sample['image_private_growth']=host_ceiling(sample)+service.render_reservation+1
    service.resources.sample=lambda:dict(sample)
    def forbidden(*args,**kwargs):raise AssertionError('Unadmitted allocation/child')
    monkeypatch.setattr(service.reader,'decode_preview',forbidden)
    monkeypatch.setattr(service.budget,'reserve_peak',forbidden)
    before=service.budget.reserved
    with pytest.raises(ValueError):service.prepare_preview(str(path),'1:1',threading.Event())
    assert service.budget.reserved==before and not service._cache


def test_cache_hash_refuses_same_size_restored_timestamp_mutation(tmp_path):
    import os
    path=tmp_path/'identity.png';image=QImage(32,32,QImage.Format_RGB32);image.fill(QColor('red'));assert image.save(str(path))
    service=ImageSourceService(QtImageReader(),resources=Resources())
    value=service.prepare_preview(str(path),'1:1',threading.Event());source=value.metadata.source;value.release()
    info=path.stat();data=bytearray(path.read_bytes());data[-12]^=1;path.write_bytes(data);os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns))
    assert path.stat().st_size==source.signature.size and path.stat().st_mtime_ns==source.signature.mtime_ns
    with pytest.raises(ValueError,match='changed'):service.prepare_preview(str(path),'1:1',threading.Event(),source)
    service.close_cache();assert service.budget.reserved==service.budget.overhead


def test_decoder_lost_straight_rgb_is_not_claimed_as_stored_channels():
    from types import SimpleNamespace
    image=QImage(2,2,QImage.Format_ARGB32_Premultiplied);image.fill(QColor(200,30,70,128))
    raster=SimpleNamespace(straight=image,display=image,plan=SimpleNamespace(width=2,height=2))
    with pytest.raises(ValueError,match='Stored RGB is unavailable'):
        QtImageReader().channel_preview(raster,'RGB',threading.Event())
