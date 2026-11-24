import hashlib
import threading
from types import SimpleNamespace
from pathlib import Path
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QImage,QColor
from PySide6.QtTest import QTest
from mediaforge.application.services.images import ImageSourceService
from mediaforge.infrastructure.images.reader import QtImageReader
from mediaforge.presentation.bridge.image_preview import ImagePreviewController


class Resources:
    def __init__(self): self.gpu = 0
    def sample(self):
        return dict(available_physical=8*1024**3,available_commit=8*1024**3,
                    total_physical=16*1024**3,total_commit=16*1024**3,
                    dedicated=self.gpu,shared=0,image_private_growth=0)
    def close(self): pass
    sample_host = sample


def write_image(path,size=(96,64),alpha=False):
    image=QImage(*size,QImage.Format_RGBA8888 if alpha else QImage.Format_RGB32)
    image.fill(QColor(255,0,0,128 if alpha else 255))
    assert image.save(str(path))


def test_owned_preview_reduction_preserves_source_and_metadata(tmp_path):
    path=tmp_path/'проба кадр.jpg';write_image(path)
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    service=ImageSourceService(QtImageReader(),resources=Resources())
    assert service.preview_plan(service.reader.probe(str(path)),'Auto',(1920,1080)).divisor==1
    value=service.prepare_preview(str(path),'1:2',threading.Event())
    assert (value.metadata.width,value.metadata.height)==(96,64)
    assert (value.raster.display.width(),value.raster.display.height())==(48,32)
    assert value.raster.display.format()==QImage.Format_ARGB32_Premultiplied
    assert not hasattr(value.raster,'rgba')
    assert service.budget.reserved==service.budget.overhead+2*48*32*4
    value.release()
    assert value.raster is None and service.budget.reserved==service.budget.overhead+service.cache_bytes
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


@pytest.mark.parametrize('suffix',['png','tiff','webp'])
def test_full_alpha_and_exact_reduced_alpha_preview(tmp_path,suffix):
    path=tmp_path/('alpha.'+suffix);write_image(path,alpha=True)
    service=ImageSourceService(QtImageReader(),resources=Resources())
    reduced=service.prepare_preview(str(path),'1:2',threading.Event())
    assert (reduced.raster.display.width(),reduced.raster.display.height())==(48,32)
    assert reduced.raster.display.pixelColor(10,10).alpha()==128
    reduced.release()
    value=service.prepare_preview(str(path),'Auto',threading.Event())
    assert value.raster.plan.divisor==1
    assert value.raster.display.pixelColor(10,10).alpha()==128
    value.release()


def test_pressure_and_unsafe_full_tier_refuse_before_decode(tmp_path):
    class Reader(QtImageReader):
        calls=0
        def decode_preview(self,*args): self.calls+=1; return super().decode_preview(*args)
    path=tmp_path/'image.jpg';write_image(path)
    reader=Reader();resources=Resources();service=ImageSourceService(reader,resources=resources)
    resources.gpu=service.render_reservation
    with pytest.raises(ValueError,match='pressure'):
        service.prepare_preview(str(path),'1:1',threading.Event())
    assert reader.calls==0 and service.budget.reserved==service.budget.overhead
    source=reader.probe(str(path))
    from dataclasses import replace
    large=replace(source,facts=replace(source.facts,width=7680,height=4320))
    with pytest.raises(ValueError,match='headroom'):
        service.preview_plan(large,'1:1')
    quarter=service.preview_plan(large,'1:4')
    assert (quarter.width,quarter.height)==(1920,1080)


def wait_until(predicate):
    for _ in range(200):
        if predicate(): return
        QTest.qWait(10)
    assert predicate(), 'Bounded image worker did not settle'


def test_region_keeps_full_source_and_pinned_cpu_file_ownership(tmp_path):
    path=tmp_path/'wide.png'
    image=QImage(96,64,QImage.Format_RGB32)
    for y in range(64):
        for x in range(96):image.setPixelColor(x,y,QColor(x*2,y*3,19))
    assert image.save(str(path))
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    service=ImageSourceService(QtImageReader(),resources=Resources());service.texture_limit=16
    first=service.prepare_preview(str(path),'1:1',threading.Event(),viewport=(16,12),view=(10,12,16,12,16,12))
    assert first.raster.plan.full_source and first.raster.straight.size()==QImage(96,64,QImage.Format_RGB32).size()
    assert first.raster.display.pixelColor(3,4)==QColor(26,48,19)
    original_key=first.raster.straight.cacheKey();source=first.raster.source
    pin=first.borrow();lease=first.lease;reserved=service.budget.reserved
    first.release();first.release()
    assert service.budget.reserved==reserved and lease in service.budget._leases
    with pytest.raises(OSError):path.open('wb')
    second=service.prepare_preview(str(path),'1:1',threading.Event(),source,(16,12),
        view=(30,20,16,12,16,12),reuse=pin)
    pin.release();assert lease not in service.budget._leases
    assert second.raster.straight.cacheKey()==original_key
    assert second.raster.display.pixelColor(3,4)==QColor(66,72,19)
    with pytest.raises(OSError):path.open('wb')
    second.release();service.close_cache()
    assert service.budget.reserved==service.budget.overhead and not service.budget._refs
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    with path.open('ab'):pass


def test_large_source_uses_host_admission_not_a_dimension_cutoff(tmp_path):
    from dataclasses import replace
    path=tmp_path/'source.png';write_image(path)
    service=ImageSourceService(QtImageReader(),resources=Resources());service.texture_limit=16384
    source=service.reader.probe(str(path))
    huge=replace(source,facts=replace(source.facts,width=20003,height=137))
    plan=service.preview_plan(huge,'1:1',(1200,700),Resources().sample())
    assert plan.full_source and plan.region==(0,0,20003,137)
    assert plan.width<=1200 and plan.height<=700
    big=replace(source,facts=replace(source.facts,width=16384,height=9216))
    pressure=Resources().sample()
    with pytest.raises(ValueError,match='headroom'):service.preview_plan(big,'1:1',(1200,700),pressure)
    pressure.update(available_physical=48*1024**3,available_commit=48*1024**3,
        total_physical=64*1024**3,total_commit=68*1024**3)
    plan=service.preview_plan(big,'1:1',(1200,700),pressure)
    assert plan.full_source and plan.region==(0,0,16384,9216)
    assert plan.width*plan.height*4<=32*1024**2
    # The shared default processing/control cap was not silently raised.
    assert service.budget.limit==256*1024**2


def test_canceled_pending_region_drops_only_its_pin(tmp_path):
    import weakref
    app=QCoreApplication.instance() or QCoreApplication([])
    path=tmp_path/'region.png';write_image(path)
    service=ImageSourceService(QtImageReader(),resources=Resources());service.texture_limit=16
    first=service.prepare_preview(str(path),'1:1',threading.Event(),viewport=(16,12),view=(0,0,16,12,16,12))
    source=first.raster.source;published=[];entered=threading.Event();release=threading.Event()
    original=service.prepare_preview
    def held(*args,**kwargs):
        entered.set();assert release.wait(3)
        return original(*args,**kwargs)
    service.prepare_preview=held
    preview=ImagePreviewController(service,lambda *_:published.append(True));preview._backend=preview.qualified_backend
    preview.live_prepared=weakref.ref(first);preview._view=('item',(0,0,16,12,16,12))
    try:
        preview.request(str(path),source,'item','1:1');assert entered.wait(1)
        preview.request(str(path),source,'item','1:1')
        assert service.budget._refs[first.lease]==3
        preview.cancel();assert service.budget._refs[first.lease]==2
        first.release();assert first.lease in service.budget._leases
        with pytest.raises(OSError):path.open('wb')
        release.set();wait_until(lambda:not preview.busy)
        assert not published and first.lease not in service.budget._leases
        assert preview.statusKind=='idle'
        with path.open('ab'):pass
    finally:
        release.set();preview.shutdown();first.release()
    assert service.budget.reserved==service.budget.overhead


def test_uhd_full_preview_requires_native_limit_and_keeps_cpu_cap(tmp_path):
    path=tmp_path/'uhd.png'
    image=QImage(3840,2160,QImage.Format_RGB32)
    image.fill(QColor(19,83,221))
    image.setPixelColor(3839,2159,QColor(217,39,11))
    assert image.save(str(path))
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    service=ImageSourceService(QtImageReader(),resources=Resources())
    source=service.reader.probe(str(path))
    region_plan=service.preview_plan(source,'1:1')
    assert region_plan.full_source and region_plan.region==(0,0,3840,2160)
    assert region_plan.width<=2048 and region_plan.height<=2048
    service.texture_limit=16384
    value=service.prepare_preview(str(path),'1:1',threading.Event())
    try:
        assert (value.raster.display.width(),value.raster.display.height())==(3840,2160)
        assert value.raster.display.pixelColor(0,0)==QColor(19,83,221)
        assert value.raster.display.pixelColor(3839,2159)==QColor(217,39,11)
        assert service.budget.peak<=256*1024**2
        # Preview upload increases the existing calculated pool before allocation;
        # it never debits old generations on timer/frame callbacks.
        high=service.render_reservation
        service._preview_pixels=max(service._preview_pixels,3840*2160)
        service.render_surface(3840,2160)
        assert service.render_reservation>=high
        service.render_surface(800,560)
        assert service.render_reservation>=high
    finally:
        value.release();service.close_cache()
    assert service.budget.reserved==service.budget.overhead
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


def test_worker_coalesces_latest_and_rejects_canceled_source():
    app=QCoreApplication.instance() or QCoreApplication([])
    started=threading.Event();proceed=threading.Event();released=[];activated=[];threads=[]
    class Service:
        resources=Resources()
        def prepare_preview(self,path,quality,cancel,*_):
            threads.append(threading.get_ident())
            if path=='a': started.set(); assert proceed.wait(2)
            return SimpleNamespace(raster=SimpleNamespace(channel='RGBA',plan=SimpleNamespace(effective_quality='1:1',width=16,height=16)),
                                   release=lambda:released.append(path))
        def check_pressure(self): return {}
    preview=ImagePreviewController(Service(),lambda request,value:activated.append(request.path))
    preview._backend=preview.qualified_backend
    try:
        assert preview.request('a')
        assert started.wait(1)
        preview.request('b');preview.request('c')
        assert preview._active.path=='a' and preview._pending.path=='c'
        proceed.set();wait_until(lambda:not preview.busy)
        assert activated==['c'] and released==['a']
        assert all(t!=threading.get_ident() for t in threads)
        assert len(threads)==2
    finally: proceed.set();preview.shutdown()


def test_activation_failure_is_reported_and_prepared_lease_released():
    app=QCoreApplication.instance() or QCoreApplication([])
    released=[]
    class Service:
        resources=Resources()
        def prepare_preview(self,*_): return SimpleNamespace(release=lambda:released.append(True))
        def check_pressure(self): return {}
    def refuse(*_): raise ValueError('Source generation no longer exists')
    preview=ImagePreviewController(Service(),refuse);preview._backend=preview.qualified_backend
    try:
        preview.request('a');wait_until(lambda:not preview.busy)
        assert 'generation' in preview.status and released==[True]
    finally: preview.shutdown()


@pytest.mark.parametrize('orientation',range(1,9))
def test_owned_preview_exif_orientation_once(tmp_path,orientation):
    import struct
    path=tmp_path/'oriented.jpg'
    image=QImage(17,11,QImage.Format_RGB32)
    for y in range(11):
        for x in range(17):image.setPixelColor(x,y,QColor(x*12,y*20,0))
    assert image.save(str(path));coded=QImage(str(path))
    payload=b'Exif\0\0'+b'II'+struct.pack('<HIH',42,8,1)+struct.pack('<HHI',274,3,1)+struct.pack('<H',orientation)+b'\0'*6
    data=path.read_bytes();path.write_bytes(data[:2]+b'\xff\xe1'+struct.pack('>H',len(payload)+2)+payload+data[2:])
    service=ImageSourceService(QtImageReader(),resources=Resources())
    value=service.prepare_preview(str(path),'1:1',threading.Event())
    result=value.raster.display
    assert (result.width(),result.height())==((11,17) if orientation>=5 else (17,11))
    def original(x,y):
        return {1:(x,y),2:(16-x,y),3:(16-x,10-y),4:(x,10-y),5:(y,x),6:(y,10-x),7:(16-y,10-x),8:(16-y,x)}[orientation]
    for x,y in ((0,0),(result.width()-1,result.height()-1),(3,5)):
        assert result.pixelColor(x,y)==coded.pixelColor(*original(x,y))
    value.release()


def test_progressive_jpeg_does_not_borrow_sequential_early_decode_budget(tmp_path):
    from dataclasses import replace
    path=tmp_path/'small.jpg';write_image(path)
    service=ImageSourceService(QtImageReader(),resources=Resources())
    source=service.reader.probe(str(path))
    assert source.facts.variant=='sequential'
    progressive=replace(source,facts=replace(source.facts,width=7680,height=4320,variant='progressive'))
    with pytest.raises(ValueError,match='headroom'):
        service.preview_plan(progressive,'1:4')


def test_first_image_uses_attached_window_and_swaps_do_not_debit_pool(tmp_path):
    from PySide6.QtCore import QObject,Signal
    from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput,QVideoSink
    from mediaforge.presentation.bridge.playback import PlaybackController
    app=QCoreApplication.instance() or QCoreApplication([])
    class Window(QObject):
        frameSwapped=Signal()
        def update(self): pass
    window=Window();controller=PlaybackController(QMediaPlayer(),QAudioOutput())
    controller.attachImageWindow(window)
    sink=QVideoSink();controller._video_sink=sink;controller._presentation_sink=sink
    path=tmp_path/'first.png';write_image(path)
    service=ImageSourceService(QtImageReader(),resources=Resources())
    prepared=service.prepare_preview(str(path),'1:1',threading.Event())
    try:
        controller.set_image(prepared)
        assert controller._image_receiver.window is window
        assert controller._player is None and controller._staging_sink is None
        retained=service.budget.reserved
        for _ in range(2):window.frameSwapped.emit();app.processEvents()
        assert service.budget.reserved==retained
        assert controller.state=='Paused' and sink.videoFrame().isValid()
        candidate=service.prepare_preview(str(path),'1:2',threading.Event())
        controller.update_image_preview(candidate,controller.prepare_image(candidate))
        assert controller._image_receiver is not None
        controller.clear_source()
        assert controller._image_receiver is None and not controller.imageMode
    finally: controller.shutdown()
    service.close_cache()
    assert service.budget.reserved==service.budget.overhead


def test_old_pressure_observation_cannot_poison_new_source():
    app=QCoreApplication.instance() or QCoreApplication([])
    class Service:
        resources=Resources()
    preview=ImagePreviewController(Service(),lambda *_:None)
    try:
        preview._serial=3
        preview._pressure_done((2,'pressure from retired video'))
        assert preview._pressure_fault is None and preview.status==''
    finally: preview.shutdown()


def test_prepared_image_cannot_activate_after_other_media_work_becomes_busy():
    from mediaforge.presentation.bridge.media import MediaViewModel
    from mediaforge.application.contracts.media import MediaMetadataDto
    app=QCoreApplication.instance() or QCoreApplication([])
    model=MediaViewModel(SimpleNamespace())
    model._workspace.add(MediaMetadataDto('old','C:/old.mp4','mp4',1.,'h264',96,64,24.))
    before=model.activeMediaId
    model._thread=object()  # Another admitted metadata operation now owns selection.
    try:
        with pytest.raises(ValueError,match='busy'):
            model._image_prepared(SimpleNamespace(item_id=None),SimpleNamespace(raster=SimpleNamespace(plan=SimpleNamespace(full_source=True))))
        assert model.activeMediaId==before
    finally:
        model._thread=None
        model.shutdown()


def test_window_resize_does_not_invalidate_source_picture():
    app=QCoreApplication.instance() or QCoreApplication([])
    started=threading.Event();release=threading.Event();released=[];published=[];size=[500]
    class Service:
        resources=Resources()
        def prepare_preview(self,*_):
            started.set();assert release.wait(2)
            return SimpleNamespace(raster=SimpleNamespace(channel='RGBA',plan=SimpleNamespace(effective_quality="1:1",width=16,height=16)),release=lambda:released.append(True))
    preview=ImagePreviewController(Service(),lambda *_:published.append(True))
    preview._backend=preview.qualified_backend
    preview._window=SimpleNamespace(width=lambda:size[0],height=lambda:500,devicePixelRatio=lambda:1)
    try:
        preview.request('a');assert started.wait(1);size[0]=600;release.set()
        wait_until(lambda:not preview.busy)
        assert published==[True] and released==[] and preview.status==''
    finally:release.set();preview.shutdown()


def test_preview_status_has_semantic_kind_and_canceled_completion_stays_silent():
    app=QCoreApplication.instance() or QCoreApplication([])
    entered=threading.Event();release=threading.Event();published=[];observed=[]
    class Service:
        resources=Resources()
        def prepare_preview(self,path,*_):
            if path=='old':entered.set();assert release.wait(2)
            # Even progress-looking error text must remain an error.
            raise ValueError('Preparing failed: invalid source')
    preview=ImagePreviewController(Service(),lambda *_:published.append(True))
    preview._backend=preview.qualified_backend
    preview.changed.connect(lambda:observed.append((preview.requestGeneration,preview.statusKind,preview.status)))
    try:
        preview.request('old');assert entered.wait(1)
        generation=preview.requestGeneration
        assert preview.statusKind=='progress' and preview.busy
        preview.cancel();assert preview.requestGeneration>generation
        assert preview.statusKind=='idle' and preview.status=='' and observed[-1][1]=='idle'
        release.set();wait_until(lambda:not preview.busy)
        assert preview.statusKind=='idle' and preview.status=='' and not published
        preview.request('bad');wait_until(lambda:not preview.busy)
        assert preview.statusKind=='error' and preview.status.startswith('Preparing failed')
        assert preview.notice=='Preview unavailable · click for details'
        preview.cancel();assert preview.statusKind=='idle' and preview.status==''
    finally:release.set();preview.shutdown()
    assert preview.statusKind=='idle'


def test_renderer_error_remains_an_error_with_a_canceled_worker_in_flight():
    app=QCoreApplication.instance() or QCoreApplication([])
    entered=threading.Event();release=threading.Event()
    class Service:
        resources=Resources()
        def prepare_preview(self,*_):
            entered.set();assert release.wait(2)
            raise ValueError('late worker failure')
    preview=ImagePreviewController(Service(),lambda *_:None);preview._backend=preview.qualified_backend
    try:
        preview.request('old');assert entered.wait(1)
        preview._renderer_error(None,'device unavailable')
        assert preview.busy and preview.statusKind=='error'
        assert preview.notice=='Preview unavailable · click for details'
        release.set();wait_until(lambda:not preview.busy)
        assert preview.statusKind=='error' and 'device unavailable' in preview.status
    finally:release.set();preview.shutdown()


def test_sequence_manifest_owner_survives_request_status_handoff():
    from mediaforge.presentation.bridge.media import MediaViewModel
    app=QCoreApplication.instance() or QCoreApplication([])
    entered=threading.Event();release=threading.Event();released=[]
    class Service:
        resources=Resources()
        def prepare_sequence_preview(self,*_):
            entered.set();assert release.wait(2)
            raise ValueError('controlled terminal worker')
    model=MediaViewModel(SimpleNamespace(),image_service=Service())
    preview=model.imagePreview;preview._backend=preview.qualified_backend
    manifest=SimpleNamespace(identity='incoming',count=1,members=(SimpleNamespace(path='frame1001.png'),))
    owner=SimpleNamespace(manifest=manifest,release=lambda:released.append(True))
    model._pending_sequence=owner
    model._playback=SimpleNamespace(sequence=SimpleNamespace(item=None))
    try:
        assert model.confirmSequence('24') and entered.wait(1)
        # MediaViewModel observes changed and releases unused manifests. It must
        # see the newly owned request, never an intermediate empty cancellation.
        assert model._sequence_leases.get('incoming') is owner and not released
    finally:release.set();model._playback=None;model.shutdown()


def test_region_reuse_still_revalidates_complete_source_and_metadata(tmp_path,monkeypatch):
    from mediaforge.infrastructure.images import reader as module
    from PySide6.QtGui import QImage,QColor
    from threading import Event
    image=QImage(80,60,QImage.Format_RGB32);image.fill(QColor(40,80,120))
    path=tmp_path/'original.png';assert image.save(str(path))
    service=ImageSourceService(module.QtImageReader(),resources=Resources())
    service.texture_limit=32
    source=service.reader.probe(str(path))
    first=service.prepare_preview(str(path),'1:1',Event(),source,(20,20))
    pin=first.borrow();calls=[]
    original_signature=module.signature;original_metadata=module.metadata
    def signature(*args):
        calls.append('hash');return original_signature(*args)
    def metadata(*args):
        calls.append('metadata');return original_metadata(*args)
    monkeypatch.setattr(module,'signature',signature);monkeypatch.setattr(module,'metadata',metadata)
    second=None
    try:
        second=service.prepare_preview(str(path),'1:1',Event(),source,(20,20),view=(10,10,20,20,20,20),reuse=pin)
        assert calls==['hash','metadata']
        assert second.raster.source==source and second.raster.plan.region==(10,10,20,20)
        assert second.raster.display.pixelColor(2,2).getRgb()==(40,80,120,255)
    finally:
        if second is not None:second.release()
        pin.release();first.release();service.close_cache()
    assert service.budget.reserved==service.budget.overhead


def test_observed_cpu_growth_uses_dynamic_host_ceiling_not_old_preview_baseline():
    service=ImageSourceService(QtImageReader(),resources=Resources())
    sample=Resources().sample()
    sample['image_private_growth']=800*1024**2
    service._check_sample(sample)
    # No resource check is removed: exceeding measured headroom still refuses.
    sample['image_private_growth']=service.preview_capacity(sample)+service.video_cpu_reservation+service.render_reservation+1
    with pytest.raises(ValueError,match='pressure'):service._check_sample(sample)
    sample['image_private_growth']=0;sample['dedicated']=service.render_reservation+1
    with pytest.raises(ValueError,match='pressure'):service._check_sample(sample)


def test_old_viewport_intent_cannot_cancel_new_import_or_tray_selection(tmp_path):
    from mediaforge.presentation.bridge.media import MediaViewModel
    from mediaforge.application.contracts.media import MediaMetadataDto
    app=QCoreApplication.instance() or QCoreApplication([])
    path=tmp_path/'old.png';write_image(path)
    source=QtImageReader().probe(str(path))
    model=MediaViewModel(SimpleNamespace())
    item,_=model._workspace.add(MediaMetadataDto('old',str(path),'png',None,'',96,64,None,source=source))
    model._playback=SimpleNamespace(imageMode=True,_image_prepared=SimpleNamespace(raster=SimpleNamespace(plan=SimpleNamespace(full_source=True))))
    called=[]
    for candidate in [None,'different-tray-id']:
        model._image_preview=SimpleNamespace(_pending=None,_active=SimpleNamespace(discover=False,exported=None,item_id=candidate),request=lambda *a:called.append(a))
        model._refresh_image_region()
        assert not called
    model._image_preview=SimpleNamespace(_pending=None,_active=SimpleNamespace(discover=False,exported=None,item_id=item.id),request=lambda *a:called.append(a))
    model._refresh_image_region();assert len(called)==1 and called[0][2]==item.id
    model._playback=None;model._image_preview=None;model.shutdown()
