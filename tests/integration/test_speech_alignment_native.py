"""Real amended A1/A3/A2 controls; annotations are test oracles, never inputs."""
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
import array,hashlib,json,os,sys,wave
import pytest
from mediaforge.application.contracts.speech import SpeechRequest
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.bootstrap.speech import compose_dub_local_ai,compose_speech
from mediaforge.features.dub.contracts import DubControl,OutputMode
from mediaforge.features.dub.planning import output_plan
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.ffmpeg.dub_probe import FfprobeDubInspector
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest,FfmpegRuntimeResolver
from mediaforge.infrastructure.models.providers import WorkerRuntime
from mediaforge.infrastructure.models.registry import development_registry

pytestmark=[pytest.mark.native,pytest.mark.integration]
ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture(scope='module')
def native(tmp_path_factory):
    root=os.environ.get('MEDIAFORGE_TEST_AI_ROOT');ff=os.environ.get('MEDIAFORGE_TEST_RUNTIME_ROOT');source=os.environ.get('MEDIAFORGE_TEST_C3_SPARSE_SOURCE')
    if not root or not ff or not source:pytest.skip('Explicit existing authenticated models/FFmpeg/retained source required.')
    paths=ProfilePaths.for_test(Path(root));specs=development_registry(ROOT/'external/manifests/dub-c2-development.json',
        'e95373ca537ac2139eb47ce37113122d45eeb679c26fa416d39a830322f81e2a',allow_development=True)
    runtime=WorkerRuntime(Path(sys._base_executable),paths.root/'runtime/site',paths.manifests/'runtime.json',specs[0].runtime_fingerprint)
    service=compose_speech(compose_dub_local_ai(paths,runtime,MediaProcessingGate(),specs=specs),
        FfmpegRuntimeResolver(FfmpegManifest.load()).verify_directory(Path(ff)))
    source=Path(source);assert hashlib.sha256(source.read_bytes()).hexdigest()=='9d1b50d077ed0485920b439eee64c06c63bd9c834057b2cbd4fa51ff0158fe0d'
    with wave.open(str(source),'rb') as stream:raw=stream.readframes(stream.getnframes())
    directory=tmp_path_factory.mktemp('Annotated Юникод source');cases=[]
    yield service,source,raw,directory,cases
    service.sessions.close(DubControl())
    receipt=os.environ.get('MEDIAFORGE_TEST_ALIGNMENT_RECEIPT')
    if receipt:
        with Path(receipt).open('x',encoding='utf-8') as stream:json.dump(dict(cases=cases,workers=service.providers.receipts,audio=service.audio.receipts),stream,ensure_ascii=False,indent=2)

def derived(native,count):
    service,_,raw,directory,_=native;p,q=65363,84556
    data=raw[:q*2]+bytes(32000*2)
    supports=[(p,q)]
    if count==2:
        a=len(data)//2;data+=raw[p*2:q*2]+bytes(32000*2);supports.append((a,a+q-p))
    source=directory/f'Unaltered first utterance x{count}.wav'
    with wave.open(str(source),'wb') as out:out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(data)
    # Independent numerical support oracle over all decoded source samples.
    values=array.array('h',data)
    for a,b in supports:assert values[a] and values[b-1] and any(values[a:b])
    return source,supports,len(data)//2

def request(service,source,generation,**options):
    descriptor=FfprobeDubInspector(service.audio.runtime,service.audio.paths).inspect(source,DubControl())
    return SpeechRequest(descriptor,output_plan(descriptor,OutputMode.AUDIO),generation,retain_private_recovery=True,**options)

@pytest.mark.parametrize('count',[1,2])
def test_real_first_utterance_and_complete_two_phrase_positive(native,count):
    service,_,_,_,cases=native;source,supports,n=derived(native,count);before=hashlib.sha256(source.read_bytes()).hexdigest()
    req=request(service,source,'amended-positive-'+str(count))
    try:result=service.prepare(req,DubControl())
    except BaseException as error:
        cases.append(dict(case='positive-'+str(count),failure=getattr(error,'code',type(error).__name__),receipt=dict(service.last_receipt or {})))
        raise
    folder=result.path.parent;pcm=array.array('h',result.path.read_bytes())
    assert result.samples==n*3==len(pcm) and result.peak<=-1.3
    fits=sorted((json.loads(p.read_bytes())['payload'] for p in folder.glob('fit-*.json')),key=lambda r:r['start'])
    assert len(fits)==count
    position=0;measurements=[]
    for (a,b),fit in zip(supports,fits):
        assert not any(pcm[position:a*3]) and fit['start']==a*3 and fit['slot_end']==b*3
        assert not any(pcm[b*3:(supports[len(measurements)+1][0]*3 if len(measurements)+1<count else n*3)])
        thresholds={str(t):next(i for i in range(a*3,b*3) if abs(pcm[i])>=t) for t in (1,16,32,128)}
        assert 0<=(thresholds['32']/48000-a/16000)<=.100
        assert Fraction(fit['tempo'])<=Fraction(5,4)
        measurements.append(dict(source=[a,b],anchor=fit['anchor'],thresholds=thresholds,fit=fit))
        position=b*3
    assert not any(pcm[position:])
    english=' '.join(json.loads(p.read_bytes())['payload']['text'] for p in sorted(folder.glob('segment-*.json')))
    russian=' '.join(json.loads(p.read_bytes())['payload']['text'] for p in sorted(folder.glob('translate-*.json')))
    assert english.casefold().count('computer')==count and english.casefold().count('ready')==count
    assert russian.casefold().count('компьютер')==count and russian.casefold().count('готов')==count
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before and not service.providers.gate.busy and not service.providers.gate.failure
    cases.append(dict(case='positive-'+str(count),source=str(source),source_sha256=before,measurements=measurements,
        samples=result.samples,peak=result.peak,lufs=result.integrated_lufs,receipt=dict(service.last_receipt),
        text=dict(english=english,russian=russian),listening='PENDING OWNER; numerical checks do not establish phoneme/pronunciation quality'))
    output=os.environ.get('MEDIAFORGE_TEST_ALIGNMENT_LISTENING')
    if output:
        with wave.open(output.replace('.wav',f'-{count}.wav'),'wb') as out:
            out.setnchannels(1);out.setsampwidth(2);out.setframerate(48000);out.writeframes(pcm.tobytes())
    service.sessions.retire(result.session_id,DubControl())

def test_retained_sparse_refuses_when_russian_does_not_fit_source_support(native):
    service,source,_,_,cases=native;before=hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(Exception) as error:service.prepare(request(service,source,'amended-sparse-refusal'),DubControl())
    assert getattr(error.value,'code',None)=='timing_unfit'
    assert service.last_receipt['status']=='failed_or_cancelled' and not service.providers.gate.busy and not service.providers.gate.failure
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    cases.append(dict(case='retained-sparse-refusal',error=getattr(error.value,'code'),receipt=dict(service.last_receipt)))
    service.sessions.retire(service.last_receipt['session_id'],DubControl())
