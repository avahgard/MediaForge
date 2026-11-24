import hashlib,json,subprocess
from decimal import Decimal
from fractions import Fraction
import pytest
from tests.unit.test_export_timestamps import timestamp_sources
from mediaforge.application.services.export import capture
from mediaforge.application.services.media import OpenMediaService
from mediaforge.application.state.workspace import MediaWorkspaceSession
from mediaforge.features.media.export import ExportControl
from mediaforge.infrastructure.ffmpeg.export import FfmpegExportExecutor
from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe


@pytest.mark.integration
@pytest.mark.parametrize('kind,profile,tag',[('cfr',4,'ap4h'),('vfr',5,'ap4x')])
def test_opaque_prores4444_profiles_picture_geometry_and_exact_pts(timestamp_sources,tmp_path,kind,profile,tag):
    root,runtime=timestamp_sources;source=root/(kind+'.mp4');commands=[]
    def cli(args):
        return subprocess.run(list(map(str,args)),capture_output=True,check=True,timeout=25).stdout
    def probe(path,frames=False):
        return json.loads(cli([runtime.ffprobe,'-v','error','-of','json',
            *(['-select_streams','v:0','-show_frames'] if frames else ['-show_streams','-show_format']),path]))
    metadata=OpenMediaService(FfprobeMediaMetadataProbe(runtime.ffprobe)).open_media(str(source)).metadata
    session=MediaWorkspaceSession();item=session.add(metadata)[0]
    item.output.container='mov';item.output.codec='prores';item.output.prores_quality=profile
    item.output.folder=str(tmp_path)
    item.trim.enabled=True;item.trim.in_ms,item.trim.out_ms=(410,810) if kind=='cfr' else (47,640)
    assert session.set_crop(40,20,80,80);item.crop.enabled=True;item.resize.enabled=True
    assert session.set_reformat('mode','format') and session.set_reformat('preset','Custom')
    assert session.set_reformat('width','96') and session.set_reformat('height','72')
    assert session.set_reformat('placement','stretch') and session.set_reformat('pixel_aspect','4/3')
    request=capture(item);digest=hashlib.sha256(source.read_bytes()).hexdigest()
    def factory(args,**kwargs):commands.append(args);return subprocess.Popen(args,**kwargs)
    output=FfmpegExportExecutor(runtime,process_factory=factory).execute(request,ExportControl()).output
    src=probe(source);actual=probe(output);v=actual['streams'][0]
    (tmp_path/'probe-initial.json').write_text(json.dumps(actual,indent=2),encoding='utf-8')
    assert v['codec_name']=='prores' and v['codec_tag_string']==tag
    assert v['profile']==('4444' if profile==4 else 'XQ')
    assert v['pix_fmt']=='yuv444p12le' and v['bits_per_raw_sample']=='12'
    assert (v['width'],v['height'],v['sample_aspect_ratio'],v['display_aspect_ratio'])==(96,72,'4:3','16:9')
    assert 'mov' in actual['format']['format_name'].split(',')
    def times(path,video):return [Fraction(f['best_effort_timestamp'])*Fraction(video['time_base']) for f in probe(path,True)['frames']]
    video=src['streams'][0];origin=Fraction(video.get('start_pts',0))*Fraction(video['time_base'])
    start,end=(origin+Fraction(x,1000) for x in request.trim)
    expected=[t-start for t in times(source,video) if start<=t<end]
    assert times(output,v)==expected and len(expected)==(10 if kind=='cfr' else 15)
    reference=cli([runtime.ffmpeg,'-v','error','-copyts','-i',source,'-vf',
        f'trim=start={Decimal(start.numerator)/Decimal(start.denominator)}:end={Decimal(end.numerator)/Decimal(end.denominator)},crop=80:80:40:20:exact=1,scale=96:72:flags=lanczos,format=rgb24',
        '-frames:v','1','-f','rawvideo','-'])
    decoded=cli([runtime.ffmpeg,'-v','error','-i',output,'-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'])
    assert len(reference)==len(decoded)==96*72*3
    mae=sum(abs(a-b) for a,b in zip(reference,decoded))/len(reference)
    assert mae<8  # Independent decode of the source + requested transform, not a size-only check.
    assert all(s['codec_name']=='aac' for s in actual['streams'][1:]) and len(actual['streams'])==2
    encode=next(c for c in commands if '-vf' in c)
    assert encode[encode.index('-pix_fmt')+1]=='yuv444p10le' and encode[encode.index('-alpha_bits')+1]=='0'
    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
    (tmp_path/'prores-proof.json').write_text(json.dumps(dict(source=src,output=actual,commands=commands,
        expected_pts=[str(t) for t in expected],actual_pts=[str(t) for t in times(output,v)],
        rgb_mae=mae,source_sha256=digest,alpha='omitted; opaque output only'),indent=2),encoding='utf-8')


@pytest.mark.integration
def test_alpha_rgb_video_is_rejected_with_specific_limitation(tmp_path):
    from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
    from mediaforge.application.services.export import summary
    from mediaforge.features.media.export import ExportError
    runtime=resolve_ffmpeg_runtime();source=tmp_path/'alpha video.mkv'
    def cli(args):return subprocess.run(list(map(str,args)),capture_output=True,check=True,timeout=20).stdout
    cli([runtime.ffmpeg,'-v','error','-n','-f','lavfi','-i','color=red@0.25:s=16x16:r=25:d=0.04,format=rgba',
         '-c:v','ffv1',source])
    metadata=OpenMediaService(FfprobeMediaMetadataProbe(runtime.ffprobe)).open_media(str(source)).metadata
    item=MediaWorkspaceSession().add(metadata)[0];item.output.container='mov';item.output.codec='prores';item.output.prores_quality=4
    assert 'alpha omitted' in summary(item)['message']
    before=cli([runtime.ffmpeg,'-v','error','-i',source,'-pix_fmt','rgba','-f','rawvideo','-'])
    assert len(before)==16*16*4 and set(before[3::4])=={63}
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    request=capture(item)
    with pytest.raises(ExportError,match='RGB/alpha input') as caught:
        FfmpegExportExecutor(runtime).execute(request,ExportControl())
    assert caught.value.code=='unsupported_color' and not request.destination.exists()
    assert not list(tmp_path.glob('.mediaforge-export-*'))
    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
    (tmp_path/'alpha-policy.json').write_text(json.dumps(dict(source_alpha=63,error=caught.value.code,
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),output_published=False,
        policy=summary(item)['message']),indent=2),encoding='utf-8')


def test_4444_validation_rejects_wrong_profile_chroma_or_depth(tmp_path):
    from tests.unit.test_export_plan import item_at
    from mediaforge.features.media.export import ExportError
    _,item=item_at(tmp_path);item.output.container='mov';item.output.codec='prores';item.output.prores_quality=4
    request=capture(item)
    v=dict(width=1024,height=768,sample_aspect_ratio='1:1',display_aspect_ratio='4:3',codec_name='prores',
        codec_tag_string='ap4h',profile='4444',pix_fmt='yuv444p12le',bits_per_raw_sample='12',
        time_base='1/1000',duration='1.0')
    payload=dict(streams=[v],format={'format_name':'mov'})
    FfmpegExportExecutor._validate(request,v,0,payload,v,0,Fraction(1,1000))
    for wrong in ({'profile':'Standard'},{'codec_tag_string':'apch'},{'pix_fmt':'yuv422p10le'},{'bits_per_raw_sample':'10'}):
        with pytest.raises(ExportError,match='differs from the selected preset'):
            FfmpegExportExecutor._validate(request,v,0,payload,v|wrong,0,Fraction(1,1000))
