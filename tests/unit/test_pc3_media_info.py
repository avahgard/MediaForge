from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import os
import pytest
from mediaforge.application.contracts.media import MediaMetadataDto, MediaInfoFactsDto
from mediaforge.presentation.models.media_info import info_rows, codec_label, pixel_label, rate_label
from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe
from mediaforge.application.services.media import OpenMediaService


def metadata(path='C:/source.mov', family='mov,mp4,m4a,3gp,3g2,mj2', **facts):
    return MediaMetadataDto(Path(path).name,path,family,3.878875,'prores',1920,1080,24000/1001,
                            info_facts=MediaInfoFactsDto(**facts))


@pytest.mark.parametrize('path,family,long_name,expected',[
    ('C:/source.mov','mov,mp4','QuickTime / MOV','QuickTime / MOV'),
    ('C:/source.mp4','mov,mp4','QuickTime / MOV','MP4'),
    ('C:/source.mkv','matroska,webm','Matroska / WebM','Matroska / MKV'),
    ('C:/source.mov','matroska,webm','Matroska / WebM','Matroska / WebM'),
    ('C:/source.mov','matroska,webm',None,'Unknown'),
])
def test_container_requires_suffix_and_probed_family(path,family,long_name,expected):
    rows=info_rows(metadata(path,family,container_long_name=long_name),'93')
    assert next(r['value'] for r in rows if r['label']=='Container')==expected
    assert all(r['value']!=family for r in rows)


@pytest.mark.parametrize('profile,tag,expected',[
    ('Proxy','apco','Apple ProRes 422 Proxy'),('LT','apcs','Apple ProRes 422 LT'),
    ('Standard','apcn','Apple ProRes 422'),('422','apcn','Apple ProRes 422'),
    ('HQ','apch','Apple ProRes 422 HQ'),('4444','ap4h','Apple ProRes 4444'),
    ('XQ','ap4x','Apple ProRes 4444 XQ'),('HQ','ap4h','Apple ProRes'),
    (None,None,'Apple ProRes'),('mystery',None,'Apple ProRes'),(None,'ap4x','Apple ProRes 4444 XQ'),
])
def test_prores_profile_evidence_and_conflict(profile,tag,expected):
    assert codec_label('prores','raw long name',profile,tag)==expected
    assert codec_label('other','Codec long name',profile,tag)=='Codec long name'


@pytest.mark.parametrize('pixel,bits,expected',[
    ('yuv420p',None,'YUV 4:2:0 8-bit'),('yuv420p10le',None,'YUV 4:2:0 10-bit'),
    ('yuv422p10le',None,'YUV 4:2:2 10-bit'),('yuv444p12le',None,'YUV 4:4:4 12-bit'),
    ('yuva444p12le',None,'YUVA 4:4:4 12-bit'),('gbrp10le',None,'RGB 10-bit'),
    ('rgba',None,'RGBA 8-bit'),('yuv444p10le',12,'YUV 4:4:4 12-bit'),
    ('unknown_token',None,'unknown_token'),('unknown_token',10,'unknown_token 10-bit'),
    (None,None,'Unknown')])
def test_pixel_characteristics_are_fact_based(pixel,bits,expected):
    assert pixel_label(MediaInfoFactsDto(pixel_format=pixel,bit_depth=bits))==expected


@pytest.mark.parametrize('frames',['93','Indexing…','Unavailable'])
def test_rows_exact_frame_state_and_audio_facts(frames):
    m=metadata(video_profile='4444',video_tag='ap4h',pixel_format='yuv444p12le',frame_rate_ratio='24000/1001',
               audio_codec='aac',audio_sample_rate=48000,audio_channels=2,audio_layout='stereo')
    rows=info_rows(m,frames)
    values={r['label']:r['value'] for r in rows if not r['section']}
    expected = frames if frames == '93' else '≈93 (duration × frame rate; unverified)'
    assert values['Frames']==expected and values['Duration']=='00:00:03.879'
    assert values['Frame rate']=='23.98 fps'
    assert values['Sample rate']=='48 kHz' and values['Channels']=='Stereo / 2'
    assert rows[-3]['value']=='AAC'
    missing=info_rows(replace(m,info_facts=None),frames)
    assert missing[-1]['label']=='None' and next(r['value'] for r in missing if r['label']=='Frame rate')=='Unknown'


def test_existing_probe_and_application_preserve_narrow_optional_facts(tmp_path):
    source=tmp_path/'Пример с пробелами.mov';source.write_bytes(b'public')
    payload={'format':{'format_name':'mov,mp4','format_long_name':'QuickTime / MOV','duration':'1'},
             'streams':[{'codec_type':'video','codec_name':'prores','profile':'4444','codec_tag_string':'ap4h',
                         'width':64,'height':48,'pix_fmt':'yuv444p12le','bits_per_raw_sample':'12','avg_frame_rate':'24000/1001'},
                        {'codec_type':'audio','codec_name':'pcm_s16le','sample_rate':'44100','channels':2,'channel_layout':'stereo'}]}
    raw=FfprobeMediaMetadataProbe._parse(payload,source)
    class Probe:
        def probe(self,p): assert p==source;return raw
    result=OpenMediaService(Probe()).open_media(str(source))
    facts=result.metadata.info_facts
    assert facts.bit_depth==12 and facts.frame_rate_ratio=='24000/1001'
    assert facts.audio_sample_rate==44100 and facts.audio_channels==2
    assert facts.audio_codec=='pcm_s16le'
    assert rate_label('0/0')=='Unknown'
    assert rate_label('7830000/130541', 'average') == '59.98 fps (average)'
    assert rate_label('59999/1000') == '≈60 fps'


def test_info_path_normalizes_source_segments_without_selecting_another_source():
    source=os.path.abspath(os.path.join('Папка с пробелами','..','источник.mov'))
    path=os.path.join(os.path.dirname(source),'temporary','..',os.path.basename(source))
    rows=info_rows(metadata(path),'Unavailable')
    assert next(r['value'] for r in rows if r['label']=='Path')==source
    assert next(r['value'] for r in rows if r['label']=='File')==os.path.basename(source)


@pytest.mark.integration
@pytest.mark.skipif(sys.platform!='win32',reason='Authenticated Windows managed runtime')
@pytest.mark.parametrize('profile,label',[(0,'422 Proxy'),(1,'422 LT'),(2,'422'),(3,'422 HQ'),(4,'4444'),(5,'4444 XQ')])
def test_managed_prores_variants_have_evidence_based_info(tmp_path,profile,label):
    from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
    from mediaforge.infrastructure.ffmpeg.source_timeline import FfprobeTimelineIndexer
    from mediaforge.application.contracts.source_timeline import SourceSignature,TimelineRequest,TimelineBuildControl
    runtime=resolve_ffmpeg_runtime();source=tmp_path/f'public ProRes {profile}.mov'
    generated=subprocess.run([str(runtime.ffmpeg),'-hide_banner','-loglevel','error','-f','lavfi',
                              '-i','testsrc2=size=64x48:rate=25:duration=0.15','-an','-c:v','prores_ks',
                              '-profile:v',str(profile),'-pix_fmt','yuv422p10le' if profile<4 else 'yuv444p10le',str(source)],
                             capture_output=True,timeout=60,shell=False)
    assert generated.returncode==0,generated.stderr
    metadata=OpenMediaService(FfprobeMediaMetadataProbe(runtime.ffprobe)).open_media(str(source)).metadata
    assert metadata is not None
    control=TimelineBuildControl()
    built=FfprobeTimelineIndexer(runtime.ffprobe).build(TimelineRequest('public',SourceSignature.capture(source),1),control)
    assert built.timeline is not None and built.timeline.count==4 and not control.owns_process
    rows=info_rows(metadata,str(built.timeline.count))
    assert next(r['value'] for r in rows if r['label']=='Codec')=='Apple ProRes '+label
    assert next(r['value'] for r in rows if r['label']=='Container')=='QuickTime / MOV'
    assert next(r['value'] for r in rows if r['label']=='Frames')=='4'
    pixel=next(r['value'] for r in rows if r['label']=='Pixel format')
    assert pixel.startswith('YUV 4:2:2' if profile<4 else 'YUV 4:4:4')
    if metadata.info_facts.bit_depth: assert pixel.endswith(f'{metadata.info_facts.bit_depth}-bit')
    assert rows[-1]['label']=='None'
