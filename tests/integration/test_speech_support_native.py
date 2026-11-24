"""Small real precision controls plus bounded mechanical one-hour PCM clock.

No hour-long model/video qualification: the latter is solely a writer/clock test.
"""
import array,json,os,shutil,time
from fractions import Fraction as F
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from mediaforge.features.dub.contracts import DubControl
from mediaforge.features.dub.alignment import ceil,floor,anchored_fit
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest,FfmpegRuntimeResolver
from mediaforge.infrastructure.ffmpeg.speech import SpeechAudio

pytestmark=[pytest.mark.native,pytest.mark.integration]

@pytest.mark.parametrize('kind',['antiphase','quiet-f64'])
def test_real_source_support_is_not_downmix_or_quantized_silence(tmp_path,kind):
    ff=os.environ.get('MEDIAFORGE_TEST_RUNTIME_ROOT')
    if not ff:pytest.skip('Explicit managed FFmpeg required.')
    paths=ProfilePaths.for_test(tmp_path/'Precision Юникод/Data/v2');paths.prepare()
    audio=SpeechAudio(paths,FfmpegRuntimeResolver(FfmpegManifest.load()).verify_directory(Path(ff)))
    channels=2 if kind=='antiphase' else 1;value=.01 if channels==2 else 1e-12
    values=array.array('d')
    for i in range(32000):
        v=value if 4000<=i<12000 else 0.
        values.extend((v,-v) if channels==2 else (v,))
    raw=paths.temporary/'source.f64';raw.write_bytes(values.tobytes());source=paths.temporary/'source.wav'
    audio.pipe(['-v','error','-f','f64le','-ar','16000','-ac',str(channels),'-i',str(raw),'-c:a','pcm_f64le',str(source)],DubControl(),timeout=10)
    plan=NS(duration=F(2),origin=F(0),audio_index=0);track=NS(codec='pcm_f64le',sample_rate=16000,channels=channels)
    recognition=paths.temporary/'recognition.pcm';audio.extract(source,plan,recognition,DubControl())
    assert not any(recognition.read_bytes()) # neither fact is valid source-silence evidence
    ledger,support=audio.source_support(source,plan,track,recognition,DubControl())
    assert ledger['zeros']==[(0,4000),(12000,32000)]
    assert len(support)==1 and support[0].start==4000 and support[0].end==12000
    (tmp_path/'receipt.json').write_text(json.dumps(dict(case=kind,ledger=ledger,recognition_zero=True)),encoding='utf-8')

def test_mechanical_hour_absolute_clock_streams_and_retires_owned_pcm(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'Hour mechanical/Data/v2');paths.prepare()
    total=3600*48000
    assert shutil.disk_usage(paths.root).free>total*2+512*1024**2
    control=DubControl();store=SpeechSessions(paths);started=time.monotonic()
    events=[ceil(F((i*61+17)*16000*3,31)) for i in range(1800)]
    pulse=array.array('h',[777]*8).tobytes();req=NS(retry_session=None,retain_private_recovery=False)
    try:
        with store.open(req,dict(mechanical=True,not_c6=True),control) as session:
            with session.pcm('mechanical.pcm',total) as writer:
                position=0
                for start in events:
                    fit=anchored_fit(start,start+14400,8)
                    assert fit.ratio==1
                    writer.silence(start-position);writer.silence(960);writer.write(pulse);writer.silence(960)
                    position=start+1928
                writer.silence(total-position)
            path=session.path/'mechanical.pcm';assert path.stat().st_size==total*2
            with path.open('rb') as stream:
                position=0
                for start in events:
                    remaining=(start+960-position)*2
                    while remaining:
                        block=stream.read(min(32768,remaining));assert block and not any(block);remaining-=len(block)
                    assert stream.read(16)==pulse;position=start+968
                remaining=(total-position)*2
                while remaining:
                    block=stream.read(min(32768,remaining));assert block and not any(block);remaining-=len(block)
                assert not stream.read(1)
            name=session.path.name
        store.retire(name,control);assert not list(paths.sessions.iterdir())
    finally:store.close(control)
    (tmp_path/'receipt.json').write_text(json.dumps(dict(mechanical_only=True,not_c6=True,samples=total,
        events=len(events),first=events[0],last=events[-1],max_pcm_block=32768,seconds=time.monotonic()-started,retired=True)),encoding='utf-8')
