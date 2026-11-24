"""Independent typed samples, metadata precedence and allocation-plan witnesses."""
from pathlib import Path
from threading import Event
from dataclasses import replace
import struct,zlib
import numpy as np
import imagecodecs,tifffile,pytest
from mediaforge.application.services.images import ImageSourceService
from mediaforge.infrastructure.images.reader import QtImageReader
from mediaforge.infrastructure.images.precision import convert,orient,float_area
from tests.unit.test_image_export import executor,Resources
from tests.unit.test_import_export_parity import fixture
from mediaforge.application.services.image_export import capture
from mediaforge.features.media.export import ExportControl, ExportError

@pytest.mark.parametrize('dtype',[np.uint8,np.uint16,np.float32])
@pytest.mark.parametrize('compression',['raw','packbits','lzw','deflate'])
def test_tiff_decode_has_no_aliased_qfile_cursor(tmp_path,dtype,compression):
    rng=np.random.default_rng(17);data=rng.integers(0,65536,(91,127,4),dtype=np.uint16)
    data=(data/np.float32(65535)).astype(np.float32) if dtype==np.float32 else data.astype(dtype)
    path=tmp_path/'large-enough.tiff';tifffile.imwrite(path,data,photometric='rgb',extrasamples=['unassalpha'],compression=None if compression=='raw' else compression,metadata=None)
    assert path.stat().st_size>8192
    service=ImageSourceService(QtImageReader(),resources=Resources());p=service.prepare_preview(str(path),'1:1',Event(),viewport=(200,200))
    image=p.raster.straight;assert image is not None
    raw=np.frombuffer(image.constBits(),dtype).reshape(91,127,4)
    assert np.array_equal(raw,data)
    p.release();service.close_cache()

@pytest.mark.parametrize('depth',[8,16])
@pytest.mark.parametrize('channels',[1,2])
def test_gray_and_gray_alpha_normalize_without_losing_precision(tmp_path,depth,channels):
    y,x=np.indices((15,23));a=((x*257+y*379)%2**depth).astype(np.uint16 if depth==16 else np.uint8)
    data=a if channels==1 else np.stack((a,np.full_like(a,2**(depth-1))),2)
    path=tmp_path/'gray.png';path.write_bytes(imagecodecs.png_encode(data))
    service=ImageSourceService(QtImageReader(),resources=Resources());prepared=service.prepare_preview(str(path),'1:1',Event(),viewport=(100,100))
    assert not prepared.raster.display.isNull() and prepared.metadata.source.facts.depth==depth
    prepared.release();service.close_cache()


def test_float_area_matches_independent_pixel_cell_reference():
    from fractions import Fraction
    data=np.array([[[.1234567,.7,.3,1],[.9,.1,.2,0],[.71,.32,.21,.25]],[[.29,.17,.9,.5],[.1,.2,.4,1],[.8,.7,.1,.75]]],np.float32)
    plan=dict(width=2,height=3,transform=dict(picture_width='2',picture_height='3',x='0',y='0'))
    got=float_area(data,plan);expected=np.zeros_like(got)
    for oy in range(3):
        for ox in range(2):
            color=np.zeros(3);alpha=0.;area=Fraction(3,2)*Fraction(2,3)
            for sy in range(2):
                for sx in range(3):
                    w=max(0,min(Fraction((ox+1)*3,2),sx+1)-max(Fraction(ox*3,2),sx))*max(0,min(Fraction((oy+1)*2,3),sy+1)-max(Fraction(oy*2,3),sy))
                    alpha+=float(w)*float(data[sy,sx,3]);color+=float(w)*data[sy,sx,:3].astype(float)*float(data[sy,sx,3])
            expected[oy,ox,:3]=color/alpha if alpha else 0;expected[oy,ox,3]=alpha/float(area)
    assert np.allclose(got,expected,rtol=0,atol=6e-8)


def test_bt709_png_cicp_precedence_display_and_export(tmp_path):
    runner,item,arrays=fixture(tmp_path,'8',False);p=Path(item.metadata.source_path)
    blob=p.read_bytes();payload=bytes([1,1,0,1]);chunk=struct.pack('>I',4)+b'cICP'+payload+struct.pack('>I',zlib.crc32(b'cICP'+payload)&0xffffffff)
    gamma=struct.pack('>I',45455);chunk+=struct.pack('>I',4)+b'gAMA'+gamma+struct.pack('>I',zlib.crc32(b'gAMA'+gamma)&0xffffffff)
    p.write_bytes(blob[:33]+chunk+blob[33:]);source=runner._image_service.reader.probe(str(p));assert source.facts.color_class=='BT.709'
    item.metadata=replace(item.metadata,source=source);item.output.container='png';item.output.image_depth='16';item.output.filename='srgb'
    data=arrays[0].astype(float)/255;linear=np.where(data<.081,data/4.5,((data+.099)/1.099)**(1/.45));srgb=np.where(linear<=.0031308,12.92*linear,1.055*linear**(1/2.4)-.055)
    result=runner.execute(capture(item),ExportControl());actual=imagecodecs.png_decode(result.output.read_bytes())
    assert np.max(np.abs(actual.astype(int)-np.floor(srgb*65535+.5)))<=1
    prepared=runner._image_service.prepare_preview(str(p),'1:1',Event(),viewport=(100,100))
    for y,x in [(0,0),(5,7),(9,17)]:
        pixel=prepared.raster.display.pixelColor(x,y).getRgb()[:3];assert np.max(np.abs(np.array(pixel)-np.floor(srgb[y,x]*255+.5)))<=1
    prepared.release();runner._image_service.close_cache()

@pytest.mark.parametrize('value',[float('nan'),float('inf')])
def test_invalid_float_values_fail_closed(tmp_path,value):
    data=np.zeros((3,4,4),np.float32);data[:,:,3]=1;data[1,1,0]=value;p=tmp_path/'invalid.tiff';tifffile.imwrite(p,data,photometric='rgb',metadata=None,extrasamples=['unassalpha'])
    s=ImageSourceService(QtImageReader(),resources=Resources())
    with pytest.raises(ValueError,match='Non-finite'):s.prepare_preview(str(p),'1:1',Event(),viewport=(100,100))
    assert s.budget.reserved==s.budget.overhead;s.close_cache()


@pytest.mark.parametrize('depth',['16','float'])
def test_typed_source_pin_agrees_with_actual_decoder_route(tmp_path,monkeypatch,depth):
    runner,item,_=fixture(tmp_path,depth,True);service=runner._image_service;source=item.metadata.source
    service.texture_limit=16;view=(3,4,12,11,12,11)
    value=service.prepare_preview(source.path,'1:1',Event(),viewport=(12,11),view=view);pin=value.borrow();assert pin is not None
    import mediaforge.infrastructure.images.precision as precision
    calls=[];real=precision.read_samples
    def observed(*a,**kw):calls.append(True);return real(*a,**kw)
    monkeypatch.setattr(precision,'read_samples',observed)
    view=(3,4,12,11,12,11)
    try:
        plan=service.preview_plan(source,'1:1',(12,11),view=view,reuse=pin);assert plan.reuse_source
        region=service.prepare_preview(source.path,'1:1',Event(),source,(12,11),view=view,reuse=pin)
        assert not calls and region.raster.straight.cacheKey()==value.raster.straight.cacheKey();region.release()
        from mediaforge.application.services.exported_result import GuardedSource
        from types import SimpleNamespace
        value.source_guard=GuardedSource(value.source_guard,SimpleNamespace(close=lambda:None))
        assert value.borrow() is None
        cold=service.preview_plan(source,'1:1',(12,11),view=view,reuse=value)
        assert not cold.reuse_source and cold.peak_bytes>=4*source.facts.width*source.facts.height*source.facts.decoded_bytes_per_pixel
        unpinned=service.prepare_preview(source.path,'1:1',Event(),source,(12,11),view=view,reuse=value);assert calls==[True];unpinned.release()
        pin.close_source()
        with pytest.raises(ValueError,match='pin'):service.reader.decode_preview(source,plan,Event(),reuse=pin)
        assert calls==[True]
    finally:pin.release();value.release();service.close_cache()


def test_complete_typed_processing_plan_is_checked_before_child(tmp_path,monkeypatch):
    runner,item,_=fixture(tmp_path,'float',True);item.output.container='tiff';item.output.image_depth='float';item.output.filename='blocked'
    request=capture(item)
    from mediaforge.infrastructure.ffmpeg.image_export import processing_peak
    assert processing_peak(request)>64*1024**2+request.width*request.height*32
    resource = runner._image_service.resources
    host = resource.sample_host()
    # Leave enough room for the parent admission check, but not for its
    # quarter-headroom child budget and the complete typed processing plan.
    headroom = 512 * 1024**2
    host["available_physical"] = max(2 * 1024**3, int(host["total_physical"] * .15)) + headroom
    host["available_commit"] = max(2 * 1024**3, int(host["total_commit"] * .10)) + headroom
    monkeypatch.setattr(resource, "sample_host", lambda: host)
    import mediaforge.infrastructure.ffmpeg.preview_job as jobs
    calls=[];real=jobs.PreviewJobs.__call__
    def spawned(*a,**kw):calls.append(True);return real(*a,**kw)
    monkeypatch.setattr(jobs.PreviewJobs,'__call__',spawned)
    try:
        with pytest.raises(ExportError, match="complete processing plan") as refusal:
            runner.execute(request, ExportControl())
        assert refusal.value.code == "insufficient_memory"
        assert not calls and not request.destination.exists()
    finally:
        runner._image_service.close_cache()



def test_control_receipt_leases_overlap_large_preview_with_same_host_gate(tmp_path):
    from tests.unit.test_preview_reuse_admission import Resources as Capacity
    from mediaforge.infrastructure.filesystem.export_identity import capture_output,LockedResult
    from mediaforge.infrastructure.filesystem.image_output import ReadLocks
    from mediaforge.application.contracts.exported_result import ExportedResult
    s=ImageSourceService(QtImageReader(),resources=Capacity(2*1024**3));live=s.budget.reserve_peak(512*1024**2,2*1024**3)
    p=tmp_path/'result';p.write_bytes(b'owned');control=ExportControl()
    try:
        with ReadLocks() as locks:
            locks.add(p);receipt=capture_output([p],[p.name],p,control,s.budget)
        held=LockedResult(ExportedResult(str(p),'still',1,1,'png',job_id=1,identity=receipt),control,s.budget)
        held.prepare();held.close();assert s.budget.reserved>512*1024**2
        s.resources.capacity=520*1024**2
        with pytest.raises(ValueError):s.budget.reserve_control(16*1024**2)
        assert s.budget.owns(live)
    finally:s.budget.release(live);s.close_cache()



def test_receipt_budget_callback_does_not_root_preview_owner():
    import weakref,gc
    s=ImageSourceService(QtImageReader(),resources=Resources());budget=s.budget;owner=weakref.ref(s)
    del s;gc.collect();assert owner() is None
    with pytest.raises(ValueError,match='owner'):budget.reserve_control(4096)



def test_finite_float_out_of_display_range_and_signed_zero_are_original_samples(tmp_path):
    runner,item,arrays=fixture(tmp_path,'float',True);data=arrays[0];data[0,0,:3]=[-0.,-.25,1.25]
    source=Path(item.metadata.source_path);tifffile.imwrite(source,data,photometric='rgb',metadata=None,extrasamples=['unassalpha'])
    facts=runner._image_service.reader.probe(str(source));item.metadata=replace(item.metadata,source=facts)
    item.output.container='tiff';item.output.image_depth='float';item.output.filename='exact-floats'
    result=runner.execute(capture(item),ExportControl());actual=tifffile.imread(result.output)
    assert np.array_equal(actual.view(np.uint32),data.view(np.uint32))
    prepared=runner._image_service.prepare_preview(str(result.output),'1:1',Event(),viewport=(100,100));assert not prepared.raster.display.isNull();prepared.release();runner._image_service.close_cache()


def test_transformed_nonfinite_float_cannot_be_written():
    from mediaforge.infrastructure.images.precision import validate_floats
    with pytest.raises(ValueError,match='Non-finite'):validate_floats(np.full((2,3,3),np.inf,np.float32))


@pytest.mark.parametrize('depth',['8','16','float'])
def test_tiff_orientation_six_applied_once_in_preview_and_processing(tmp_path,depth):
    from tests.unit.test_import_export_parity import samples
    from mediaforge.application.contracts.media import MediaMetadataDto
    from mediaforge.application.state.workspace import MediaItem
    data=samples(depth,True);path=tmp_path/'oriented.tiff'
    tifffile.imwrite(path,data,photometric='rgb',extrasamples=['unassalpha'],compression='deflate',metadata=None,extratags=[(274,'H',1,6,False)])
    runner=executor();s=runner._image_service;source=s.reader.probe(str(path));expected=np.rot90(data,-1)
    assert (source.facts.width,source.facts.height)==(19,31) and source.facts.orientation==6
    value=s.prepare_preview(str(path),'1:1',Event(),viewport=(100,100));assert (value.raster.display.width(),value.raster.display.height())==(19,31);value.release()
    item=MediaItem.create(MediaMetadataDto('oriented',source.path,'tiff',None,'',19,31,None,source=source));item.output.folder=str(tmp_path);item.output.filename='normalized';item.output.container='tiff';item.output.image_depth=depth
    result=runner.execute(capture(item),ExportControl());actual=tifffile.imread(result.output);assert np.array_equal(actual,expected)
    assert s.reader.probe(str(result.output)).facts.orientation==1;s.close_cache()
