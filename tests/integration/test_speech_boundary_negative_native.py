"""Real recognition/activity refuses independent ambiguous source preludes."""
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
def native():
    root=os.environ.get('MEDIAFORGE_TEST_AI_ROOT');ff=os.environ.get('MEDIAFORGE_TEST_RUNTIME_ROOT');source=os.environ.get('MEDIAFORGE_TEST_C3_SPARSE_SOURCE')
    if not root or not ff or not source:pytest.skip('Explicit retained model/runtime/source roots required.')
    paths=ProfilePaths.for_test(Path(root));specs=development_registry(ROOT/'external/manifests/dub-c2-development.json',
        'e95373ca537ac2139eb47ce37113122d45eeb679c26fa416d39a830322f81e2a',allow_development=True)
    rt=WorkerRuntime(Path(sys._base_executable),paths.root/'runtime/site',paths.manifests/'runtime.json',specs[0].runtime_fingerprint)
    service=compose_speech(compose_dub_local_ai(paths,rt,MediaProcessingGate(),specs=specs),
        FfmpegRuntimeResolver(FfmpegManifest.load()).verify_directory(Path(ff)))
    source=Path(source);before=hashlib.sha256(source.read_bytes()).hexdigest()
    assert before=='9d1b50d077ed0485920b439eee64c06c63bd9c834057b2cbd4fa51ff0158fe0d'
    with wave.open(str(source),'rb') as stream:raw=stream.readframes(84556)
    cases=[]
    yield service,raw,cases
    service.sessions.close(DubControl());assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    target=os.environ.get('MEDIAFORGE_TEST_BOUNDARY_NEGATIVE_RECEIPT')
    if target:
        with Path(target).open('x',encoding='utf-8') as f:json.dump(dict(cases=cases,workers=service.providers.receipts),f,indent=2)

@pytest.mark.parametrize('kind',['isolated-click','quiet-nonzero-prelude'])
def test_ambiguous_native_prelude_never_becomes_an_earlier_voice_anchor(native,tmp_path,kind):
    service,raw,cases=native;p=65363;values=array.array('h',raw+bytes(32000*2))
    if kind=='isolated-click':values[p-4000]=500
    else:
        for i in range(p-4000,p):values[i]=11 if i%2 else -11
    source=tmp_path/(kind+'.wav')
    with wave.open(str(source),'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(values.tobytes())
    descriptor=FfprobeDubInspector(service.audio.runtime,service.audio.paths).inspect(source,DubControl())
    request=SpeechRequest(descriptor,output_plan(descriptor,OutputMode.AUDIO),'negative-'+kind,retain_private_recovery=True)
    with pytest.raises(Exception) as refused:service.prepare(request,DubControl())
    assert getattr(refused.value,'code',None)=='speech_boundary_uncertain'
    assert service.last_receipt['status']=='failed_or_cancelled' and not service.providers.gate.busy and not service.providers.gate.failure
    cases.append(dict(case=kind,error=getattr(refused.value,'code'),detail=str(refused.value),receipt=dict(service.last_receipt),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),original_first_word_start=p,prelude_start=p-4000))
    service.sessions.retire(service.last_receipt['session_id'],DubControl())
