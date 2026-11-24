"""OC-R01: the actual native decode route must match preallocation admission."""
from dataclasses import replace
from threading import Event
from types import SimpleNamespace
import hashlib
import pytest
from PySide6.QtGui import QImage,QColor,QImageReader
from mediaforge.application.services.images import ImageSourceService
from mediaforge.application.contracts.image_source import preview_reuse_eligible
from mediaforge.application.services.exported_result import GuardedSource
from mediaforge.infrastructure.images.reader import QtImageReader

pytestmark = pytest.mark.integration


class Resources:
    def __init__(self,capacity=2*1024**3):self.capacity=capacity
    def sample(self):
        physical=64*1024**3;commit=68*1024**3
        return dict(total_physical=physical,total_commit=commit,
            available_physical=int(physical*.15)+4*self.capacity,
            available_commit=int(commit*.10)+4*self.capacity,
            dedicated=0,shared=0,image_private_growth=0)

    sample_host = sample


@pytest.fixture
def staged(tmp_path):
    path=tmp_path/'original4500.png'
    image=QImage(4500,2000,QImage.Format_RGB32);image.fill(QColor(41,83,127))
    assert image.save(str(path));image=None
    service=ImageSourceService(QtImageReader(),resources=Resources());service.texture_limit=16384
    value=service.prepare_preview(str(path),'1:1',Event(),viewport=(1888,886))
    assert value.raster.plan.full_source
    try:yield path,service,value
    finally:value.release();service.close_cache()


def count_reads(monkeypatch):
    calls=[];original=QImageReader.read
    def read(reader,*args,**kw):calls.append(True);return original(reader,*args,**kw)
    monkeypatch.setattr(QImageReader,'read',read)
    return calls


def cold(source,plan):
    return 4*source.facts.width*source.facts.height*4+2*plan.width*plan.height*4+source.signature.size


VIEW=(1306,557,1888,886,1888,886)


def test_completed_result_wrapper_cannot_create_a_guardless_pin(staged):
    _,service,value=staged;closes=[]
    admission=SimpleNamespace(close=lambda:closes.append(True))
    value.source_guard=GuardedSource(value.source_guard,admission)
    refs=dict(service.budget._refs)
    assert value.borrow() is None and service.budget._refs==refs
    plan=service.preview_plan(value.raster.source,'1:1',(1888,886),service.resources.sample(),view=VIEW,reuse=value)
    assert not plan.reuse_source and plan.peak_bytes==cold(value.raster.source,plan)
    assert not closes
    value.release();assert closes==[True]


def test_guardless_counterexample_is_cold_and_refused_at_190_mib(staged,monkeypatch):
    path,service,value=staged;source=value.raster.source
    # Same original live total as the independent counterexample, without pressure.
    padding=service.budget.reserve_peak(72315840-service.budget.reserved)
    broken=SimpleNamespace(released=False,raster=value.raster,source_guard=None)
    full=service.preview_plan(source,'1:1',(1888,886),service.resources.sample(),view=VIEW,reuse=broken)
    assert not full.reuse_source and full.peak_bytes==cold(source,full)
    service.resources.capacity=190*1024**2
    reads=count_reads(monkeypatch)
    try:
        assert service.budget.reserved+full.peak_bytes>service.preview_capacity(service.resources.sample())
        with pytest.raises(ValueError,match='headroom'):
            service.prepare_preview(str(path),'1:1',Event(),source,(1888,886),view=VIEW,reuse=broken)
        assert not reads and service.budget.reserved==72315840
    finally:service.budget.release(padding)


@pytest.mark.native
def test_valid_pin_still_passes_190_mib_without_native_decode(staged,monkeypatch):
    path,service,value=staged;source=value.raster.source;pin=value.borrow()
    padding=service.budget.reserve_peak(72315840-service.budget.reserved)
    service.resources.capacity=190*1024**2;reads=count_reads(monkeypatch);result=None
    try:
        plan=service.preview_plan(source,'1:1',(1888,886),service.resources.sample(),view=VIEW,reuse=pin)
        assert plan.reuse_source and plan.peak_bytes==83886080
        assert service.budget.reserved+plan.peak_bytes<=service.preview_capacity(service.resources.sample())
        assert service.budget.reserved+cold(source,plan)>service.preview_capacity(service.resources.sample())
        original=value.raster.straight.cacheKey()
        result=service.prepare_preview(str(path),'1:1',Event(),source,(1888,886),view=VIEW,reuse=pin)
        assert result.raster.plan.reuse_source and result.raster.straight.cacheKey()==original
        assert not reads
        value.release();pin.release()
        with pytest.raises(OSError):path.open('wb')
    finally:
        if result is not None:result.release()
        pin.release();service.budget.release(padding)
    assert service.budget.reserved==service.budget.overhead
    with path.open('ab'):pass


@pytest.mark.parametrize('loss',['released','closed_guard','no_backing','wrong_source','not_borrowed','no_guard','lost_lease'])
def test_ineligible_owner_never_gets_reuse_discount(staged,loss):
    _,service,value=staged;source=value.raster.source;pin=value.borrow()
    try:
        if loss=='released':pin.release()
        elif loss=='closed_guard':pin.source_guard.__exit__(None,None,None)
        elif loss=='no_backing':pin.raster=replace(pin.raster,straight=None)
        elif loss=='wrong_source':source=replace(source,signature=replace(source.signature,size=source.signature.size+1))
        elif loss=='not_borrowed':pin._pinned=False
        elif loss=='no_guard':pin.close_source()
        elif loss=='lost_lease':
            while service.budget.owns(pin.lease):service.budget.release(pin.lease)
        assert not preview_reuse_eligible(pin,source)
        plan=service.preview_plan(source,'1:1',(1888,886),service.resources.sample(),view=VIEW,reuse=pin)
        assert not plan.reuse_source and plan.peak_bytes==cold(source,plan)
    finally:pin.release()


@pytest.mark.parametrize('when',['before_execution','during_metadata'])
def test_lost_pin_after_admission_refuses_instead_of_cold_fallback(staged,monkeypatch,when):
    _,service,value=staged;source=value.raster.source;pin=value.borrow()
    plan=service.preview_plan(source,'1:1',(1888,886),service.resources.sample(),view=VIEW,reuse=pin)
    assert plan.reuse_source
    reads=count_reads(monkeypatch);before_limit=QImageReader.allocationLimit()
    if when=='before_execution':pin.release()
    else:
        from mediaforge.infrastructure.images import reader as module
        original=module.metadata
        def metadata(file):
            facts=original(file);pin.release();return facts
        monkeypatch.setattr(module,'metadata',metadata)
    try:
        with pytest.raises(ValueError,match='pin'):
            service.reader.decode_preview(source,plan,Event(),reuse=pin)
        assert not reads and QImageReader.allocationLimit()==before_limit
    finally:pin.release()


def test_cold_execution_does_not_infer_a_discount_from_later_backing(staged,monkeypatch):
    _,service,value=staged;source=value.raster.source;pin=value.borrow()
    plan=service.preview_plan(source,'1:1',(1888,886),service.resources.sample(),view=VIEW)
    assert not plan.reuse_source and plan.peak_bytes==cold(source,plan)
    reads=count_reads(monkeypatch);guard=None
    try:
        raster,guard=service.reader.decode_preview(source,plan,Event(),reuse=pin)
        assert len(reads)==1 and raster.source==source
    finally:
        if guard is not None:guard.__exit__(None,None,None)
        pin.release()


@pytest.mark.native
def test_canceled_pin_keeps_old_source_until_its_lease_is_released(staged,monkeypatch):
    path,service,value=staged;source=value.raster.source;pin=value.borrow();before=hashlib.sha256(path.read_bytes()).hexdigest()
    plan=service.preview_plan(source,'1:1',(1888,886),service.resources.sample(),view=VIEW,reuse=pin)
    value.release();cancel=Event();cancel.set();reads=count_reads(monkeypatch)
    try:
        with pytest.raises(ValueError,match='canceled'):
            service.reader.decode_preview(source,plan,cancel,reuse=pin)
        assert not reads and preview_reuse_eligible(pin,source)
        with pytest.raises(OSError):path.open('wb')
    finally:pin.release()
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    assert service.budget.reserved==service.budget.overhead


@pytest.mark.parametrize('mode',['none','raise'])
def test_failed_guard_retention_rolls_back_the_borrowed_memory_reference(staged,mode):
    _,service,value=staged;original=value.source_guard;before=dict(service.budget._refs)
    def retain():
        if mode=='raise':raise ValueError('Source guard already retired.')
        return None
    value.source_guard=SimpleNamespace(retain=retain)
    try:
        if mode=='raise':
            with pytest.raises(ValueError,match='retired'):value.borrow()
        else:assert value.borrow() is None
        assert service.budget._refs==before
    finally:value.source_guard=original
