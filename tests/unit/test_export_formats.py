"""Bounded real video profile, selected audio and combined exact-PTS checks."""
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import pytest

from mediaforge.application.services.export import capture
from mediaforge.application.services.export import summary
from mediaforge.application.services.media import OpenMediaService
from mediaforge.application.state.workspace import MediaWorkspaceSession
from mediaforge.features.media.export import ExportControl, output_aspect
from mediaforge.features.media.export import ExportError
from mediaforge.infrastructure.ffmpeg.export import FfmpegExportExecutor
from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
from tests.unit.test_export_timestamps import timestamp_sources


def cli(args):
    return subprocess.run(list(map(str,args)),capture_output=True,check=True,timeout=30,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout


@pytest.fixture(scope='module')
def format_source(tmp_path_factory):
    root=tmp_path_factory.mktemp('formats');runtime=resolve_ffmpeg_runtime()
    source=root/'two audio Кириллица.mp4'
    cli([runtime.ffmpeg,'-v','error','-n','-f','lavfi','-i','testsrc2=s=160x120:r=25:d=1.2',
         '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=1.2',
         '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=1.2',
         '-map','0:v','-map','1:a','-map','2:a','-c:v','libx264','-c:a','aac',source])
    return source,runtime


def opened(source,runtime,tmp_path):
    metadata=OpenMediaService(FfprobeMediaMetadataProbe(runtime.ffprobe)).open_media(str(source)).metadata
    assert metadata is not None
    session=MediaWorkspaceSession();item=session.add(metadata)[0]
    item.output.folder=str(tmp_path);item.output.filename='результат with spaces'
    return session,item


def probe(runtime,path):
    return json.loads(cli([runtime.ffprobe,'-v','error','-show_streams','-show_format','-of','json',path]))


@pytest.mark.integration
@pytest.mark.parametrize('container,codec,profile', [('mp4','h264','main'),('mov','h264','high'),
        *[('mov','prores',str(n)) for n in range(4)]])
def test_real_video_profiles_and_common_origin(format_source,tmp_path,container,codec,profile):
    source,runtime=format_source;session,item=opened(source,runtime,tmp_path)
    original=hashlib.sha256(source.read_bytes()).hexdigest()
    item.output.container=container;item.output.codec=codec
    item.output.h264_profile=profile if codec=='h264' else 'auto'
    item.output.prores_quality=int(profile) if codec=='prores' else 2
    item.output.speed='fast';item.output.h264_quality=21
    item.trim.enabled=True;item.trim.in_ms=410;item.trim.out_ms=810
    item.crop.enabled=True;item.crop.x=40;item.crop.y=20;item.crop.width=item.crop.height=80
    item.resize.enabled=True;item.resize.mode='format';item.resize.width=160;item.resize.height=90
    item.resize.pixel_aspect='0.75';item.resize.placement='fit'
    output=FfmpegExportExecutor(runtime).execute(capture(item),ExportControl()).output
    metadata=probe(runtime,output);video=metadata['streams'][0]
    assert (video['width'],video['height'],video['sample_aspect_ratio'],video['display_aspect_ratio'])==(160,90,'3:4','4:3')
    expected_profile=({'main':'Main','high':'High'}[profile] if codec=='h264' else ['Proxy','LT','Standard','HQ'][int(profile)])
    assert video['profile']==expected_profile
    assert video['pix_fmt']==('yuv420p' if codec=='h264' else 'yuv422p10le')
    frames=json.loads(cli([runtime.ffprobe,'-v','error','-select_streams','v:0','-show_frames','-of','json',output]))['frames']
    actual=[Fraction(f['best_effort_timestamp'])*Fraction(video['time_base']) for f in frames]
    assert actual==[Fraction(30+40*n,1000) for n in range(10)]
    assert len(metadata['streams'])==3
    for index in range(2):
        audio=metadata['streams'][index+1]
        af=json.loads(cli([runtime.ffprobe,'-v','error','-select_streams',f'a:{index}','-show_frames','-of','json',output]))['frames']
        assert Fraction(af[0]['best_effort_timestamp'])*Fraction(audio['time_base'])==0
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original
    (tmp_path/'proof.json').write_text(json.dumps({'ffprobe':metadata,'pts':[str(t) for t in actual],
        'expected':[str(Fraction(30+40*n,1000)) for n in range(10)],'source_sha256':original},indent=2),encoding='utf-8')


@pytest.mark.integration
@pytest.mark.parametrize('audio_format',['wav','mp3'])
def test_audio_only_second_stream_real_waveform(format_source,tmp_path,audio_format):
    source,runtime=format_source;session,item=opened(source,runtime,tmp_path)
    assert len(item.metadata.audio_streams)==2
    item.output.container='audio';item.output.audio_format=audio_format;item.output.audio_stream=1;item.output.audio_bitrate=256
    item.trim.enabled=True;item.trim.in_ms=410;item.trim.out_ms=810
    # Irrelevant invalid picture settings cannot prevent audio extraction.
    item.resize.enabled=True;item.resize.error='invalid geometry';item.crop.enabled=True;item.crop.width=0
    commands=[]
    def factory(args,**kwargs):commands.append(args);return subprocess.Popen(args,**kwargs)
    output=FfmpegExportExecutor(runtime,process_factory=factory).execute(capture(item),ExportControl()).output
    metadata=probe(runtime,output);audio=metadata['streams'][0]
    assert len(metadata['streams'])==1 and audio['codec_type']=='audio'
    assert audio['codec_name']==('pcm_s16le' if audio_format=='wav' else 'mp3')
    assert (audio['channels'],audio['sample_rate'])==(1,'48000')
    pcm=cli([runtime.ffmpeg,'-v','error','-i',output,'-f','s16le','-'])
    samples=struct.unpack('<'+'h'*(len(pcm)//2),pcm)
    assert len(samples)==19200  # The exact selected 400ms interval, including MP3 skip/padding.
    crossings=sum(a<=0<b for a,b in zip(samples,samples[1:]))
    assert abs(crossings/.4-880)<8  # Selected second track, not the 440 Hz default.
    assert not any('-vf' in c or '-c:v' in c for c in commands)
    (tmp_path/'proof.json').write_text(json.dumps({'ffprobe':metadata,'samples':len(samples),'frequency':crossings/.4,
        'commands':commands},indent=2,ensure_ascii=False),encoding='utf-8')


def test_pixel_aspect_bounded_exact():
    for value,expected in [('1.0',Fraction(1)),('1.5',Fraction(3,2)),('0.75',Fraction(3,4)),('4/3',Fraction(4,3))]:
        assert output_aspect(value,1024,768,'1:1')==expected
    assert output_aspect('16:9',160,120,'1:1')==Fraction(4,3)
    assert output_aspect('16:9',160,90,'1:1')==1
    for value in ('0','-1','NaN','1/0','1.333333','1/65536'):
        with pytest.raises(ValueError): output_aspect(value,1024,768,'1:1')


@pytest.mark.integration
def test_audio_only_silent_source_explains_unavailable(format_source,tmp_path):
    source,runtime=format_source
    silent=tmp_path/'silent.mp4'
    cli([runtime.ffmpeg,'-v','error','-n','-i',source,'-map','0:v:0','-an','-c:v','copy',silent])
    _,item=opened(silent,runtime,tmp_path)
    assert not item.metadata.audio_streams
    item.output.container='audio'
    assert not summary(item)['valid'] and 'no audio' in summary(item)['message']
    with pytest.raises(ExportError,match='no audio'): capture(item)


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('outcome',['success','failed_encoder','cancelled_encoder'])
def test_real_confirmed_replacement_lifecycle(format_source,tmp_path,outcome):
    from threading import Event
    import time
    from mediaforge.application.services.export import ExportService
    source,runtime=format_source;_,item=opened(source,runtime,tmp_path)
    item.output.codec='h264';target=capture(item).destination;target.write_bytes(b'previous export')
    digest=hashlib.sha256(source.read_bytes()).hexdigest();entered=Event();children=[]
    def factory(args,**kwargs):
        args=list(args)
        if '-vf' in args:
            if outcome=='failed_encoder': args[args.index('-vf')+1]='invalid_task010_final_filter'
            if outcome=='cancelled_encoder': args[args.index('-i'):args.index('-i')]=['-re']
        process=subprocess.Popen(args,**kwargs);children.append(process)
        if '-vf' in args: entered.set()
        return process
    service=ExportService(FfmpegExportExecutor(runtime,process_factory=factory))
    request=service.prepare(item, allow_replacement=True)
    assert request.replacement is not None and service.start_request(request)
    if outcome=='cancelled_encoder':
        assert entered.wait(5) and service.cancel()
    end=time.monotonic()+10
    while service.busy and time.monotonic()<end: time.sleep(.01)
    assert not service.busy
    job=service.snapshot();service.shutdown()
    assert job.state=={'success':'Completed','failed_encoder':'Failed','cancelled_encoder':'Cancelled'}[outcome]
    if outcome=='success': assert probe(runtime,target)['streams'][0]['codec_name']=='h264'
    else: assert target.read_bytes()==b'previous export' and not job.result
    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
    assert all(p.poll() is not None for p in children)
    assert not list(tmp_path.glob('.mediaforge-export-*'))
    (tmp_path/'replacement-proof.json').write_text(json.dumps(dict(outcome=outcome,state=job.state,error=job.error,
        source_sha256=digest,children_reaped=len(children),published=bool(job.result)),indent=2),encoding='utf-8')


@pytest.mark.integration
@pytest.mark.parametrize('speed,profile,crf',[('fast','main',24),('quality','high',18)])
def test_new_speed_profile_choices_preserve_irregular_vfr_pts(timestamp_sources,tmp_path,speed,profile,crf):
    root,runtime=timestamp_sources;source=root/'vfr.mp4';_,item=opened(source,runtime,tmp_path)
    item.trim.enabled=True;item.trim.in_ms=47;item.trim.out_ms=640
    item.output.codec='h264';item.output.container='mp4';item.output.speed=speed
    item.output.h264_profile=profile;item.output.h264_quality=crf
    def timestamps(path):
        v=next(s for s in probe(runtime,path)['streams'] if s['codec_type']=='video')
        frames=json.loads(cli([runtime.ffprobe,'-v','error','-select_streams','v:0','-show_frames','-of','json',path]))['frames']
        return [Fraction(f['best_effort_timestamp'])*Fraction(v['time_base']) for f in frames]
    expected=[t-Fraction(47,1000) for t in timestamps(source) if Fraction(47,1000)<=t<Fraction(640,1000)]
    output=FfmpegExportExecutor(runtime).execute(capture(item),ExportControl()).output
    actual=timestamps(output)
    assert actual==expected and len(actual)==15
    assert set(actual[i]-actual[i-1] for i in range(1,len(actual)))=={Fraction(47,1000),Fraction(26,1000)}
    (tmp_path/'vfr-speed-proof.json').write_text(json.dumps(dict(speed=speed,profile=profile,crf=crf,
        expected=list(map(str,expected)),actual=list(map(str,actual))),indent=2),encoding='utf-8')
