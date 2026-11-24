from dataclasses import replace
import json
from types import SimpleNamespace as NS

import pytest

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.features.dub.contracts import DubControl
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions

pytestmark=pytest.mark.native

def request(**kw):
    return NS(retry_session=kw.get('retry'),retain_private_recovery=kw.get('retain',True))

def test_explicit_retry_hmac_source_model_identity_and_cleanup(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'Portable Юникод'/'Data/v2');paths.prepare()
    store=SpeechSessions(paths);binding=dict(source='a'*64,voice='ruslan',model='exact-pin',policy='clock-v1')
    with store.open(request(),binding,DubControl()) as session:
        name=session.path.name
        session.put('row.json',dict(text='Private Russian text.'))
        assert session.get('row.json')==dict(text='Private Russian text.')
        with pytest.raises(Exception):
            with (session.path/'row.json').open('wb') as stream:stream.write(b'not allowed')
    with pytest.raises(LocalAiError):
        with store.open(request(retry=name),dict(binding,model='other-model'),DubControl()):pass
    with store.open(request(retry=name),binding,DubControl()) as session:
        assert session.get('row.json')['text']=='Private Russian text.' and session.hits==1
    store.retire(name,DubControl());assert not (paths.sessions/name).exists()

def test_live_retry_reuses_existing_slot_without_adopting_or_deleting_an_orphan(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'Retry with orphan/Data/v2');paths.prepare();store=SpeechSessions(paths)
    orphan=paths.sessions/'dub-untrusted-prior-process';orphan.mkdir();sentinel=orphan/'unknown';sentinel.write_bytes(b'not owned')
    other=paths.sessions/'dub-second-untrusted';other.mkdir();other_sentinel=other/'unknown';other_sentinel.write_bytes(b'also not owned')
    identities=(orphan.stat().st_ino,other.stat().st_ino)
    binding=dict(policy='source-support-v2')
    with store.admission(DubControl()),store.open(request(),binding,DubControl()) as session:
        name=session.path.name;session.put('row.json',dict(text='private synthetic'))
    with pytest.raises(LocalAiError) as full:
        with store.admission(DubControl()):pass
    assert full.value.code=='disk_budget'
    with pytest.raises(LocalAiError) as unknown:
        with store.admission(DubControl(),retry_session=orphan.name):pass
    assert unknown.value.code=='checkpoint_ineligible'
    with store.admission(DubControl(),retry_session=name),store.open(request(retry=name),binding,DubControl()) as session:
        assert session.get('row.json')['text']=='private synthetic'
        assert len(list(paths.sessions.iterdir()))==3
    store.close(DubControl())
    assert sentinel.read_bytes()==b'not owned' and other_sentinel.read_bytes()==b'also not owned'
    assert (orphan.stat().st_ino,other.stat().st_ino)==identities
    assert set(paths.sessions.iterdir())=={orphan,other}

def test_tampered_checkpoint_and_crash_orphan_never_reused(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'portable/Data/v2');paths.prepare();store=SpeechSessions(paths)
    with store.open(request(),dict(source='one'),DubControl()) as session:
        name=session.path.name;session.put('row.json',dict(text='original'))
    path=paths.sessions/name/'row.json';path.write_bytes(b'{"payload":{},"seal":"forged"}')
    with pytest.raises(LocalAiError):
        with store.open(request(retry=name),dict(source='one'),DubControl()) as session:session.get('row.json')
    # A new process/store has no HMAC authority; explicit retry is refused, not
    # automatic recovery or acceptance of a self-authenticating manifest.
    with pytest.raises(LocalAiError):
        with SpeechSessions(paths).open(request(retry=name),dict(source='one'),DubControl()):pass

def test_cancelled_session_only_checkpoint_cleanup_and_immutable_pcm(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'portable/Data/v2');paths.prepare();store=SpeechSessions(paths);c=DubControl()
    with pytest.raises(Exception):
        with store.open(request(retain=False),dict(source='one'),c) as session:
            name=session.path.name;session.put('row.json',dict(text='private'))
            c.cancel();c.check()
    assert not (paths.sessions/name).exists()
    c=DubControl()
    with store.open(request(),dict(source='two'),c) as session:
        name=session.path.name
        with session.pcm('speech.pcm',100) as writer:writer.silence(40);writer.write(b'\x01\x00'*60)
        session.track(session.path/'speech.pcm')
        assert (session.path/'speech.pcm').stat().st_size==200
        session.discard(session.path/'speech.pcm')
    store.retire(name,c)

def test_partial_pcm_never_becomes_a_complete_stage(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'portable/Data/v2');paths.prepare();store=SpeechSessions(paths)
    with store.open(request(),{},DubControl()) as session:
        name=session.path.name
        with pytest.raises(LocalAiError):
            with session.pcm('partial.pcm',10) as writer:writer.silence(11)
        assert list(session.path.iterdir())==[]
    store.retire(name,DubControl())

def test_expired_owned_recovery_is_retired_at_next_explicit_use(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'portable/Data/v2');paths.prepare();store=SpeechSessions(paths)
    with store.open(request(),{},DubControl()) as session:
        name=session.path.name;session.put('row.json',dict(text='private'))
    store._tickets[name]['created']-=86401
    with pytest.raises(LocalAiError):
        with store.open(request(retry=name),{},DubControl()):pass
    assert not (paths.sessions/name).exists()

def test_cleanup_fault_poison_precedes_processing_gate_release(tmp_path,monkeypatch):
    from mediaforge.infrastructure.models.dub_providers import SpeechProviders
    from mediaforge.application.services.processing_gate import MediaProcessingGate
    paths=ProfilePaths.for_test(tmp_path/'portable/Data/v2');paths.prepare();store=SpeechSessions(paths)
    providers=SpeechProviders(paths,NS(),MediaProcessingGate());control=DubControl()
    def fail(*args):raise OSError('Injected private cleanup refusal')
    monkeypatch.setattr(store,'retire',fail)
    with pytest.raises(LocalAiError) as error:
        with providers.session(control):
            with store.open(request(retain=False),{},control) as session:
                name=session.path.name;session.put('row.json',dict(text='private'))
                control.cancel();control.check()
    assert error.value.code=='lifecycle_failed' and error.value.retained_resources
    assert providers.gate.busy and providers.gate.failure and control.cleanup_failed
    assert name in store._tickets and (paths.sessions/name).exists()

def test_directory_object_cannot_be_replaced_before_retirement(tmp_path,monkeypatch):
    import ctypes as C
    from mediaforge.infrastructure.models.owned import Ownership,Info
    paths=ProfilePaths.for_test(tmp_path/'portable/Data/v2');paths.prepare();store=SpeechSessions(paths)
    with store.open(request(),{},DubControl()) as session:
        name=session.path.name;session.put('row.json',dict(text='private'))
    path=paths.sessions/name;attempts=[];original=Ownership.pending
    def pending(owner,handle,value):
        info=Info();assert owner.api.GetFileInformationByHandle(handle,C.byref(info))
        if value and info.attributes&0x10:
            try:path.rename(path.with_name('replacement-race'))
            except OSError as error:attempts.append(error.winerror)
            else:pytest.fail('Retiring directory namespace was not protected')
        return original(owner,handle,value)
    monkeypatch.setattr(Ownership,'pending',pending)
    store.retire(name,DubControl())
    assert attempts and not path.exists() and not path.with_name('replacement-race').exists()

def test_paused_recognition_reader_retires_before_cancel_cleanup(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'portable/Data/v2');paths.prepare();store=SpeechSessions(paths);control=DubControl()
    with pytest.raises(Exception) as error:
        with store.open(request(retain=False),{},control) as session:
            name=session.path.name;path=session.put_bytes('extract.pcm',b'\x01\x00'*100)
            def reader():
                with path.open('rb') as stream:yield stream.read(2)
            active=session.own_reader(reader());assert next(active)==b'\x01\x00'
            control.cancel();control.check()
    assert getattr(error.value,'code',None)=='cancelled' and not control.cleanup_failed
    assert not (paths.sessions/name).exists()

def test_abrupt_writer_exit_leaves_no_partial_pcm_and_root_reopens(tmp_path,monkeypatch):
    import sys
    from pathlib import Path
    from mediaforge.infrastructure.models.process import run_worker,CpuJob
    paths=ProfilePaths.for_test(tmp_path/'portable/Data/v2');paths.prepare()
    target=paths.temporary/'partial.pcm'
    code="""
import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from mediaforge.infrastructure.ffmpeg.speech import PcmWriter
from mediaforge.features.dub.contracts import DubControl
writer=PcmWriter(Path(sys.argv[2]),DubControl(),100)
writer.__enter__();writer.write(b'\\x01\\x00'*10)
Path(sys.argv[2]).with_suffix('.witness').write_bytes(b'writer reached')
os._exit(9)
"""
    observed=[];close=CpuJob.close
    def retire(owner):
        close(owner)
        observed.append(dict(exit=owner.process.returncode,closed=owner.process._handle.closed,native_exited=owner.native.exited))
    monkeypatch.setattr(CpuJob,'close',retire)
    with pytest.raises(LocalAiError):
        run_worker([sys._base_executable,'-I','-S','-B','-c',code,str(Path(__file__).resolve().parents[2]/'src'),str(target)],
            paths=paths,environment=paths.child_environment(),control=DubControl(),timeout=20)
    assert target.with_suffix('.witness').read_bytes()==b'writer reached'
    assert observed==[dict(exit=9,closed=True,native_exited=True)]
    assert not target.exists() and not list(paths.temporary.glob('.pcm-*'))
    # No ACL recovery/administrator operation; same ordinary token reopens.
    paths.prepare();store=SpeechSessions(paths)
    with store.open(request(),{},DubControl()) as session:name=session.path.name
    store.retire(name,DubControl())
