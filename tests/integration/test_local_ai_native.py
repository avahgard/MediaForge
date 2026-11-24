"""Explicit opt-in real Base/EN-RU/Ruslan qualification. No downloads/install.

Run this selector with network blocked for the entire test interpreter tree.
MEDIAFORGE_TEST_AI_ROOT must name the separately provisioned development Data/v2.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import socket
import sys
import threading
import time
from types import SimpleNamespace
import wave

import pytest

from mediaforge.application.contracts.local_ai import *
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.bootstrap.local_ai import compose_local_ai
from mediaforge.features.media.processing_control import ProcessingControl
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.models.registry import development_registry
from mediaforge.infrastructure.models.providers import WorkerRuntime
from mediaforge.infrastructure.models.process import run_worker

pytestmark=[pytest.mark.native,pytest.mark.integration,pytest.mark.heavy]
REGISTRY=Path(__file__).resolve().parents[2]/'external/manifests/dub-c2-development.json'
REGISTRY_SHA='e95373ca537ac2139eb47ce37113122d45eeb679c26fa416d39a830322f81e2a'


def control():return ProcessingControl(lambda:LocalAiError('cancelled','Cancelled by native test.'))


def composition(root):
    paths=ProfilePaths.for_test(root)
    specs=development_registry(REGISTRY,REGISTRY_SHA,allow_development=True)
    runtime=WorkerRuntime(Path(sys._base_executable),paths.root/'runtime/site',paths.manifests/'runtime.json',specs[0].runtime_fingerprint)
    return compose_local_ai(paths,runtime,MediaProcessingGate(),specs=specs)


@pytest.fixture(scope='module')
def native():
    value=os.environ.get('MEDIAFORGE_TEST_AI_ROOT')
    if not value:pytest.skip('Explicit isolated real-model root is not provisioned.')
    root=Path(value)
    assert root.is_absolute() and root.name=='v2' and root.parent.name=='Data'
    services=composition(root)
    import ctypes as C
    from mediaforge.infrastructure.images.resources import _Process
    kernel=C.WinDLL('kernel32');kernel.GetCurrentProcess.restype=C.c_void_p
    ps=C.WinDLL('psapi');ps.GetProcessMemoryInfo.argtypes=[C.c_void_p,C.c_void_p,C.c_uint]
    def parent_memory():
        row=_Process();row.cb=C.sizeof(row)
        assert ps.GetProcessMemoryInfo(kernel.GetCurrentProcess(),C.byref(row),row.cb)
        return dict(private=row.private,peak_commit=row.peak_commit,peak_ws=row.peak_ws)
    baseline=parent_memory()
    services.providers._qualified_cancellations=[]
    yield services
    final=parent_memory()
    growth=max(0,final['peak_commit']-baseline['peak_commit'])
    assert growth<=64*1024**2
    receipt=os.environ.get('MEDIAFORGE_TEST_AI_RECEIPT')
    if receipt:
        Path(receipt).write_text(json.dumps(dict(receipts=services.providers.receipts,parent_before=baseline,parent_after=final,parent_peak_increment=growth,
            cancellations=services.providers._qualified_cancellations),indent=2),encoding='utf-8')


@pytest.fixture(scope='module',autouse=True)
def prior_sessions(native):
    """Preserve forensic sessions left by an earlier deliberately killed parent."""
    return {p.name:sorted((f.name,hashlib.sha256(f.read_bytes()).hexdigest()) for f in p.iterdir() if f.is_file())
            for p in native.providers.paths.temporary.glob('inference-*')}


@pytest.fixture(scope='module')
def pcm(native):
    file=native.providers.paths.root/'fixtures/english-16000.wav'
    with wave.open(str(file),'rb') as reader:
        assert (reader.getnchannels(),reader.getsampwidth(),reader.getframerate())==(1,2,16000)
        raw=reader.readframes(reader.getnframes())
    assert hashlib.sha256(raw).hexdigest()=='5b0729e396fe10734ad2fa43762284f17e00fa41862fbc032d76838c082f30e5'
    return raw


def test_offline_native_control_in_same_job(native):
    paths=native.providers.paths
    # This child has NO Python audit hook: Winsock is denied by the external
    # sandbox/network policy, independently of provider/library configuration.
    code="import socket,json;s=socket.socket();s.settimeout(2);e=s.connect_ex(('1.1.1.1',443));s.close();print(json.dumps({'ok':True,'connect_error':e}))"
    row=run_worker([sys._base_executable,'-I','-S','-B','-c',code],paths=paths,environment=paths.child_environment(),control=control())
    assert row['connect_error']==10013,'Run this qualification with an OS network block, not only offline environment flags.'


def test_real_chain_and_native_pcm(native,pcm):
    recognized=native.providers.recognize(RecognitionRequest(pcm),control())
    text=' '.join(s.text for s in recognized.segments)
    assert 'computer' in text.lower() and '12' in text and 'Do not delete the backup' in text
    assert all(0<=s.start_sample<s.end_sample<=len(pcm)//2 for s in recognized.segments)
    translation=native.providers.translate(TranslationRequest(text),control())
    assert '12' in translation.text and 'не удаляйте' in translation.text.lower()
    assert ''.join(s.source for s in translation.spans)==text
    fragment=native.providers.synthesize(SynthesisRequest(translation.text),control())
    assert fragment.sample_rate==22050 and 22050<fragment.samples<60*22050
    import array
    samples=array.array('h',fragment.pcm16)
    assert max(abs(n) for n in samples)>1000
    # Independent repeated cold load after resolved model/child ownership.
    again=native.providers.translate(TranslationRequest('There are 12 files. Do not delete the backup.'),control())
    assert '12' in again.text and 'не удаляйте' in again.text.lower()


def test_sentence_coverage_numbers_negation_and_long_text(native):
    text='This computer is ready. Do not delete any file. '*40
    row=native.providers.translate(TranslationRequest(text),control())
    assert len(row.spans)==80
    assert ''.join(s.source for s in row.spans)==text
    assert sum('не удаляйте' in s.russian.lower() for s in row.spans)==40
    assert sum('компьютер' in s.russian.lower() for s in row.spans)==40
    technical=native.providers.translate(TranslationRequest('The processor uses 4 threads and 512 megabytes of memory.'),control())
    assert '4' in technical.text and '512' in technical.text and 'памят' in technical.text.lower()
    assert native.providers.translate(TranslationRequest(''),control()).spans==()


@pytest.mark.parametrize('pcm',[b'',b'\0'*100,b'\0'*32000],ids=['empty','short','silence'])
def test_silence_empty_short(native,pcm):
    assert not native.providers.recognize(RecognitionRequest(pcm),control()).segments


@pytest.mark.parametrize('key,phase',[('whisper-base','load'),('whisper-base','inference'),('argos-en-ru','inference'),('piper-ruslan','inference')])
def test_cancel_actual_native_phase_and_recover(native,pcm,key,phase):
    c=control();seen=[];timers=[];cancelled_at=[]
    def on_phase(name):
        seen.append(name)
        if name==phase:
            def cancel():
                if c.cancel():cancelled_at.append(time.monotonic())
            timer=threading.Timer(.05,cancel);timers.append(timer);timer.start()
    request={
        'whisper-base':lambda:native.providers.recognize(RecognitionRequest(pcm),c,on_phase=on_phase),
        'argos-en-ru':lambda:native.providers.translate(TranslationRequest('This is a local test. '*50),c,on_phase=on_phase),
        'piper-ruslan':lambda:native.providers.synthesize(SynthesisRequest('Проверка локального синтеза речи. '*10),c,on_phase=on_phase),
    }[key]
    started=time.monotonic()
    try:
        with pytest.raises(LocalAiError) as error:request()
        assert error.value.code=='cancelled' and phase in seen
        assert not native.providers.gate.busy and not c.cleanup_failed
        assert time.monotonic()-started<30  # includes TWO cold runtime/model authentications
        assert cancelled_at
        latency=time.monotonic()-cancelled_at[0]
        assert latency<10
        native.providers._qualified_cancellations.append(dict(model=key,phase=phase,settlement_seconds=latency))
    finally:
        for timer in timers:timer.join(2)
    assert native.providers.translate(TranslationRequest('The backup is safe.'),control()).text


@pytest.mark.parametrize('key,action,member',[
    ('whisper-base','recognize','model.bin'),
    ('argos-en-ru','translate','model/model.bin'),
    ('piper-ruslan','synthesize','ru_RU-ruslan-medium.onnx'),
])
def test_native_missing_and_corrupt_load_refuse(native,tmp_path,key,action,member):
    # Isolated invalid copies, never tamper the real installed weights.
    source=native.providers.paths.root/'models'/key/native.models.spec(key).revision
    invalid=native.providers.paths.temporary/('invalid-'+key)
    invalid.mkdir(exist_ok=True)
    for filename in ('tokenizer.json','config.json','metadata.json','sentencepiece.model','model/config.json','ru_RU-ruslan-medium.onnx.json'):
        if (source/filename).exists() and not (invalid/filename).exists():
            (invalid/filename).parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source/filename,invalid/filename)
    (invalid/member).parent.mkdir(parents=True,exist_ok=True)
    (invalid/member).write_bytes(b'INVALID MODEL')
    payload={'pcm16':''} if key=='whisper-base' else {'text':'The computer is ready.' if key=='argos-en-ru' else 'Проверка.'}
    with pytest.raises(LocalAiError) as error:
        native.providers._run(native.models.spec(key),SimpleNamespace(path=invalid),action,payload,control())
    assert error.value.code=='provider_failed' and not native.providers.gate.busy
    (invalid/member).unlink()
    with pytest.raises(LocalAiError):native.providers._run(native.models.spec(key),SimpleNamespace(path=invalid),action,payload,control())


def test_receipts_and_cache_miss_remain_portable(native,prior_sessions):
    for receipt in native.providers.receipts:
        assert receipt['violations']==[]
        assert all(Path(path).is_relative_to(native.providers.paths.root) for path in receipt['writes'])
        assert receipt['metrics']['job_commit_peak']<=2*1024**3
        assert receipt['metrics']['cpu_slots']<=4
    current={p.name:sorted((f.name,hashlib.sha256(f.read_bytes()).hexdigest()) for f in p.iterdir() if f.is_file())
             for p in native.providers.paths.temporary.glob('inference-*')}
    assert current==prior_sessions
