"""Final Media controls: independent stored-pixel/tag and exact-clock oracles."""
from fractions import Fraction
from dataclasses import replace
from pathlib import Path
import hashlib,subprocess,json,struct
import numpy as np
import imagecodecs
import pytest
from PySide6.QtGui import QImage,QColor
from mediaforge.application.contracts.sequence import sequence_rate,friendly_rate
from mediaforge.application.services.image_export import capture
from mediaforge.features.media.export import ExportControl
from test_image_export import make_item,executor


@pytest.mark.parametrize('text,expected',[('23.976',Fraction(2997,125)),('29.97',Fraction(2997,100)),
    ('59.94',Fraction(2997,50)),('21',Fraction(21)),('1.0',Fraction(1)),('23.9',Fraction(239,10)),
    ('23.976001',Fraction(23976001,1000000)),('24000/1001',Fraction(24000,1001))])
def test_decimal_fps_is_exact_and_presets_are_explicit(text,expected):
    assert sequence_rate(text)==expected
    assert sequence_rate(str(expected))==expected
    if expected not in (Fraction(24000,1001),Fraction(30000,1001),Fraction(60000,1001)):
        assert sequence_rate(friendly_rate(expected))==expected


def test_settings_are_format_specific_and_quality_mapping_is_monotonic(tmp_path):
    from mediaforge.application.state.workspace import MediaWorkspaceSession
    item=make_item(tmp_path);workspace=MediaWorkspaceSession();workspace.items=[item];workspace.active_id=item.id
    values=[]
    for n in range(101):
        assert workspace.set_output('jpeg_quality_normalized',f'{n/100:.2f}')
        assert item.output.jpeg_quality_normalized==f'{n/100:.2f}'
        values.append(item.output.jpeg_quality)
    assert values[0]==1 and values[-1]==100 and values==sorted(values)
    assert workspace.set_output('container','tiff') and workspace.set_output('image_depth','float')
    assert workspace.set_output('container','png') and item.output.image_depth=='8'
    assert workspace.set_output('container','jpeg') and item.output.matte=='black'
    assert workspace.set_output('container','png') and item.output.matte==''
    for text in ['NaN','Infinity','-0.1','1.01','0.999']:assert not workspace.set_output('jpeg_quality_normalized',text)


def source_pixels(item):
    image=QImage(128,96,QImage.Format_RGBA8888)
    for y in range(96):
        for x in range(128):image.setPixelColor(x,y,QColor((x*7)%256,(y*11)%256,(x+y)%256,[0,1,64,128,254,255][x//22]))
    assert image.save(item.metadata.source_path)
    from mediaforge.infrastructure.images.reader import QtImageReader
    source=QtImageReader().probe(item.metadata.source_path)
    item.metadata=replace(item.metadata,source=source)
    return np.frombuffer(bytes(image.constBits()),dtype=np.uint8).reshape(96,128,4).copy()


def tags(path):
    """Independent minimal classic-TIFF IFD reader, no tifffile/OIIO metadata API."""
    data=Path(path).read_bytes();endian='<' if data[:2]==b'II' else '>'
    assert struct.unpack_from(endian+'H',data,2)[0]==42
    offset=struct.unpack_from(endian+'I',data,4)[0];count=struct.unpack_from(endian+'H',data,offset)[0];result={}
    for i in range(count):
        entry=offset+2+i*12;tag,kind,n=struct.unpack_from(endian+'HHI',data,entry)
        if kind not in (3,4):continue
        width=2 if kind==3 else 4;value=entry+8 if n*width<=4 else struct.unpack_from(endian+'I',data,entry+8)[0]
        result[tag]=struct.unpack_from(endian+('H' if kind==3 else 'I')*n,data,value)
    return result


def ffmpeg_pixels(runtime,path,width,height,depth=8):
    r=subprocess.run([str(runtime.ffmpeg),'-v','error','-threads','1','-i',str(path),'-frames:v','1',
        '-pix_fmt','rgba64le' if depth==16 else 'rgba','-threads:v','1','-f','rawvideo','-'],check=True,capture_output=True,timeout=15)
    return np.frombuffer(r.stdout,dtype='<u2' if depth==16 else 'u1').reshape(height,width,4)


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('depth,compression',[(d,c) for d in ['8','16','float'] for c in ['raw','packbits','lzw','deflate']])
def test_tiff_actual_types_compression_straight_alpha_and_pixels(tmp_path,depth,compression):
    item=make_item(tmp_path);pixels=source_pixels(item);path=Path(item.metadata.source_path);before=hashlib.sha256(path.read_bytes()).hexdigest()
    item.output.container='tiff';item.output.image_depth=depth;item.output.tiff_compression=compression
    result=executor().execute(capture(item),ExportControl());t=tags(result.output)
    assert t[258]==(32 if depth=='float' else int(depth),)*4 and t[259]==({'raw':1,'packbits':32773,'lzw':5,'deflate':8}[compression],)
    assert t[274]==(1,) and t[277]==(4,) and t[338]==(2,) and t.get(339,(1,)*4)==((3 if depth=='float' else 1),)*4
    if depth=='float':
        actual=imagecodecs.tiff_decode(Path(result.output).read_bytes());assert actual.dtype==np.float32
        np.testing.assert_array_equal(actual,pixels.astype(np.float32)/np.float32(255))
    else:
        actual=ffmpeg_pixels(executor()._runtime,result.output,128,96,int(depth))
        np.testing.assert_array_equal(actual,pixels.astype(np.uint16)*257 if depth=='16' else pixels)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


@pytest.mark.native
@pytest.mark.integration
def test_png16_samples_are_full_range_expansion_not_shift(tmp_path):
    item=make_item(tmp_path);pixels=source_pixels(item);item.output.image_depth='16'
    result=executor().execute(capture(item),ExportControl());raw=Path(result.output).read_bytes()
    assert raw[24:26]==bytes([16,6])
    np.testing.assert_array_equal(ffmpeg_pixels(executor()._runtime,result.output,128,96,16),pixels.astype(np.uint16)*257)


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('sampling,pixel_format',[('411','yuvj411p'),('422','yuvj422p'),('444','yuvj444p')])
def test_jpeg_real_sampling_and_quality(tmp_path,sampling,pixel_format):
    item=make_item(tmp_path,alpha=False);item.output.container='jpeg';item.output.jpeg_subsampling=sampling
    runtime=executor()._runtime;result=executor().execute(capture(item),ExportControl())
    response=subprocess.run([str(runtime.ffprobe),'-v','error','-show_streams','-of','json',str(result.output)],check=True,capture_output=True,text=True,timeout=10)
    assert json.loads(response.stdout)['streams'][0]['pix_fmt']==pixel_format
    actual=ffmpeg_pixels(runtime,result.output,128,96)
    assert np.max(np.abs(actual[48,64].astype(int)-np.array([200,40,80,255])))<=3


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('lossless',[True,False])
def test_webp_selected_encoding_mode(tmp_path,lossless):
    item=make_item(tmp_path);item.output.container='webp';item.output.webp_ack=True;item.output.webp_lossless=lossless
    result=executor().execute(capture(item),ExportControl());raw=Path(result.output).read_bytes()
    assert (b'VP8L' if lossless else b'VP8 ') in raw
    actual=ffmpeg_pixels(executor()._runtime,result.output,128,96)
    assert actual[48,64,3]==128
    assert np.max(np.abs(actual[48,64,:3].astype(int)-np.array([200,40,80])))<=(0 if lossless else 5)


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('matte,background',[('black',0),('white',255)])
def test_flatten_is_opaque_rgb_not_black_alpha(tmp_path,matte,background):
    item=make_item(tmp_path);pixels=source_pixels(item);item.output.container='tiff';item.output.image_depth='16';item.output.matte=matte
    result=executor().execute(capture(item),ExportControl());actual=ffmpeg_pixels(executor()._runtime,result.output,128,96,16)
    expected=(pixels[:,:,:3].astype(np.uint32)*pixels[:,:,3:4]+background*(255-pixels[:,:,3:4].astype(np.uint32))+127)//255
    # Qualified native 8-bit composite is within one rounding code; expansion
    # to 16-bit is exact k*257, not a claim of new source precision.
    assert np.max(np.abs(actual[:,:,:3].astype(int)-expected.astype(int)*257))<=257
    assert np.all(actual[:,:,3]==65535) and tags(result.output)[277]==(3,)


def area_reference(pixels, width, height, picture_width, picture_height, x=0, y=0, floating=False):
    # Independent tiny-cell rational reference; no prefix arrays or production math.
    h,w,_=pixels.shape;result=np.zeros((height,width,4),dtype=np.float32 if floating else np.uint16)
    from math import floor,ceil
    for oy in range(height):
        top=(Fraction(oy)-y)*h/picture_height;bottom=(Fraction(oy+1)-y)*h/picture_height
        for ox in range(width):
            left=(Fraction(ox)-x)*w/picture_width;right=(Fraction(ox+1)-x)*w/picture_width
            alpha=Fraction(0);rgb=[Fraction(0)]*3
            for sy in range(max(0,floor(top)),min(h,ceil(bottom))):
                wy=max(Fraction(0),min(bottom,sy+1)-max(top,sy))
                for sx in range(max(0,floor(left)),min(w,ceil(right))):
                    wx=max(Fraction(0),min(right,sx+1)-max(left,sx));a=int(pixels[sy,sx,3])*wx*wy
                    alpha+=a
                    for ch in range(3):rgb[ch]+=int(pixels[sy,sx,ch])*a
            def rounded(v):return (2*v.numerator+v.denominator)//(2*v.denominator)
            result[oy,ox,3]=(float(alpha/((right-left)*(bottom-top)))/65535 if floating else rounded(alpha/((right-left)*(bottom-top))))
            if alpha:
                for ch in range(3):result[oy,ox,ch]=float(rgb[ch]/alpha)/65535 if floating else rounded(rgb[ch]/alpha)
    return result


@pytest.mark.parametrize('size,picture,origin',[
    ((16,8),(16,8),(0,0)),((16,16),(16,16),(0,0)),
    ((7,5),(Fraction(13,2),Fraction(13,4)),(Fraction(1,4),Fraction(7,8))),
    ((5,5),(10,5),(-Fraction(5,2),0))])
def test_native_exact_alpha_area_both_edge_geometries_and_fractional_placement(size,picture,origin):
    from mediaforge.infrastructure.images.encode_worker import alpha_area
    pixels=np.zeros((16,32,4),dtype=np.uint16);pixels[:,:16]=[65535,0,0,65535];pixels[:,16:]=[0,65535,0,0]
    # Retain partial/near-zero constant witnesses alongside the hard edge.
    pixels[8:,0:4,3]=257;pixels[8:,4:8,3]=64*257;pixels[8:,8:12,3]=128*257;pixels[8:,12:16,3]=254*257
    plan=dict(width=size[0],height=size[1],transform=dict(picture_width=str(picture[0]),picture_height=str(picture[1]),x=str(origin[0]),y=str(origin[1])))
    actual=alpha_area(pixels,plan);expected=area_reference(pixels,*size,*picture,*origin)
    np.testing.assert_array_equal(actual,expected)
    assert not np.any(actual[:,:,1]) # hidden green contributes no visible color


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('fmt,depth',[('png','8'),('png','16'),('tiff','float'),('webp','8')])
def test_preserve_alpha_reformat_is_exact_and_source_based(tmp_path,fmt,depth):
    item=make_item(tmp_path);pixels=source_pixels(item);item.output.container=fmt;item.output.image_depth=depth;item.output.webp_ack=True
    item.resize.enabled=True;item.resize.mode='format';item.resize.width=64;item.resize.height=48;item.resize.placement='stretch'
    result=executor().execute(capture(item),ExportControl())
    expected=area_reference(pixels.astype(np.uint16)*257,64,48,64,48,floating=depth=='float')
    actual=(imagecodecs.tiff_decode(Path(result.output).read_bytes()) if fmt=='tiff' else
            ffmpeg_pixels(executor()._runtime,result.output,64,48,16 if depth=='16' else 8))
    if depth=='float':np.testing.assert_array_equal(actual,expected)
    else:np.testing.assert_array_equal(actual,expected if depth=='16' else ((expected.astype(np.uint32)+128)//257).astype(np.uint8))


@pytest.mark.native
@pytest.mark.integration
def test_codec_plan_and_inputs_stay_locked_through_validation(tmp_path,monkeypatch):
    from mediaforge.infrastructure.ffmpeg.child_process import ChildProcesses
    real=ChildProcesses.run;checks=[]
    def run(self,command,*args,**kwargs):
        if any(str(arg).endswith('encode_worker.py') for arg in command):
            plan=Path(command[-2]);metadata=json.loads(plan.read_text('utf-8'))
            for path in [plan,Path(metadata['members'][0][0])]:
                with pytest.raises(PermissionError):path.write_bytes(b'UNVALIDATED')
                with pytest.raises(PermissionError):path.rename(path.with_name('substitute'))
            checks.append(command[-1])
        return real(self,command,*args,**kwargs)
    monkeypatch.setattr(ChildProcesses,'run',run)
    item=make_item(tmp_path);item.output.container='tiff';item.output.image_depth='float'
    runner=executor();result=runner.execute(capture(item),ExportControl())
    assert checks==['encode','validate'] and result.output.exists()
    assert 0<runner.last_image_metrics['peak_job']<=512*1024**2 and runner.last_image_metrics['reaped']


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('container',['mp4','mov'])
def test_processed_timecoded_video_has_only_validated_video_audio_streams(tmp_path,container):
    from mediaforge.application.services.media import OpenMediaService
    from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe
    from mediaforge.application.services.export import capture as video_capture
    from mediaforge.application.state.workspace import MediaItem
    runner=executor();runtime=runner._runtime;source=tmp_path/'timecoded.mov'
    subprocess.run([str(runtime.ffmpeg),'-v','error','-n','-f','lavfi','-i','testsrc2=s=128x96:r=25:d=0.4',
        '-c:v','libx264','-timecode','01:02:03:04','-write_tmcd','1',str(source)],check=True,capture_output=True,timeout=15)
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    def streams(path):return json.loads(subprocess.run([str(runtime.ffprobe),'-v','error','-show_streams','-of','json',str(path)],check=True,capture_output=True,text=True).stdout)['streams']
    assert any(s['codec_type']=='data' for s in streams(source))
    metadata=OpenMediaService(FfprobeMediaMetadataProbe(runtime.ffprobe)).open_media(str(source)).metadata
    item=MediaItem.create(metadata);item.output.folder=str(tmp_path);item.output.filename='processed';item.output.container=container;item.output.codec='h264'
    result=runner.execute(video_capture(item),ExportControl())
    assert [s['codec_type'] for s in streams(result.output)]==['video']
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before


@pytest.mark.native
@pytest.mark.integration
def test_identity_reformat_preserves_hidden_rgb_and_exact_alpha(tmp_path):
    item=make_item(tmp_path);expected=source_pixels(item)
    item.resize.enabled=True;item.resize.mode='format';item.resize.width=128;item.resize.height=96;item.resize.placement='stretch'
    result=executor().execute(capture(item),ExportControl())
    np.testing.assert_array_equal(ffmpeg_pixels(executor()._runtime,result.output,128,96),expected)


@pytest.mark.parametrize('height',[8,16])
def test_exact_half_coverage_edge_is_alpha128_without_green_halo(height):
    from mediaforge.infrastructure.images.encode_worker import alpha_area
    pixels=np.empty((16,32,4),dtype=np.uint16);pixels[:,::2]=[65535,0,0,65535];pixels[:,1::2]=[0,65535,0,0]
    p=dict(width=16,height=height,transform=dict(picture_width='16',picture_height=str(height),x='0',y='0'))
    actual=alpha_area(pixels,p);codes=((actual.astype(np.uint32)+128)//257).astype(np.uint8)
    np.testing.assert_array_equal(codes,np.broadcast_to([255,0,0,128],codes.shape))
    p['depth']='float';actual=alpha_area(pixels,p)
    np.testing.assert_array_equal(actual,np.broadcast_to([1.,0.,0.,.5],actual.shape))


@pytest.mark.parametrize('alpha',[0,1,64,128,254,255])
def test_constant_alpha_native_exact_area_is_unchanged(alpha):
    from mediaforge.infrastructure.images.encode_worker import alpha_area
    data=np.empty((5,9,4),dtype=np.uint16);data[:]=[100*257,200*257,13*257,alpha*257]
    actual=alpha_area(data,dict(width=7,height=3,transform=dict(picture_width='7',picture_height='3',x='0',y='0')))
    assert np.all(actual[:,:,3]==alpha*257)
    if alpha:np.testing.assert_array_equal(actual[:,:,:3],np.broadcast_to([100*257,200*257,13*257],actual[:,:,:3].shape))
