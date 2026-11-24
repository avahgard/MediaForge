"""C3 extraction clock probes on tiny synthetic inputs; no Dub mux/export."""
import array,hashlib,json,math,os,wave
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from mediaforge.features.dub.contracts import DubControl,OutputMode
from mediaforge.features.dub.planning import output_plan
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.filesystem.dub_source import ProtectedDubSource
from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
from mediaforge.infrastructure.ffmpeg.speech import SpeechAudio
from mediaforge.infrastructure.ffmpeg.dub_probe import FfprobeDubInspector
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest,FfmpegRuntimeResolver
from mediaforge.application.contracts.local_ai import LocalAiError

pytestmark=[pytest.mark.native,pytest.mark.integration]

@pytest.mark.parametrize('kind',['aac-offset','prores-pcm','mkv-flac'])
def test_source_clock_pulses_survive_extraction(tmp_path,kind):
    root=os.environ.get('MEDIAFORGE_TEST_RUNTIME_ROOT')
    if not root:pytest.skip('Explicit pinned FFmpeg root required.')
    runtime=FfmpegRuntimeResolver(FfmpegManifest.load()).verify_directory(Path(root))
    paths=ProfilePaths.for_test(tmp_path/'Portable Юникод/Data/v2');paths.prepare();audio=SpeechAudio(paths,runtime)
    fixtures=paths.root/'fixtures';fixtures.mkdir();original=fixtures/'pulse.wav'
    raw=array.array('h')
    for n in range(6*48000):
        t=n/48000
        amplitude=6000*math.sin(2*math.pi*(440 if t<2 else 660)*t) if .5<=t<1.2 or 3<=t<3.8 else 0
        raw.extend((int(amplitude),int(amplitude)))
    with wave.open(str(original),'wb') as out:out.setnchannels(2);out.setsampwidth(2);out.setframerate(48000);out.writeframes(raw.tobytes())
    suffix={'aac-offset':'.mp4','prores-pcm':'.mov','mkv-flac':'.mkv'}[kind];source=fixtures/(kind+suffix)
    prefix=['-v','error','-f','lavfi','-i','testsrc2=size=96x64:rate=25:duration=6']
    if kind=='aac-offset':prefix+=['-itsoffset','0.2']
    prefix+=['-i',str(original),'-map','0:v:0','-map','1:a:0']
    if kind=='prores-pcm':prefix+=['-c:v','prores_ks','-profile:v','2','-pix_fmt','yuv422p10le','-c:a','pcm_s16le']
    else:
        prefix+=['-c:v','libx264','-profile:v','high','-pix_fmt','yuv420p','-c:a','aac' if kind=='aac-offset' else 'flac']
        if kind=='mkv-flac':prefix+=['-frame_size','4608']
    audio.pipe([*prefix,'-t','6',str(source)],DubControl(),maximum=0,timeout=30)
    descriptor=FfprobeDubInspector(runtime,paths).inspect(source,DubControl())
    plan=output_plan(descriptor,OutputMode.VIDEO_AUDIO_ONLY)
    assert plan.duration==Fraction(6) and plan.origin==0
    store=SpeechSessions(paths);control=DubControl();before=hashlib.sha256(source.read_bytes()).hexdigest()
    with ProtectedDubSource(source,control) as protected,store.open(NS(retry_session=None,retain_private_recovery=False),{},control) as session:
        path=session.name('extract.pcm');session.admit(6*16000*2)
        audio.extract(source,plan,path,control);session.track(path)
        pcm=array.array('h',path.read_bytes());assert len(pcm)==6*16000
        # Independent RMS pulse oracle, 5 ms blocks, 12 ms onset tolerance.
        # Smaller than one 48 kHz AAC priming frame; not an ASR/word-sync claim.
        active=[]
        for start in range(0,len(pcm),80):
            block=pcm[start:start+80]
            if math.sqrt(sum(int(v)**2 for v in block)/len(block))>=256:active.append(start/16000)
        expected=.7 if kind=='aac-offset' else .5
        assert active and abs(active[0]-expected)<=.012
        second=next(t for t in active if t>2)
        assert abs(second-(3.2 if kind=='aac-offset' else 3))<=.012
        assert not any(pcm[:int((expected-.04)*16000)])
        assert hashlib.sha256(source.read_bytes()).hexdigest()==before
        receipt=dict(kind=kind,source_sha256=before,origin=str(plan.origin),duration=str(plan.duration),
            samples=len(pcm),first_onset=active[0],second_onset=second,span_ack_required=plan.requires_span_acknowledgment,
            extraction_sha256=hashlib.sha256(pcm.tobytes()).hexdigest(),source_unchanged=True)
        track=next(t for t in descriptor.audio_tracks if t.index==plan.audio_index)
        try:
            ledger,units=audio.source_support(source,plan,track,path,control)
            receipt['source_support']=dict(ledger=ledger,units=len(units),
                qualification='waveform facts only; not VAD/ASR anchors')
        except LocalAiError as error:
            assert error.code=='speech_boundary_uncertain'
            receipt['source_support']=dict(refusal=error.code,detail=str(error))
        (fixtures/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    store.close(control);assert not list(paths.sessions.iterdir())
