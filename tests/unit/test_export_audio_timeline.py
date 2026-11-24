"""Selected-stream duration validation, common-origin intersection and unknown timing."""
import hashlib,json,struct,subprocess
from fractions import Fraction
import pytest
from mediaforge.application.services.export import ExportService, capture, summary
from mediaforge.application.services.media import OpenMediaService
from mediaforge.application.state.workspace import MediaWorkspaceSession
from mediaforge.features.media.export import ExportControl,ExportError
from mediaforge.infrastructure.ffmpeg.export import FfmpegExportExecutor,_audio_span
from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime


def cli(args):
    return subprocess.run(list(map(str,args)),capture_output=True,check=True,timeout=30,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout


@pytest.fixture(scope='module')
def audio_sources(tmp_path_factory):
    root=tmp_path_factory.mktemp('selected audio');runtime=resolve_ffmpeg_runtime()
    for duration in ('0.4','1.6'):
        path=root/(duration+'.mp4')
        cli([runtime.ffmpeg,'-v','error','-n','-f','lavfi','-i','color=s=160x120:r=25:d=1.2',
             '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=1.2',
             '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration='+duration,
             '-map','0:v','-map','1:a','-map','2:a','-c:v','libx264','-c:a','aac',path])
    return root,runtime


def opened(source,runtime,tmp_path):
    metadata=OpenMediaService(FfprobeMediaMetadataProbe(runtime.ffprobe)).open_media(str(source)).metadata
    session=MediaWorkspaceSession();item=session.add(metadata)[0]
    item.output.folder=str(tmp_path);item.output.container='audio';item.output.audio_stream=1
    return item


def probe(runtime,path):return json.loads(cli([runtime.ffprobe,'-v','error','-show_streams','-show_format','-of','json',path]))


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('extension,encoder', [('mp4', 'aac'), ('wav', 'pcm_s16le'), ('mp3', 'libmp3lame')])
def test_audio_only_open_and_export_admission_agree(tmp_path, extension, encoder):
    runtime = resolve_ffmpeg_runtime()
    source = tmp_path/f'Только звук.{extension}'
    cli([runtime.ffmpeg, '-v', 'error', '-n', '-f', 'lavfi', '-i', 'sine=sample_rate=48000:duration=0.4',
         '-c:a', encoder, source])
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    item = opened(source, runtime, tmp_path)
    assert item.metadata.audio_only and item.metadata.duration_seconds > 0
    item.output.audio_stream = 0
    item.output.audio_format = 'mp3' if extension == 'wav' else 'wav'
    item.output.filename = 'Converted'
    adapter = FfmpegExportExecutor(runtime)
    service = ExportService(adapter)
    if encoder == 'aac':
        assert summary(item)['code'] == 'unsupported_audio_source'
        commands = []
        adapter._processes._factory = lambda command, **kw: commands.append(command)
        assert not service.start(item) and not service.busy
        assert service.snapshot().error == 'unsupported_audio_source'
        assert 'PCM 16-bit WAV or MP3' in service.snapshot().message
        assert not commands and list(tmp_path.iterdir()) == [source]
    else:
        assert summary(item)['valid']
        result = adapter.execute(service.prepare(item), ExportControl())
        streams = probe(runtime, result.output)['streams']
        assert len(streams) == 1 and streams[0]['codec_name'] == ('mp3' if extension == 'wav' else 'pcm_s16le')
    service.shutdown()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


@pytest.mark.integration
@pytest.mark.parametrize('length,format,trim', [('0.4','wav',None),('0.4','mp3',None),('1.6','wav',None),('1.6','mp3',None),
        ('0.4','wav',(200,800)),('0.4','mp3',(200,800)),('1.6','wav',(800,1400)),('1.6','mp3',(800,1400))])
def test_selected_audio_own_duration_and_trim(audio_sources,tmp_path,length,format,trim):
    root,runtime=audio_sources;source=root/(length+'.mp4');item=opened(source,runtime,tmp_path)
    assert len(item.metadata.audio_streams)==2
    item.output.audio_format=format
    if trim:item.trim.enabled=True;item.trim.in_ms,item.trim.out_ms=trim
    request=capture(item);digest=hashlib.sha256(source.read_bytes()).hexdigest()
    meta=probe(runtime,source);selected=meta['streams'][2]
    assert Fraction(selected['duration_ts'])*Fraction(selected['time_base'])==Fraction(length)
    assert Fraction(meta['streams'][0]['duration'])==Fraction('1.2')
    expected=_audio_span(request,meta['streams'][0],meta['streams'])
    result=FfmpegExportExecutor(runtime).execute(request,ExportControl()).output
    actual=probe(runtime,result);assert len(actual['streams'])==1
    assert actual['streams'][0]['codec_name']==('pcm_s16le' if format=='wav' else 'mp3')
    ref=[runtime.ffmpeg,'-v','error','-i',source,'-map','0:a:1']
    if trim:ref+=['-af',f'atrim=start={trim[0]/1000}:end={trim[1]/1000}']
    reference=cli(ref+['-f','s16le','-']);decoded=cli([runtime.ffmpeg,'-v','error','-i',result,'-f','s16le','-'])
    assert len(decoded)==len(reference)  # No padding to the unrelated video duration.
    samples=struct.unpack('<'+'h'*(len(decoded)//2),decoded)
    measured=len(samples)/48000
    assert abs(measured-float(expected))<=1024/48000  # Source AAC's final decode quantum only.
    crossings=sum(a<=0<b for a,b in zip(samples,samples[1:]))
    assert abs(crossings/measured-880)<8  # Chosen non-first stream, not the 440 Hz first stream.
    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
    (tmp_path/'audio-timeline-proof.json').write_text(json.dumps(dict(source=meta,output=actual,
        requested_trim=trim,expected_selected_duration=str(expected),decoded_samples=len(samples),
        reference_samples=len(reference)//2,frequency=crossings/measured,source_sha256=digest),indent=2),encoding='utf-8')


@pytest.mark.integration
@pytest.mark.parametrize('fault',['missing','conflicting','nan','empty','missing_start'])
def test_uncertain_or_empty_audio_fails_before_encode(audio_sources,tmp_path,fault):
    root,runtime=audio_sources;source=root/'0.4.mp4';item=opened(source,runtime,tmp_path)
    if fault in ('empty','missing_start'):
        item.trim.enabled=True;item.trim.in_ms,item.trim.out_ms=(600,900) if fault=='empty' else (0,300)
    request=capture(item);adapter=FfmpegExportExecutor(runtime);original=adapter._probe;commands=[]
    def altered(path,*args,**kw):
        payload,video,count=original(path,*args,**kw)
        if path==source:
            audio=payload['streams'][2]
            if fault=='missing':audio.pop('duration',None);audio.pop('duration_ts',None)
            if fault=='conflicting':audio['duration']='3.5'
            if fault=='nan':audio.pop('duration_ts',None);audio['duration']='NaN'
            if fault=='missing_start':audio.pop('start_pts',None);audio.pop('start_time',None)
        return payload,video,count
    adapter._probe=altered;factory=adapter._processes._factory
    def observed(args,**kw):commands.append(args);return factory(args,**kw)
    adapter._processes._factory=observed
    with pytest.raises(ExportError) as caught:adapter.execute(request,ExportControl())
    assert caught.value.code==('empty_audio_range' if fault=='empty' else 'audio_timing_unavailable')
    assert not any('-af' in c for c in commands)
    assert not request.destination.exists() and not list(tmp_path.glob('.mediaforge-export-*'))


def test_duration_fallback_and_validation_stay_specific(tmp_path):
    from tests.unit.test_export_plan import item_at
    from dataclasses import replace
    _,item=item_at(tmp_path);request=replace(capture(item),codec='pcm_s16le',destination=tmp_path/'x.wav')
    source={'streams':[{'codec_type':'video','start_pts':0,'time_base':'1/25'},
        {'codec_type':'audio','codec_name':'aac','sample_rate':'48000','channels':1,'start_time':'0','duration':'0.4'}]}
    payload={'streams':[{'codec_type':'audio','codec_name':'pcm_s16le','sample_rate':'48000','channels':1}],
             'format':{'format_name':'wav','duration':'0.4'}}
    assert _audio_span(request,source['streams'][0],source['streams'])==Fraction(2,5)
    FfmpegExportExecutor._validate_audio(request,source,payload)
    for changed in ({'channels':0},{'sample_rate':'0'},{'codec_name':'aac'}):
        invalid={'streams':[payload['streams'][0]|changed],'format':payload['format']}
        with pytest.raises(ExportError,match='not published'):FfmpegExportExecutor._validate_audio(request,source,invalid)
    for duration in ('0','NaN','1.2'):
        invalid={'streams':payload['streams'],'format':payload['format']|{'duration':duration}}
        with pytest.raises(ExportError,match='not published'):FfmpegExportExecutor._validate_audio(request,source,invalid)
