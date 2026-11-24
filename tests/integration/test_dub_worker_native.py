"""B1/B2/B4/B5, exact new C3 factory and real existing assets. No downloads."""
import array,hashlib,json,os,sys,wave
from pathlib import Path
import pytest
from mediaforge.bootstrap.speech import compose_dub_local_ai
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.application.contracts.local_ai import LocalAiError,RecognitionRequest,TranslationRequest,SynthesisRequest
from mediaforge.application.contracts.models import ModelError
from mediaforge.infrastructure.models.providers import WorkerRuntime
from mediaforge.infrastructure.models.registry import development_registry
from mediaforge.infrastructure.models.process import CpuJob
from mediaforge.infrastructure.models import providers as c2_providers
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.features.media.processing_control import ProcessingControl

pytestmark=[pytest.mark.native,pytest.mark.integration]
ROOT=Path(__file__).resolve().parents[2]
def control():return ProcessingControl(lambda:LocalAiError('cancelled','Native qualification cancelled.'))
@pytest.fixture(scope='module')
def native():
    value=os.environ.get('MEDIAFORGE_TEST_AI_ROOT')
    if not value:pytest.skip('Explicit authenticated C2 model/runtime root required.')
    paths=ProfilePaths.for_test(Path(value))
    specs=development_registry(ROOT/'external/manifests/dub-c2-development.json',
        'e95373ca537ac2139eb47ce37113122d45eeb679c26fa416d39a830322f81e2a',allow_development=True)
    runtime=WorkerRuntime(Path(sys._base_executable),paths.root/'runtime/site',paths.manifests/'runtime.json',specs[0].runtime_fingerprint)
    return compose_dub_local_ai(paths,runtime,MediaProcessingGate(),specs=specs)

@pytest.fixture
def observed(monkeypatch):
    rows=[];commands=[];retired=[];original=c2_providers.run_worker;spawn=CpuJob.spawn;close=CpuJob.close
    def call(*args,**kwargs):
        result=original(*args,**kwargs)
        rows.append({k:result[k] for k in ('native_facts','platform_selectors','delegate_sha256','metrics','violations') if k in result})
        return result
    def spawned(owner,command,**kwargs):commands.append(command);return spawn(owner,command,**kwargs)
    def closed(owner):
        close(owner)
        retired.append(dict(exited=owner.native.exited,handle_closed=owner.process._handle.closed,job_closed=owner.job is None,metrics=owner.metrics))
    monkeypatch.setattr(c2_providers,'run_worker',call);monkeypatch.setattr(CpuJob,'spawn',spawned);monkeypatch.setattr(CpuJob,'close',closed)
    yield rows,commands,retired
    assert all(r['exited'] and r['handle_closed'] and r['job_closed'] for r in retired)
    directory=os.environ.get('MEDIAFORGE_TEST_C3_WORKER_RECEIPTS')
    if directory:
        import uuid
        target=Path(directory);target.mkdir(parents=True,exist_ok=True)
        (target/(uuid.uuid4().hex+'.json')).write_text(json.dumps(dict(rows=rows,commands=commands,retired=retired),indent=2),encoding='utf-8')

def test_c3_piper_smoke_inference_and_native_facts(native,observed):
    fragment=native.providers.synthesize(SynthesisRequest('Проверка локального русского голоса.'),control())
    values=array.array('h',fragment.pcm16)
    assert fragment.sample_rate==22050 and fragment.samples>22050 and max(abs(v) for v in values)>1000
    rows,commands,_=observed
    assert len(rows)==len(commands)==2 # real ModelService smoke AND inference
    assert all(Path(cmd[4])==native.providers.runtime.entry for cmd in commands)
    assert all(r['native_facts']['version']==list(sys.getwindowsversion().platform_version) for r in rows)
    assert all(r['platform_selectors']['system']>0 and r['platform_selectors']['release']>0 for r in rows)
    assert all(not any(Path(image['path']).name.lower() in ('mpoav.dll','amsi.dll') for image in r['metrics']['native_images']) for r in rows)
    assert not native.providers.gate.busy and not native.providers.gate.failure

def test_c3_real_base_argos_same_factory(native,observed):
    path=Path(os.environ['MEDIAFORGE_TEST_C3_ENGLISH_SOURCE'])
    with wave.open(str(path),'rb') as stream:pcm=stream.readframes(stream.getnframes())
    assert hashlib.sha256(pcm).hexdigest()=='5b0729e396fe10734ad2fa43762284f17e00fa41862fbc032d76838c082f30e5'
    recognized=native.providers.recognize(RecognitionRequest(pcm),control())
    text=' '.join(s.text for s in recognized.segments)
    assert 'computer' in text.lower() and '12' in text and 'backup' in text.lower()
    translated=native.providers.translate(TranslationRequest(text),control())
    assert '12' in translated.text and 'не ' in translated.text.lower()
    assert all(r.get('native_facts',{}).get('system')=='Windows' for r in observed[0])
    assert all(Path(cmd[4])==native.providers.runtime.entry for cmd in observed[1])

@pytest.mark.parametrize('phase',['load','inference'])
def test_cancel_c3_load_then_explicit_retry(native,observed,phase):
    c=control()
    with pytest.raises(LocalAiError) as error:
        native.providers.synthesize(SynthesisRequest('Проверка отмены.'),c,on_phase=lambda p:c.cancel() if p==phase else None)
    assert error.value.code=='cancelled' and not c.cleanup_failed and not native.providers.gate.busy
    result=native.providers.synthesize(SynthesisRequest('Новая явная проверка.'),control())
    assert result.samples>0

def test_c3_owned_crash_then_clean_retry(native,observed,monkeypatch):
    original=c2_providers.run_worker
    def crash(command,**kwargs):
        import signal
        kwargs['on_started']=lambda pid:os.kill(pid,signal.SIGTERM)
        return original(command,**kwargs)
    monkeypatch.setattr(c2_providers,'run_worker',crash)
    with pytest.raises(ModelError) as refused:native.providers.synthesize(SynthesisRequest('Проверка сбоя.'),control())
    assert refused.value.code in ('incompatible','provider_failed')
    monkeypatch.setattr(c2_providers,'run_worker',original)
    assert not native.providers.gate.busy and not native.providers.gate.failure
    assert native.providers.synthesize(SynthesisRequest('Новая проверка после сбоя.'),control()).samples>0

@pytest.mark.parametrize('member',['dub_worker.py','worker.py'])
def test_c3_exact_entry_delegate_refusal_and_cleanup(native,tmp_path,monkeypatch,member):
    from mediaforge.infrastructure.models import dub_runtime
    root=tmp_path/'Перенесённый worker';root.mkdir()
    entry=root/'dub_worker.py'
    for name in ('dub_worker.py','worker.py'):(root/name).write_bytes(dub_runtime.ENTRY.with_name(name).read_bytes())
    (root/member).write_bytes(b'raise RuntimeError("MUST NEVER EXECUTE")')
    monkeypatch.setattr(dub_runtime,'ENTRY',entry)
    runtime=dub_runtime.DubWorkerRuntime(native.providers.runtime.python,native.providers.runtime.site,
        native.providers.runtime.manifest,native.providers.runtime.manifest_sha256,entry)
    with pytest.raises(LocalAiError) as refused:
        with runtime.acquire(native.providers.paths,control()):pytest.fail('Tampered source acquired')
    assert refused.value.code=='runtime_integrity'
    # Both handles and ancestors released after failure; no ACL repair needed.
    for path in root.iterdir():path.unlink()
    root.rmdir()

def test_cancel_at_c3_child_admission_then_retry(native,observed,monkeypatch):
    c=control();original=c2_providers.run_worker
    def cancel_at_start(command,**options):
        options['on_started']=lambda pid:c.cancel()
        return original(command,**options)
    monkeypatch.setattr(c2_providers,'run_worker',cancel_at_start)
    with pytest.raises((LocalAiError,ModelError)) as refused:native.providers.synthesize(SynthesisRequest('Проверка отмены.'),c)
    assert refused.value.code=='cancelled' and not native.providers.gate.busy and not c.cleanup_failed
    monkeypatch.setattr(c2_providers,'run_worker',original)
    assert native.providers.synthesize(SynthesisRequest('Явный повтор после отмены.'),control()).samples>0

def test_fixed_vad_policy_observes_retained_onset_counterexample(native,observed):
    """A refusal witness, explicitly NOT a successful A1/A3 composition oracle."""
    from fractions import Fraction
    from mediaforge.infrastructure.models.dub_providers import SpeechProviders
    from mediaforge.features.dub.alignment import Support,qualify,activity_runs
    from mediaforge.features.dub.speech import TimingError
    path=Path(os.environ['MEDIAFORGE_TEST_C3_SPARSE_SOURCE'])
    assert hashlib.sha256(path.read_bytes()).hexdigest()=='9d1b50d077ed0485920b439eee64c06c63bd9c834057b2cbd4fa51ff0158fe0d'
    with wave.open(str(path),'rb') as stream:pcm=stream.readframes(stream.getnframes())
    providers=SpeechProviders.from_local(native);c=control()
    with providers.session(c),providers.selected_models(('whisper-base',),c):
        observations=providers.activity(pcm,0,c)
    evidence=[]
    for p,q in ((65363,84556),(177438,197830),(290551,313985)):
        support=Support(Fraction(p),Fraction(q),p,q,16000)
        with pytest.raises(TimingError) as refused:qualify(support,observations)
        assert refused.value.code=='speech_boundary_uncertain'
        evidence.append(dict(source=[p,q],error=str(refused.value),
            high=[activity_runs(row['scores'],row['offset'],speech=True) for row in observations]))
    observed[0].append(dict(fixed_vad_corpus=evidence,observations=observations))
    assert not providers.gate.busy and not providers.gate.failure
