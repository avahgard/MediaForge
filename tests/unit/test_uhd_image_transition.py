"""Renderer refusal may retire a video once; allocations still need real admission."""
from types import SimpleNamespace
from threading import Event
import threading
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QTest
from mediaforge.application.services.images import ImageSourceService,ImageRendererPressure
from mediaforge.presentation.bridge.image_preview import ImagePreviewController
from tests.unit.test_image_preview import Resources,wait_until
from mediaforge.infrastructure.images.reader import QtImageReader


def picture():
    return SimpleNamespace(raster=SimpleNamespace(channel='RGBA',plan=SimpleNamespace(
        effective_quality='1:1',width=16,height=16)),release=lambda:None)


class Service:
    def __init__(self):self.resources=Resources();self.calls=[];self.sampled=Event();self.eligible=Event()
    def prepare_preview(self,path,quality,cancel,*_):
        self.calls.append(path)
        if len(self.calls)==1:raise ImageRendererPressure('GPU pressure')
        assert self.eligible.is_set(),'Unadmitted allocation'
        return picture()
    def check_pressure(self):
        self.sampled.set()
        if not self.eligible.is_set():raise ImageRendererPressure('GPU still resident')
        return {}


@pytest.fixture
def setup():
    app=QCoreApplication.instance() or QCoreApplication([])
    service=Service();published=[];retired=[]
    view=ImagePreviewController(service,lambda request,value:published.append(request.path));view._backend=view.qualified_backend
    view.retire_video=lambda request:retired.append(request.path) or True
    try:yield view,service,published,retired
    finally:view.shutdown()


def test_renderer_retirement_waits_for_real_sample_before_second_prepare(setup):
    view,s,published,retired=setup
    view.request('image');wait_until(lambda:bool(retired));assert s.sampled.wait(1)
    QTest.qWait(80);assert s.calls==['image'] and not published
    s.eligible.set();wait_until(lambda:not view.busy)
    assert retired==['image'] and s.calls==['image','image'] and published==['image']


def test_retirement_wait_rechecks_the_failed_texture_charge(setup):
    view, service, published, retired = setup
    refused = ImageRendererPressure('Texture overlap exceeds budget')
    refused.admission = (4096, 8192, 16384)
    original = service.prepare_preview

    def prepare(*args):
        if not service.calls:
            service.calls.append(args[0])
            raise refused
        return original(*args)

    def recheck(error):
        assert error is refused and error.admission == (4096, 8192, 16384)
        return service.check_pressure()

    service.prepare_preview = prepare
    service.recheck_renderer_admission = recheck
    view.request('image')
    wait_until(lambda: bool(retired))
    assert service.sampled.wait(1) and not published
    service.eligible.set()
    wait_until(lambda: not view.busy)
    assert published == ['image'] and service.calls == ['image', 'image']


def test_gpu_monitor_clears_transient_status_in_the_same_generation(setup):
    view, _, published, _ = setup
    serial = view.requestGeneration
    view._pressure_done((serial, ImageRendererPressure('Temporary GPU pressure')))
    assert view.statusKind == 'error' and view.requestGeneration == serial
    view._pressure_done((serial, None))
    assert view.status == '' and not published


def test_persistent_gpu_pressure_is_bounded_and_never_allocates(setup):
    view,s,published,retired=setup;view.retirement_wait_seconds=.02
    view.request('image');wait_until(lambda:not view.busy)
    assert 'after retirement' in view.status and retired==['image']
    assert s.calls==['image'] and not published


def test_host_failure_never_retires_video_or_retries(setup):
    view,s,published,retired=setup
    def low(*_):raise ValueError('Insufficient Windows RAM/commit headroom')
    s.prepare_preview=low;view.request('image');wait_until(lambda:not view.busy)
    assert 'RAM/commit' in view.status and not retired and not published


def test_low_headroom_after_retirement_refuses_before_prepare(setup):
    view,s,published,retired=setup
    def low():raise ValueError('Insufficient Windows RAM/commit headroom')
    s.check_pressure=low;view.request('image');wait_until(lambda:not view.busy)
    assert retired==['image'] and s.calls==['image'] and not published and 'RAM/commit' in view.status


def test_latest_intent_cancels_retirement_wait_without_stale_activation(setup):
    view,s,published,retired=setup
    view.retirement_pending=lambda:bool(retired)
    view.request('old');wait_until(lambda:bool(retired));assert s.sampled.wait(1)
    view.request('new');s.eligible.set();wait_until(lambda:not view.busy)
    assert retired==['old'] and published==['new'] and s.calls==['old','new']


def test_shutdown_cancels_wait_and_joins_worker(setup):
    view,s,published,retired=setup
    view.request('image');wait_until(lambda:bool(retired));assert s.sampled.wait(1)
    view.shutdown();assert view._pool._shutdown and s.calls==['image'] and not published


def test_no_retirement_for_image_to_image_or_stale_generation(setup):
    view,s,published,retired=setup;view.retire_video=lambda _:False
    view.request('image');wait_until(lambda:not view.busy)
    assert 'GPU pressure' in view.status and s.calls==['image'] and not published
    # A late GPU failure cannot retire any newer source.
    request=SimpleNamespace(serial=view._serial-1,cancel=Event(),reuse=None)
    view._done(request,None,ImageRendererPressure('late'))
    assert not retired


def test_real_pressure_gate_distinguishes_gpu_from_cpu_without_cap_change():
    s=ImageSourceService(QtImageReader(),resources=Resources());sample=s.resources.sample();limit=s.render_reservation
    sample['dedicated']=limit+1
    with pytest.raises(ImageRendererPressure):s._check_sample(sample)
    sample['dedicated']=limit;s._check_sample(sample)
    sample['available_physical']=1
    with pytest.raises(ValueError) as error:s._check_sample(sample)
    assert not isinstance(error.value,ImageRendererPressure) and s.render_reservation==limit


def test_upload_increment_renderer_failure_keeps_its_type_before_decode(tmp_path):
    from tests.unit.test_image_preview import write_image
    class Reader(QtImageReader):
        def decode_preview(self,*a,**kw):raise AssertionError('Decode before GPU admission')
    resources=Resources();service=ImageSourceService(Reader(),resources=resources)
    path=tmp_path/'valid.png';write_image(path);resources.gpu=service.render_reservation
    # Current residency fits; the required incoming upload does not. This must
    # remain a typed GPU refusal, not a generic source/CPU/invalid-tier failure.
    service.check_pressure()
    with pytest.raises(ImageRendererPressure):service.prepare_preview(str(path),'1:1',Event())
    assert service.budget.reserved==service.budget.overhead


def test_playback_retirement_destroys_backend_and_revokes_readiness(tmp_path):
    from tests.presentation.test_playback_controller import controller
    app=QCoreApplication.instance() or QCoreApplication([]);pc,old,_=controller()
    pc.set_source(str(tmp_path/'video.mov'));old=pc._player;signals=dict(pc._player_signal_handlers);generation=pc._source_generation
    pc.retire_for_image()
    assert old.retired and pc._player is None and pc._staging_sink is None and pc._source is None
    assert pc._source_generation>generation and not pc.framesReliable and not pc.canPlay and not pc.videoPictureAvailable
    signals['position'](999);assert pc.position==0
    pc.set_source(str(tmp_path/'video.mov'));assert pc._player is not old
    pc.shutdown()


def test_retirement_cleanup_failure_poison_keeps_owner_reachable():
    from mediaforge.presentation.bridge.media import MediaViewModel
    from mediaforge.application.contracts.image_source import VideoSource
    from mediaforge.application.services.processing_gate import MediaProcessingGate
    gate=MediaProcessingGate();item=SimpleNamespace(metadata=SimpleNamespace(source=VideoSource('v'),audio_only=False))
    def broken():raise RuntimeError('disposal failed')
    pc=SimpleNamespace(imageMode=False,_source='v',retire_for_image=broken)
    model=SimpleNamespace(_shutdown=False,canOpen=True,_playback=pc,_workspace=SimpleNamespace(active=item),_image_preview=SimpleNamespace(requestGeneration=3),_remember_video=lambda:None,_processing_gate=gate)
    with pytest.raises(RuntimeError,match='disposal'):
        MediaViewModel._retire_video_for_image(model,SimpleNamespace(serial=3))
    assert gate.busy and gate.failure and gate._external_resources==[pc]


def test_retirement_preserves_logical_frame_without_decoder_authority(tmp_path):
    from array import array
    from mediaforge.application.contracts.source_timeline import SourcePresentationTimeline,SourceSignature
    from tests.presentation.test_playback_controller import controller
    app=QCoreApplication.instance() or QCoreApplication([]);pc,_,_=controller()
    path=tmp_path/'source.mov';path.write_bytes(b'owned source')
    try:
        pc.set_source(str(path));pc._timeline=SourcePresentationTimeline.from_timestamps(array('q',[0,40000,80000]),40000)
        pc._timeline_signature=SourceSignature.capture(path);pc._timeline_offset=0;pc._origin_established=True;pc._duration=120;pc._position=40
        pc.retire_for_image()
        assert pc.position==40 and pc.duration==120 and pc.requestedFrameIndex==1 and pc.frameText=='Frame 2 / 3'
        assert not pc.timelineAvailable and not pc.framesReliable and not pc.videoPictureAvailable
        with pytest.raises(ValueError):pc.export_picture_snapshot()
        pc.set_source(str(path));assert pc._player is not None
    finally:pc.shutdown()


def test_reentrant_cancel_during_retirement_releases_old_pin(setup):
    view,s,published,retired=setup;released=[]
    def retire(request):view.cancel();return True
    view.retire_video=retire
    from mediaforge.presentation.bridge.image_preview import _Request
    request=_Request(view.requestGeneration,'old',None,None,'Auto',(1,1),Event(),0,None,reuse=SimpleNamespace(release=lambda:released.append(True)))
    view._done(request,None,ImageRendererPressure('pressure'))
    assert released==[True] and not published and not view.busy


def test_canceled_retired_transition_restores_only_after_worker_settles():
    from mediaforge.presentation.bridge.media import MediaViewModel
    calls=[];signal=SimpleNamespace(emit=lambda:None)
    preview=SimpleNamespace(busy=True,statusKind='idle')
    model=SimpleNamespace(_shutdown=False,_release_unused_manifests=lambda:None,_image_preview=preview,_metadata=object(),imageTransition=True,_pending_sequence=None,_thread=None,_restore_retired_video=lambda:calls.append(True),contentChanged=signal,_output=SimpleNamespace(changed=signal))
    MediaViewModel._image_status_changed(model);assert not calls
    preview.busy=False;preview.statusKind='progress';MediaViewModel._image_status_changed(model);assert not calls
    preview.statusKind='idle';MediaViewModel._image_status_changed(model);assert calls==[True]


def test_export_never_starts_during_retired_source_handoff():
    from PySide6.QtCore import QObject,Signal
    from mediaforge.presentation.bridge.export import ExportViewModel
    class Owner(QObject):
        workspaceChanged=Signal()
        state='loaded';canOpen=True;imageTransition=True;sourceKind='video';videoPreviewBusy=False
    app=QCoreApplication.instance() or QCoreApplication([]);owner=Owner();called=[]
    workspace=SimpleNamespace(active=SimpleNamespace(output=SimpleNamespace(kind='media')),output_summary=lambda snapshot:dict(valid=True))
    service=SimpleNamespace(busy=False,start_request=lambda _:called.append(True))
    view=ExportViewModel(service,workspace,None,owner)
    assert not view.canExport and not view.startExport() and not called
    owner.imageTransition=False;assert view.canExport


def test_restore_cannot_allocate_video_after_host_headroom_disappears():
    from mediaforge.presentation.bridge.media import MediaViewModel
    calls=[];item=object();signal=SimpleNamespace(emit=lambda:None);errors=[]
    def low():raise ValueError('Insufficient RAM/commit for video resume')
    model=SimpleNamespace(_shutdown=False,_retired_video_item=item,_retirement_restore_blocked=False,
        _workspace=SimpleNamespace(active=item),canOpen=False,canImport=True,
        _image_service=SimpleNamespace(admit_video_resume=low),
        _image_preview=SimpleNamespace(_set_status=lambda *v:errors.append(v)),_output=SimpleNamespace(changed=signal),
        _activate_item=lambda *a,**kw:calls.append(True),contentChanged=signal)
    assert MediaViewModel._restore_retired_video(model) is False
    assert not calls and model._retired_video_item is item and model._retirement_restore_blocked and errors
    # No repeated automatic attempt while the resource failure remains blocked.
    model._image_service.admit_video_resume=lambda:(_ for _ in ()).throw(AssertionError('Unbounded restore retry'))
    assert MediaViewModel._restore_retired_video(model) is False


def test_video_resume_charges_future_parent_without_double_charging_resident():
    s=ImageSourceService(QtImageReader(),resources=Resources());s.video_cpu_reservation=3*1024**3
    sample=s.resources.sample();sample['available_physical']=sample['available_commit']=4*1024**3
    s.resources.sample=lambda:dict(sample)
    with pytest.raises(ValueError,match='reload video'):s.admit_video_resume()
    # These allocations are already resident and reflected by Windows available
    # memory. Only their still-future growth may be charged on reload.
    sample['image_private_growth']=s.resident_reservation+s.video_cpu_reservation+s.render_reservation
    s.admit_video_resume()
    sample['dedicated']=s.render_reservation+1
    with pytest.raises(ImageRendererPressure):s.admit_video_resume()


def test_post_decode_gpu_refusal_cleans_traceback_before_one_readmission(setup):
    import weakref
    view,s,published,retired=setup;owners=[];original=s.prepare_preview
    class Allocation:pass
    def prepare(*args):
        if len(s.calls)==1:
            s.calls.append(args[0]);owner=Allocation();owners.append(weakref.ref(owner))
            raise ImageRendererPressure('Transient post-decode GPU pressure')
        if owners:assert owners[0]() is None,'Old decode backing survived re-admission'
        return original(*args)
    s.prepare_preview=prepare
    view.request('image');wait_until(lambda:bool(retired));s.eligible.set();wait_until(lambda:not view.busy)
    assert s.calls==['image','image','image'] and published==['image'] and retired==['image']


def test_post_retirement_prepare_attempt_count_is_finite(setup):
    view,s,published,retired=setup
    def fail(path,*a):s.calls.append(path);raise ImageRendererPressure('Persistent pressure after decode')
    s.prepare_preview=fail;view.request('image');wait_until(lambda:bool(retired));s.eligible.set();wait_until(lambda:not view.busy)
    assert s.calls==['image']*3 and not published and view.statusKind=='error'


def test_slow_eligibility_sample_cannot_allocate_after_wait_deadline(setup):
    import time
    view,s,published,retired=setup;view.retirement_wait_seconds=.01
    def delayed_sample():time.sleep(.03);return {}
    s.check_pressure=delayed_sample;view.request('image');wait_until(lambda:not view.busy)
    assert retired==['image'] and s.calls==['image'] and not published
