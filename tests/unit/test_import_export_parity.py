"""Real exported files reopen, display and retain source sample precision."""
from fractions import Fraction
from pathlib import Path
from threading import Event
import hashlib,time
import numpy as np
import imagecodecs
import tifffile
import pytest
from mediaforge.application.contracts.image_source import SequenceSource
from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.services.export import ExportService
from mediaforge.application.services.image_export import capture
from mediaforge.application.state.workspace import MediaItem
from mediaforge.features.media.export import ExportControl
from tests.unit.test_image_export import executor

pytestmark=[pytest.mark.native,pytest.mark.integration]


def samples(depth,alpha,seed=0):
    y,x=np.indices((19,31));rgb=np.stack(((x*1709+y*123+seed*719)%65536,(x*91+y*3719+17)%65536,(x*2849+y*29+193)%65536),axis=2)
    if depth=='float':rgb=rgb.astype(np.float32)/np.float32(79003)+np.float32(2**-19)
    elif depth=='8':rgb=(rgb%256).astype(np.uint8)
    else:rgb=rgb.astype(np.uint16)
    if alpha:
        a=np.array([0,.0003,.25,.5012345,1],np.float32) if depth=='float' else np.array([0,1,64,128,255],np.uint8) if depth=='8' else np.array([0,1,16383,32768,65535],np.uint16)
        rgb=np.concatenate((rgb,a[x%5,None]),axis=2)
    return rgb


def write(path,data,compression='deflate'):
    if path.suffix=='.png':path.write_bytes(imagecodecs.png_encode(data))
    else:tifffile.imwrite(path,data,photometric='rgb',metadata=None,extrasamples=['unassalpha'] if data.shape[2]==4 else None,compression=compression)


def fixture(tmp_path,depth,alpha,sequence=False):
    root=tmp_path/'inputs';root.mkdir();out=tmp_path/'outputs';out.mkdir();fmt='png' if depth!='float' else 'tiff';arrays=[]
    for i in range(3 if sequence else 1):
        data=samples(depth,alpha,i);arrays.append(data);write(root/(f'picture_{1001+i:04d}.'+fmt if sequence else 'source.'+fmt),data)
    runner=executor();s=runner._image_service
    if sequence:
        m=s.reader.discover_sequence(str(root/('picture_1001.'+fmt)),Event());src=SequenceSource(m.members[0].path,m.identity,m)
    else:src=s.reader.probe(str(root/('source.'+fmt)))
    item=MediaItem.create(MediaMetadataDto('source',src.path,fmt,None,'',31,19,None,source=src));item.output.folder=str(out)
    if sequence:item.sequence.confirmed=True;item.sequence.rate=Fraction(2997,125);item.output.kind='sequence'
    return runner,item,arrays


def execute(runner,item):
    service=ExportService(runner);runner._image_service.result_preparer=service
    assert service.start_request(capture(item));deadline=time.monotonic()+40
    while service.busy and time.monotonic()<deadline:time.sleep(.005)
    job=service.snapshot();assert job.state=='Completed',(job,runner.last_image_metrics)
    result=service.completed_result();assert result and result.job_id==job.job_id and result.viewer_supported
    return service,result


CASES=[('png',d,'raw',a) for d in ['8','16'] for a in [False,True]]+[( 'tiff',d,c,a) for d in ['8','16','float'] for c in ['raw','packbits','lzw','deflate'] for a in [False,True]]

@pytest.mark.parametrize('format,depth,compression,alpha',CASES)
@pytest.mark.parametrize('sequence',[False,True])
def test_lossless_precision_still_and_sequence_round_trip(tmp_path,format,depth,compression,alpha,sequence):
    runner,item,arrays=fixture(tmp_path,depth,alpha,sequence);s=runner._image_service
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (tmp_path/'inputs').iterdir()}
    item.output.container=format;item.output.image_depth=depth;item.output.tiff_compression=compression;item.output.filename='first'
    service,result=execute(runner,item);opened=[];manifest=None
    try:
        if sequence:
            manifest=s.discover_exported_sequence(result,Event());m=manifest.manifest;source=SequenceSource(m.members[0].path,m.identity,m)
            assert m.count==3 and result.rate==Fraction(2997,125)
            paths=[Path(v.path) for v in m.members]
        else:paths=[Path(result.path)]
        for i,path in enumerate(paths):
            actual=imagecodecs.png_decode(path.read_bytes()) if format=='png' else tifffile.imread(path)
            assert actual.dtype==arrays[i].dtype and np.array_equal(actual,arrays[i])
            facts=s.reader.probe(str(path)).facts
            assert (facts.depth,facts.sample_type)==(32,'float') if depth=='float' else facts.depth==int(depth)
            prepared=s.prepare_exported_sequence_preview(result,source,i,'1:1',Event(),(100,100)) if sequence else s.prepare_exported_preview(result,'1:1',Event(),(100,100))
            assert not prepared.raster.display.isNull();assert prepared.metadata.source.facts==facts
            # Independent source-sample -> premultiplied display witnesses.
            normalized=arrays[i].astype(np.float64)/(1 if arrays[i].dtype.kind=='f' else np.iinfo(arrays[i].dtype).max)
            for yy,xx in [(0,0),(3,4),(7,12),(17,29)]:
                rgba=normalized[yy,xx];a=rgba[3] if alpha else 1;pixel=prepared.raster.display.pixel(xx,yy)
                expected=np.floor(np.clip(rgba[:3],0,1)*a*255+.5).astype(int)
                actual=np.array([(pixel>>16)&255,(pixel>>8)&255,pixel&255])
                assert np.max(np.abs(actual-expected))<=1
                assert (pixel>>24)&255==int(np.floor(a*255+.5))
            opened.append(prepared.metadata);prepared.release();assert not service._processing_gate.busy
        new=MediaItem.create(opened[-1]);new.output.folder=str(tmp_path/'outputs');new.output.filename='second';new.output.container=format;new.output.image_depth=depth;new.output.tiff_compression=compression
        second=runner.execute(capture(new),ExportControl())
        actual=imagecodecs.png_decode(second.output.read_bytes()) if format=='png' else tifffile.imread(second.output)
        assert np.array_equal(actual,arrays[-1]) and actual.dtype==arrays[-1].dtype
        assert all(hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in before.items())
    finally:
        if manifest is not None:manifest.release()
        service.shutdown();s.close_cache()


@pytest.mark.parametrize('compression',['raw','packbits','lzw','deflate'])
def test_float_crop_and_matte_never_use_eight_bit_intermediate(tmp_path,compression):
    runner,item,arrays=fixture(tmp_path,'float',True);s=runner._image_service
    item.output.container='tiff';item.output.image_depth='float';item.output.tiff_compression=compression;item.output.filename='float-crop';item.crop.enabled=True;item.crop.x=2;item.crop.y=3;item.crop.width=17;item.crop.height=11
    result=runner.execute(capture(item),ExportControl())
    actual=tifffile.imread(result.output);assert np.array_equal(actual,arrays[0][3:14,2:19])
    item.output.filename='float-flat';item.output.matte='white'
    result=runner.execute(capture(item),ExportControl())
    data=arrays[0][3:14,2:19];expected=(data[:,:,:3].astype(np.float64)*data[:,:,3:]+1-data[:,:,3:]).astype(np.float32)
    assert np.allclose(tifffile.imread(result.output),expected,rtol=0,atol=1e-7)
    s.close_cache()


@pytest.mark.parametrize('format,option',[('jpeg',v) for v in ['411','422','444']]+[('webp',v) for v in [True,False]])
@pytest.mark.parametrize('quality',[0,37,100])
def test_lossy_configuration_reopens_and_reexports(tmp_path,format,option,quality):
    if format=='jpeg' and quality==0:quality=1  # Offered normalized zero maps to minimum encoder quality 1.
    runner,item,arrays=fixture(tmp_path,'16',format=='webp');s=runner._image_service
    item.output.container=format;item.output.filename='encoded';item.output.jpeg_quality=quality if format=='jpeg' else 90;item.output.jpeg_subsampling=option if format=='jpeg' else '444'
    item.output.webp_lossless=option if format=='webp' else True;item.output.webp_quality=quality;item.output.webp_ack=True
    service,result=execute(runner,item)
    prepared=s.prepare_exported_preview(result,'1:1',Event(),(100,100))
    assert not prepared.raster.display.isNull()
    if format=='jpeg':
        from mediaforge.infrastructure.images.encode_worker import jpeg_sampling
        assert jpeg_sampling(Path(result.path).read_bytes())[1]=={'411':0x41,'422':0x21,'444':0x11}[option]
    else:
        actual=imagecodecs.webp_decode(Path(result.path).read_bytes(),hasalpha=True)
        assert np.array_equal(actual[:,:,3],((arrays[0][:,:,3].astype(np.uint32)+128)//257).astype(np.uint8))
    decoded=imagecodecs.jpeg_decode(Path(result.path).read_bytes()) if format=='jpeg' else imagecodecs.webp_decode(Path(result.path).read_bytes(),hasalpha=True)
    expected=((arrays[0].astype(np.uint32)+128)//257).astype(np.uint8)
    mask=expected[:,:,3]>0 if format=='webp' else np.ones(expected.shape[:2],bool)
    difference=np.abs(decoded[:,:,:3].astype(float)-expected[:,:,:3])
    assert float(difference[mask].mean()) <= (0 if format=='webp' and option else 55+(100-quality)*.65)
    other=MediaItem.create(prepared.metadata);other.output.folder=item.output.folder;other.output.filename='again';other.output.container='png'
    runner.execute(capture(other),ExportControl());prepared.release();service.shutdown();s.close_cache()


@pytest.mark.parametrize('depth',['8','16','float'])
@pytest.mark.parametrize('destination',['8','16','float'])
def test_precision_conversion_is_explicit(tmp_path,depth,destination):
    runner,item,arrays=fixture(tmp_path,depth,True)
    item.output.container='tiff';item.output.image_depth=destination;item.output.filename='conversion'
    result=runner.execute(capture(item),ExportControl());actual=tifffile.imread(result.output)
    data=arrays[0]
    if destination==depth:expected=data
    elif destination=='float':expected=data.astype(np.float32)/np.float32(np.iinfo(data.dtype).max)
    elif depth=='float':expected=np.floor(np.clip(data,0,1).astype(np.float64)*(65535 if destination=='16' else 255)+.5).astype(np.uint16 if destination=='16' else np.uint8)
    elif destination=='16':expected=data.astype(np.uint16)*257
    else:expected=((data.astype(np.uint32)+128)//257).astype(np.uint8)
    assert np.array_equal(actual,expected)
    runner._image_service.close_cache()


@pytest.mark.parametrize('sampling',['411','422','444'])
def test_jpeg_sequence_members_beyond_first_reopen(tmp_path,sampling):
    runner,item,arrays=fixture(tmp_path,'16',False,True);item.output.container='jpeg';item.output.jpeg_subsampling=sampling;item.output.filename='sequence'
    service,result=execute(runner,item);s=runner._image_service;manifest=s.discover_exported_sequence(result,Event());m=manifest.manifest;source=SequenceSource(m.members[0].path,m.identity,m)
    from mediaforge.infrastructure.images.encode_worker import jpeg_sampling
    try:
        for slot in range(3):
            assert jpeg_sampling(Path(m.members[slot].path).read_bytes())[1]=={'411':0x41,'422':0x21,'444':0x11}[sampling]
            p=s.prepare_exported_sequence_preview(result,source,slot,'1:1',Event(),(100,100));assert not p.raster.display.isNull();p.release()
    finally:manifest.release();service.shutdown();s.close_cache()


@pytest.mark.parametrize('fault',['unsupported_profile','wrong_depth'])
def test_native_output_must_pass_own_import_profile_before_publication(tmp_path,monkeypatch,fault):
    from dataclasses import replace
    runner,item,_=fixture(tmp_path,'16',True);service=runner._image_service
    item.output.container='png';item.output.image_depth='16';item.output.filename='not-published'
    original=service.reader.probe;source=Path(item.metadata.source_path);before=hashlib.sha256(source.read_bytes()).hexdigest();checked=[]
    def probe(path):
        result=original(path)
        if Path(path)!=source:
            checked.append(path)
            if fault=='unsupported_profile':raise ValueError('Independent input profile refuses this staged output')
            return replace(result,facts=replace(result.facts,depth=8))
        return result
    monkeypatch.setattr(service.reader,'probe',probe)
    with pytest.raises((RuntimeError,ValueError)):
        runner.execute(capture(item),ExportControl())
    assert checked and not (tmp_path/'outputs/not-published.png').exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    assert not runner._processes._unreaped_process
    service.close_cache()


@pytest.mark.parametrize('lossless',[True,False])
def test_webp_opaque_alpha_plane_optimization_remains_round_trip_safe(tmp_path,lossless):
    runner,item,arrays=fixture(tmp_path,'16',True);data=arrays[0];data[:,:,3]=65535
    source=Path(item.metadata.source_path);write(source,data);item.metadata=__import__('dataclasses').replace(item.metadata,source=runner._image_service.reader.probe(str(source)))
    item.output.container='webp';item.output.webp_lossless=lossless;item.output.webp_ack=True;item.output.filename='opaque'
    service,result=execute(runner,item)
    actual=imagecodecs.webp_decode(Path(result.path).read_bytes(),hasalpha=True)
    assert np.all(actual[:,:,3]==255)
    prepared=runner._image_service.prepare_exported_preview(result,'1:1',Event(),(100,100));assert not prepared.raster.display.isNull()
    prepared.release();service.shutdown();runner._image_service.close_cache()


@pytest.mark.parametrize('depth',[8,16])
@pytest.mark.parametrize('alpha',[False,True])
def test_normalized_grayscale_precision_survives_processing(tmp_path,depth,alpha):
    from dataclasses import replace
    runner,item,_=fixture(tmp_path,str(depth),False);y,x=np.indices((19,31));gray=((x*257+y*391)%2**depth).astype(np.uint8 if depth==8 else np.uint16)
    plane=np.full_like(gray,2**(depth-1));data=np.stack((gray,plane),axis=2) if alpha else gray
    path=Path(item.metadata.source_path);path.write_bytes(imagecodecs.png_encode(data));source=runner._image_service.reader.probe(str(path));item.metadata=replace(item.metadata,source=source)
    item.output.container='png';item.output.image_depth='16';item.output.filename='normalized'
    service,result=execute(runner,item);actual=imagecodecs.png_decode(Path(result.path).read_bytes())
    expected=np.repeat(gray[:,:,None],3,axis=2)
    if alpha:expected=np.concatenate((expected,plane[:,:,None]),axis=2)
    if depth==8:expected=expected.astype(np.uint16)*257
    assert np.array_equal(actual,expected)
    prepared=runner._image_service.prepare_exported_preview(result,'1:1',Event(),(100,100));prepared.release();service.shutdown();runner._image_service.close_cache()


@pytest.mark.parametrize('lossless',[True,False])
def test_webp_rgb_without_alpha_has_complete_import_path(tmp_path,lossless):
    runner,item,_=fixture(tmp_path,'16',False);item.output.container='webp';item.output.webp_lossless=lossless;item.output.webp_quality=37;item.output.filename='rgb'
    service,result=execute(runner,item);actual=imagecodecs.webp_decode(Path(result.path).read_bytes(),hasalpha=False);assert actual.shape==(19,31,3)
    prepared=runner._image_service.prepare_exported_preview(result,'1:1',Event(),(100,100));assert not prepared.metadata.source.facts.alpha
    prepared.release();service.shutdown();runner._image_service.close_cache()
