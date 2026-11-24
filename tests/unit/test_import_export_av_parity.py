"""Produced AV classes must reopen under the same immutable result receipt."""
import json,subprocess,time,hashlib
from pathlib import Path
import pytest
from mediaforge.application.services.export import ExportService,capture
from mediaforge.application.services.media import OpenMediaService
from mediaforge.application.state.workspace import MediaItem,MediaWorkspaceSession
from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe
from mediaforge.infrastructure.ffmpeg.export import FfmpegExportExecutor
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
from mediaforge.features.media.export import ExportControl

pytestmark=[pytest.mark.native,pytest.mark.integration]

def cli(args):
    return subprocess.run(list(map(str,args)),capture_output=True,check=True,timeout=40,creationflags=subprocess.CREATE_NO_WINDOW).stdout

@pytest.fixture(scope='module')
def audio_input(tmp_path_factory):
    root=tmp_path_factory.mktemp('audio');rt=resolve_ffmpeg_runtime();source=root/'original.wav'
    cli([rt.ffmpeg,'-v','error','-n','-f','lavfi','-i','sine=frequency=783:sample_rate=48000:duration=0.8','-c:a','pcm_s16le',source])
    return rt,source

@pytest.mark.parametrize('format,bitrate',[('wav',192)]+[('mp3',n) for n in [128,192,256,320]])
def test_audio_export_import_transport_and_reexport(audio_input,tmp_path,format,bitrate):
    rt,source=audio_input;digest=hashlib.sha256(source.read_bytes()).hexdigest()
    opener=OpenMediaService(FfprobeMediaMetadataProbe(rt.ffprobe));original=opener.open_media(str(source));assert original.succeeded
    assert original.metadata.audio_only and not original.metadata.video_codec and original.metadata.width==0
    item=MediaItem.create(original.metadata);item.output.folder=str(tmp_path);item.output.filename='first';item.output.audio_format=format;item.output.audio_bitrate=bitrate
    executor=FfmpegExportExecutor(rt);service=ExportService(executor);opener._result_preparer=service
    assert service.start_request(capture(item));deadline=time.monotonic()+30
    while service.busy and time.monotonic()<deadline:time.sleep(.005)
    assert service.snapshot().state=='Completed',service.snapshot()
    result=service.completed_result();assert result.kind=='audio' and result.identity is not None
    opened=opener.open_exported_result(result);assert opened.succeeded,opened
    assert opened.metadata.audio_only and opened.guard is not None
    probe=json.loads(cli([rt.ffprobe,'-v','error','-show_streams','-show_format','-of','json',result.path]));sound=probe['streams'][0]
    assert len(probe['streams'])==1 and sound['codec_name']==('mp3' if format=='mp3' else 'pcm_s16le')
    decoded=cli([rt.ffmpeg,'-v','error','-i',result.path,'-f','s16le','-']);assert len(decoded)==48000*2*.8
    if format=='wav':assert decoded==cli([rt.ffmpeg,'-v','error','-i',source,'-f','s16le','-'])
    else:assert int(sound['bit_rate'])==bitrate*1000
    other=MediaItem.create(opened.metadata);other.output.folder=str(tmp_path);other.output.filename='second';other.output.audio_format='wav'
    other.trim.enabled=True;other.trim.in_ms=100;other.trim.out_ms=500
    reexport=executor.execute(capture(other),ExportControl());assert reexport.output.is_file()
    assert len(cli([rt.ffmpeg,'-v','error','-i',reexport.output,'-f','s16le','-']))==48000*2*.4
    opened.guard.close();service.shutdown();assert hashlib.sha256(source.read_bytes()).hexdigest()==digest


@pytest.fixture(scope='module')
def video_input(tmp_path_factory):
    root=tmp_path_factory.mktemp('video');rt=resolve_ffmpeg_runtime();source=root/'original.mov'
    cli([rt.ffmpeg,'-v','error','-n','-f','lavfi','-i','testsrc2=s=160x96:r=25:d=0.4','-f','lavfi','-i','sine=sample_rate=48000:duration=0.4',
         '-c:v','prores_ks','-profile:v','2','-pix_fmt','yuv422p10le','-c:a','pcm_s16le','-threads','1',source])
    return rt,source

VIDEO_CASES=[('mov','prores',q,'balanced','auto') for q in range(6)]+[(fmt,'h264',18,s,p) for fmt in ['mov','mp4'] for s in ['fast','balanced','quality'] for p in ['auto','main','high']]+[('mp4','h264',q,'fast','auto') for q in [0,1,51]]

@pytest.mark.parametrize('container,codec,quality,speed,profile',VIDEO_CASES)
def test_exported_video_reopens_indexes_and_reprocesses(video_input,tmp_path,container,codec,quality,speed,profile):
    rt,source=video_input;opener=OpenMediaService(FfprobeMediaMetadataProbe(rt.ffprobe));opened=opener.open_media(str(source));assert opened.succeeded
    item=MediaItem.create(opened.metadata);item.output.folder=str(tmp_path);item.output.filename='first';item.output.container=container;item.output.codec=codec;item.output.speed=speed;item.output.h264_profile=profile
    item.output.prores_quality=quality if codec=='prores' else 2;item.output.h264_quality=quality if codec=='h264' else 18
    executor=FfmpegExportExecutor(rt);service=ExportService(executor);opener._result_preparer=service
    assert service.start_request(capture(item));deadline=time.monotonic()+35
    while service.busy and time.monotonic()<deadline:time.sleep(.005)
    assert service.snapshot().state=='Completed',service.snapshot()
    result=service.completed_result();reopened=opener.open_exported_result(result);assert reopened.succeeded,reopened
    probe=json.loads(cli([rt.ffprobe,'-v','error','-select_streams','v:0','-show_frames','-show_streams','-of','json',result.path]));stream=probe['streams'][0]
    from fractions import Fraction
    assert len(probe['frames'])==10 and Fraction(stream['avg_frame_rate'])==25 and (stream['width'],stream['height'])==(160,96)
    times=[int(frame['best_effort_timestamp'])*Fraction(stream['time_base']) for frame in probe['frames']];assert times==[Fraction(i,25) for i in range(10)]
    assert len(cli([rt.ffmpeg,'-v','error','-i',result.path,'-map','0:v:0','-pix_fmt','rgb24','-f','rawvideo','-']))==10*160*96*3
    other=MediaItem.create(reopened.metadata);other.output.folder=str(tmp_path);other.output.filename='second';other.output.container='mp4';other.output.codec='h264'
    other.trim.enabled=True;other.trim.in_ms=80;other.trim.out_ms=280
    reexport=executor.execute(capture(other),ExportControl());assert reexport.output.is_file()
    reopened.guard.close();service.shutdown()


@pytest.mark.parametrize('codec,quality',[('h264',18),('prores',2),('prores',4)])
@pytest.mark.parametrize('depth',['16','float'])
def test_typed_sequence_to_video_has_real_import_path(tmp_path,codec,quality,depth):
    from tests.unit.test_import_export_parity import fixture
    from mediaforge.application.services.image_export import capture as image_capture
    rt=resolve_ffmpeg_runtime();runner,item,_=fixture(tmp_path,depth,False,True)
    item.output.kind='media';item.output.container='mov';item.output.codec=codec;item.output.prores_quality=quality;item.output.filename='movie'
    item.crop.enabled=True;item.crop.x=item.crop.y=0;item.crop.width=30;item.crop.height=18
    result=runner.execute(image_capture(item),ExportControl())
    imported=OpenMediaService(FfprobeMediaMetadataProbe(rt.ffprobe)).open_media(str(result.output));assert imported.succeeded,imported
    probe=json.loads(cli([rt.ffprobe,'-v','error','-show_streams','-show_frames','-select_streams','v:0','-of','json',result.output]))
    from fractions import Fraction
    assert Fraction(probe['streams'][0]['avg_frame_rate'])==Fraction(2997,125) and len(probe['frames'])==3
    assert (imported.metadata.width,imported.metadata.height)==(30,18)
    stream=probe['streams'][0]
    assert (stream.get('color_space'),stream.get('color_primaries'),stream.get('color_transfer'),stream.get('color_range'))==('bt709','bt709','iec61966-2-1','tv')
    runner._image_service.close_cache()


def test_mkv_same_source_result_is_importable_and_processable(video_input,tmp_path):
    rt,source=video_input;mkv=tmp_path/'original.mkv'
    cli([rt.ffmpeg,'-v','error','-n','-i',source,'-c:v','libx264','-c:a','aac',mkv])
    opener=OpenMediaService(FfprobeMediaMetadataProbe(rt.ffprobe));item=MediaItem.create(opener.open_media(str(mkv)).metadata)
    item.output.folder=str(tmp_path);item.output.filename='copy'
    executor=FfmpegExportExecutor(rt);service=ExportService(executor);opener._result_preparer=service
    assert service.start_request(capture(item));deadline=time.monotonic()+30
    while service.busy and time.monotonic()<deadline:time.sleep(.005)
    assert service.snapshot().state=='Completed',service.snapshot()
    result=service.completed_result();opened=opener.open_exported_result(result);assert opened.succeeded,opened
    assert Path(result.path).read_bytes()==mkv.read_bytes()
    other=MediaItem.create(opened.metadata);other.output.folder=str(tmp_path);other.output.filename='converted';other.output.container='mp4';other.output.codec='h264'
    assert executor.execute(capture(other),ExportControl()).output.is_file()
    opened.guard.close();service.shutdown()


@pytest.mark.parametrize('suffix,codec',[('avi','mjpeg'),('webm','libvpx-vp9')])
def test_additional_readable_same_source_copy_can_open_result(video_input,tmp_path,suffix,codec):
    rt,original=video_input;source=tmp_path/('source.'+suffix)
    cli([rt.ffmpeg,'-v','error','-n','-i',original,'-an','-c:v',codec,'-threads','1',source])
    opener=OpenMediaService(FfprobeMediaMetadataProbe(rt.ffprobe));opened=opener.open_media(str(source));assert opened.succeeded
    item=MediaItem.create(opened.metadata);item.output.folder=str(tmp_path);item.output.filename='copy'
    executor=FfmpegExportExecutor(rt);service=ExportService(executor);opener._result_preparer=service
    assert service.start_request(capture(item));deadline=time.monotonic()+30
    while service.busy and time.monotonic()<deadline:time.sleep(.005)
    assert service.snapshot().state=='Completed',service.snapshot()
    result=service.completed_result();assert result.kind=='video';candidate=opener.open_exported_result(result);assert candidate.succeeded,candidate
    assert Path(result.path).read_bytes()==source.read_bytes();candidate.guard.close();service.shutdown()


@pytest.mark.parametrize('speed,profile',[(s,p) for s in ['fast','balanced','quality'] for p in ['auto','main','high']])
def test_same_source_mkv_h264_encoded_settings_reopen(video_input,tmp_path,speed,profile):
    rt,source=video_input;mkv=tmp_path/'original.mkv'
    cli([rt.ffmpeg,'-v','error','-n','-i',source,'-c:v','libx264','-c:a','aac',mkv])
    # Same-as-Source exposes MKV H.264 encoding, not only byte-copy.
    test_exported_video_reopens_indexes_and_reprocesses((rt,mkv),tmp_path,'source','h264',18,speed,profile)
