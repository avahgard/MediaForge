"""Explicit real C3 qualification; no downloads, export, UI or historical edits."""
from dataclasses import replace
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import ctypes as C
import wave

import pytest

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.application.contracts.speech import SpeechRequest
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.bootstrap.local_ai import compose_local_ai
from mediaforge.bootstrap.speech import compose_speech,compose_dub_local_ai
from mediaforge.features.dub.contracts import DubControl, OutputMode
from mediaforge.features.dub.planning import output_plan
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.ffmpeg.dub_probe import FfprobeDubInspector
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest,FfmpegRuntimeResolver
from mediaforge.infrastructure.models.providers import WorkerRuntime
from mediaforge.infrastructure.models.registry import development_registry

pytestmark=[pytest.mark.native,pytest.mark.integration,pytest.mark.heavy]
ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture(scope='module')
def native(tmp_path_factory):
    value=os.environ.get('MEDIAFORGE_TEST_AI_ROOT');ff=os.environ.get('MEDIAFORGE_TEST_RUNTIME_ROOT')
    if not value or not ff:pytest.skip('Explicit authenticated local-model and managed FFmpeg roots required.')
    paths=ProfilePaths.for_test(Path(value))
    specs=development_registry(ROOT/'external/manifests/dub-c2-development.json',
        'e95373ca537ac2139eb47ce37113122d45eeb679c26fa416d39a830322f81e2a',allow_development=True)
    runtime=WorkerRuntime(Path(sys._base_executable),paths.root/'runtime/site',paths.manifests/'runtime.json',specs[0].runtime_fingerprint)
    local=compose_dub_local_ai(paths,runtime,MediaProcessingGate(),specs=specs)
    ffmpeg=FfmpegRuntimeResolver(FfmpegManifest.load()).verify_directory(Path(ff))
    service=compose_speech(local,ffmpeg)
    directory=tmp_path_factory.mktemp('speech-source-Юникод')
    english=Path(os.environ.get('MEDIAFORGE_TEST_C3_ENGLISH_SOURCE',str(paths.root/'fixtures/english-16000.wav')))
    with wave.open(str(english),'rb') as stream:raw=stream.readframes(stream.getnframes())
    assert hashlib.sha256(raw).hexdigest()=='5b0729e396fe10734ad2fa43762284f17e00fa41862fbc032d76838c082f30e5'
    source=directory/'two utterances with silence.wav'
    pcm=bytes(2*16000*2)+raw+bytes(4*16000*2)+raw+bytes(4*16000*2)
    with wave.open(str(source),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000);stream.writeframes(pcm)
    descriptor=FfprobeDubInspector(ffmpeg,paths).inspect(source,DubControl())
    request=SpeechRequest(descriptor,output_plan(descriptor,OutputMode.AUDIO),'native-c3-first',retain_private_recovery=True)
    from mediaforge.infrastructure.images.resources import _Process
    kernel=C.WinDLL('kernel32');kernel.GetCurrentProcess.restype=C.c_void_p
    ps=C.WinDLL('psapi');ps.GetProcessMemoryInfo.argtypes=[C.c_void_p,C.c_void_p,C.c_uint]
    def memory():
        row=_Process();row.cb=C.sizeof(row)
        assert ps.GetProcessMemoryInfo(kernel.GetCurrentProcess(),C.byref(row),row.cb)
        return dict(private=row.private,peak_commit=row.peak_commit,peak_ws=row.peak_ws)
    before_memory=memory();service._qualified_cases=[]
    yield service,request,paths
    service.sessions.close(DubControl()) # normal test/app shutdown retires ONLY live capabilities
    after_memory=memory()
    growth=max(0,after_memory['peak_commit']-before_memory['peak_commit'])
    assert growth<=64*1024**2
    receipt=os.environ.get('MEDIAFORGE_TEST_C3_RECEIPT')
    if receipt:Path(receipt).write_text(json.dumps(dict(model_operations=service.providers.receipts,
        audio_operations=service.audio.receipts,last=service.last_receipt,cases=service._qualified_cases,
        parent_before=before_memory,parent_after=after_memory,parent_peak_increment=growth),indent=2),encoding='utf-8')

def test_real_dense_timing_fit_or_honest_refusal(native):
    service,request,paths=native;before=hashlib.sha256(request.plan.source.path.read_bytes()).hexdigest()
    stages=[]
    try:result=service.prepare(request,DubControl(),lambda *args:stages.append(args))
    except Exception as error:
        assert getattr(error,'code',None)=='timing_unfit'
        assert service.last_receipt['status']=='failed_or_cancelled'
        candidate=service.last_receipt.get('fit_candidate')
        if candidate is not None:assert candidate['neutral_samples']>0
        assert not service.providers.gate.busy and not service.providers.gate.failure
        assert hashlib.sha256(request.plan.source.path.read_bytes()).hexdigest()==before
        service._qualified_cases.append(dict(case='dense-timing',outcome='timing_unfit',receipt=dict(service.last_receipt)))
        service.sessions.retire(service.last_receipt['session_id'],DubControl())
        return
    assert_composition(service,request,paths,result,stages,2,'dense-chain')

def assert_composition(service,request,paths,result,stages,repetitions,label):
    before=request.plan.source.sha256
    assert result.internal_only and result.path.is_relative_to(paths.sessions)
    assert result.samples==request.plan.sample_count and result.path.stat().st_size==2*result.samples
    raw=result.path.read_bytes()
    assert raw[:48000]==bytes(48000) and raw[-48000:]==bytes(48000)
    assert -70<result.integrated_lufs and result.peak<=-1.3
    assert hashlib.sha256(raw).hexdigest()==result.sha256
    # Independent semantic fixture oracle: both real English utterances survive
    # chunking. Private checkpoint text is asserted, never logged in diagnostics.
    folder=result.path.parent
    # Raw recognition checkpoints deliberately retain neighboring CONTEXT
    # observations. Assert only the reconciled, owned speech journal, not those
    # intentionally repeated observations.
    english=' '.join(json.loads(file.read_bytes())['payload']['text']
        for file in sorted(folder.glob('segment-*.json')))
    assert english.casefold().count('computer')==repetitions and english.casefold().count('backup')==repetitions
    assert english.count('12')==repetitions and english.casefold().count('not delete')==repetitions
    russian=' '.join(json.loads(file.read_bytes())['payload']['text']
        for file in sorted(folder.glob('translate-*.json')))
    assert russian.count('12')==repetitions and russian.casefold().count('не ')>=repetitions
    fits=[json.loads(file.read_bytes())['payload'] for file in folder.glob('fit-*.json')]
    fits.sort(key=lambda row:row['start'])
    assert len(fits)>=2
    gaps=[(a['start']+a['samples'],b['start']) for a,b in zip(fits,fits[1:])]
    gap_start,gap_end=max(gaps,key=lambda span:span[1]-span[0])
    assert gap_end-gap_start>=4800
    generation=hashlib.sha256(request.job_id.encode()).hexdigest()[:16]
    composition=(folder/('compose-'+generation+'.pcm')).read_bytes()
    assert len(composition)==len(raw) and not any(composition[2*gap_start:2*gap_end])
    # Fixed oversampling has a finite FIR boundary response, not a timeline
    # shift. The native impulse and Russian witnesses independently bound it
    # within 64 samples; retain exact silence beyond that response on both sides.
    assert not any(raw[2*(gap_start+64):2*(gap_end-64)])
    assert hashlib.sha256(request.plan.source.path.read_bytes()).hexdigest()==before
    assert all(row['violations']==[] for row in service.providers.receipts)
    assert any(stage[0]=='recognize' for stage in stages) and any(stage[0]=='compose' for stage in stages)
    assert not service.providers.gate.busy and not service.providers.gate.failure
    service._qualified_cases.append(dict(case=label,receipt=dict(service.last_receipt)))
    # Keep the private evidence bounded, and explicitly retire only this new job.
    output=os.environ.get('MEDIAFORGE_TEST_C3_OUTPUT')
    if output and label=='sparse-chain':
        with wave.open(output,'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(48000);stream.writeframes(raw)
        listening=os.environ.get('MEDIAFORGE_TEST_C3_LISTENING_RECEIPT')
        if listening:
            # Private consented synthetic corpus, separate from diagnostic logs.
            with Path(listening).open('x',encoding='utf-8') as stream:
                json.dump(dict(approval='Owner listening PENDING',voice='Ruslan Offline',source=str(request.plan.source.path),
                    source_sha256=before,english=english,russian=russian,fit_rows=fits,sample_rate=48000,samples=result.samples,
                    pcm_sha256=result.sha256,integrated_lufs=result.integrated_lufs,true_peak_dbtp=result.peak,
                    leading_and_trailing_silence=True,output=output),stream,ensure_ascii=False,indent=2)
    service.sessions.retire(result.session_id,DubControl())

@pytest.fixture(scope='module')
def sparse(native):
    service,original,paths=native
    value=os.environ.get('MEDIAFORGE_TEST_C3_SPARSE_SOURCE')
    if not value:pytest.skip('Explicit independently pinned sparse PCM source fixture required.')
    source=Path(value)
    with wave.open(str(source),'rb') as reader:
        assert (reader.getnchannels(),reader.getsampwidth(),reader.getframerate())==(1,2,16000)
        raw=reader.readframes(reader.getnframes())
    assert hashlib.sha256(raw).hexdigest()=='178cca8552b983975fc23aa80f192a3e3875a31fa26701bee14fb927e6182f7a'
    descriptor=FfprobeDubInspector(service.audio.runtime,paths).inspect(source,DubControl())
    return replace(original,source=descriptor,plan=output_plan(descriptor,OutputMode.AUDIO),job_id='native-c3-sparse')

def test_real_sparse_source_support_refuses_instead_of_filling_pauses(native,sparse):
    service,_,paths=native;stages=[]
    from mediaforge.application.contracts.models import ModelState,ModelError
    def progress(*args):
        stages.append(args)
        assert service.providers.models.snapshot('piper-ruslan').state==ModelState.IN_USE
        with pytest.raises(ModelError):service.providers.models.assert_mutable('piper-ruslan')
    with pytest.raises(Exception) as error:service.prepare(sparse,DubControl(),progress)
    assert getattr(error.value,'code',None)=='timing_unfit'
    assert service.last_receipt['status']=='failed_or_cancelled'
    assert not service.providers.gate.busy and not service.providers.gate.failure
    service._qualified_cases.append(dict(case='sparse-source-support-refusal',receipt=dict(service.last_receipt)))
    service.sessions.retire(service.last_receipt['session_id'],DubControl())

@pytest.fixture(scope='module')
def bounded_positive(native,sparse):
    """Unaltered first utterance; later sparse phrases no longer borrow pauses."""
    service,_,paths=native
    with wave.open(str(sparse.plan.source.path),'rb') as reader:data=reader.readframes(84556)
    target=native[1].plan.source.path.parent/'first-utterance-lifecycle.wav'
    with wave.open(str(target),'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(data+bytes(32000*2))
    descriptor=FfprobeDubInspector(service.audio.runtime,paths).inspect(target,DubControl())
    return replace(sparse,source=descriptor,plan=output_plan(descriptor,OutputMode.AUDIO))

def test_cancel_after_recognition_then_authenticated_explicit_retry(native,bounded_positive):
    service,original,_=native;control=DubControl();before=len(service.providers.receipts)
    original=bounded_positive
    request=replace(original,job_id='native-c3-cancelled')
    def cancel(stage,*args):
        if stage=='translate':control.cancel()
    with pytest.raises(Exception) as error:service.prepare(request,control,cancel)
    assert getattr(error.value,'code',None)=='cancelled'
    session=service.last_receipt['session_id']
    assert not service.providers.gate.busy
    # New control and new generation; explicit session identity, no auto resume.
    try:
        result=service.prepare(replace(original,job_id='native-c3-retry',retry_session=session),DubControl())
    except Exception as refusal:
        # Piper duration is stochastic. Explicit retry authorizes reuse, not a
        # promise that newly generated neutral audio fits every fixed slot.
        assert getattr(refusal,'code',None)=='timing_unfit'
        assert service.last_receipt['status']=='failed_or_cancelled'
        assert service.last_receipt['checkpoint_hits']>0
        assert not service.providers.gate.busy and not service.providers.gate.failure
        service._qualified_cases.append(dict(case='explicit-retry',outcome='timing_unfit',
            receipt=dict(service.last_receipt)))
        service.sessions.retire(session,DubControl())
        return
    assert result.samples==original.plan.sample_count and service.last_receipt['checkpoint_hits']>0
    assert not service.providers.gate.busy
    service._qualified_cases.append(dict(case='explicit-retry',receipt=dict(service.last_receipt)))
    service.sessions.retire(result.session_id,DubControl())

@pytest.mark.parametrize('stage',['recognize','translate','synthesize','compose'])
def test_c3_cancellation_during_each_stage(native,bounded_positive,monkeypatch,stage):
    service,original,_=native;control=DubControl();reached=[];cancelled_at=[]
    original=bounded_positive
    if stage!='compose':
        operation=getattr(service.providers,stage)
        def invoke(request,c,**options):
            def phase(value):
                if value=='inference':reached.append(value);cancelled_at.append(time.monotonic());control.cancel()
            return operation(request,c,on_phase=phase)
        monkeypatch.setattr(service.providers,stage,invoke)
    def progress(value,*args):
        if stage=='compose' and value=='compose':reached.append(value);cancelled_at.append(time.monotonic());control.cancel()
    with pytest.raises(Exception) as error:
        service.prepare(replace(original,job_id='cancel-'+stage,retain_private_recovery=False),control,progress)
    assert reached and getattr(error.value,'code',None)=='cancelled'
    assert not service.providers.gate.busy and not service.providers.gate.failure
    assert service.last_receipt['status']=='failed_or_cancelled'
    assert not (service.sessions.paths.sessions/service.last_receipt['session_id']).exists()
    latency=time.monotonic()-cancelled_at[0]
    assert latency<10
    service._qualified_cases.append(dict(case='cancel-'+stage,settlement_seconds=latency,
        gate_busy=service.providers.gate.busy,cleanup_failed=control.cleanup_failed))
