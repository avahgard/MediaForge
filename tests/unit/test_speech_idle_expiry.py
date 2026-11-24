"""Native owned-session expiry; clocks are injected, no 24-hour sleeps."""
from threading import Event
from types import SimpleNamespace as NS
import time

import pytest

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.application.services.dub import DubService,DubState
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.features.dub.contracts import DubControl
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
from mediaforge.infrastructure.models.owned import Ownership

pytestmark=pytest.mark.native


def fixture(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'Private Unicode 雪/Data/v2');paths.prepare()
    clocks=[100000.,1000.]
    store=SpeechSessions(paths,wall_clock=lambda:clocks[0],monotonic_clock=lambda:clocks[1])
    with store.open(NS(retry_session=None,retain_private_recovery=True),{},DubControl()) as session:
        name=session.path.name;session.put_bytes('private.pcm',b'\x01\x00'*20)
    gate=MediaProcessingGate();service=DubService(None,NS(sessions=store,last_receipt=None),None,gate,('aiden',))
    service._state=DubState(generation=7,state='failed',code='timing_unfit',message='Original failure.')
    service._retry=name;service._retry_key=object()
    return paths,clocks,store,name,gate,service


def wait(service):
    end=time.monotonic()+3
    while service.busy and time.monotonic()<end:time.sleep(.005)
    assert not service.busy


@pytest.mark.parametrize('clock',[0,1])
def test_idle_expiry_invalidates_retry_and_retires_exact_bytes(tmp_path,clock):
    paths,clocks,store,name,gate,service=fixture(tmp_path)
    assert service.can_retry() and not store.expiry_due()
    clocks[clock]+=86400
    assert not service.can_retry() and store.expiry_due()
    assert service.expire_idle_sessions();wait(service)
    assert name not in store._tickets and not (paths.sessions/name).exists()
    assert service._retry is None and service.snapshot().generation==7
    assert service.snapshot().state=='failed' and service.snapshot().code=='timing_unfit'
    assert 'Retry expired' in service.snapshot().message and not gate.busy
    assert not service.expire_idle_sessions()
    service.shutdown();assert not service.expire_idle_sessions()


def test_expiry_preserves_unknown_orphan_and_published_output(tmp_path):
    paths,clocks,store,name,gate,service=fixture(tmp_path)
    orphan=paths.sessions/'dub-unknown-prior-process';orphan.mkdir()
    sentinel=orphan/'private.pcm';sentinel.write_bytes(b'unknown sentinel')
    published=tmp_path/'published.wav';published.write_bytes(b'validated output sentinel')
    clocks[0]+=86401;assert service.expire_idle_sessions();wait(service);service.shutdown()
    assert sentinel.read_bytes()==b'unknown sentinel' and published.read_bytes()==b'validated output sentinel'
    assert not (paths.sessions/name).exists()
    with pytest.raises(LocalAiError) as error:
        with SpeechSessions(paths).open(NS(retry_session=orphan.name),{},DubControl()):pass
    assert error.value.code=='checkpoint_ineligible'


def test_active_session_and_other_gate_owner_are_not_retired(tmp_path):
    paths,clocks,store,name,gate,service=fixture(tmp_path)
    with store.open(NS(retry_session=name,retain_private_recovery=True),{},DubControl()):
        clocks[0]+=86401
        assert not store.expiry_due() and store.expire_idle(DubControl())==()
        assert (paths.sessions/name/'private.pcm').exists()
    token=gate.acquire();assert not service.can_retry() and not service.expire_idle_sessions()
    assert (paths.sessions/name).exists() and gate.owned_by(token)
    gate.release(token);assert service.expire_idle_sessions();wait(service);service.shutdown()


def test_foreign_native_owner_refuses_cleanup_and_quarantines_gate(tmp_path):
    paths,clocks,store,name,gate,service=fixture(tmp_path)
    actor=Ownership();actor.open(paths.sessions/name/'private.pcm',0x80000000,1,3)
    try:
        clocks[0]+=86401;assert service.expire_idle_sessions();wait(service)
        assert gate.failure and gate.busy and not service.can_retry()
        assert name in store._tickets and store._tickets[name]['expired']
        assert store._tickets[name]['identity_pin'].handles
        assert (paths.sessions/name/'private.pcm').read_bytes()==b'\x01\x00'*20
        clocks[0]-=86401;assert not store.retry_eligible(name)  # failure cannot revive authorization
        with pytest.raises(LocalAiError):service.shutdown()
    finally:actor.close()
    # Explicit exact-object test retirement after the actor settles; this is not
    # automatic application recovery and must not unpoison an existing gate.
    store.close(DubControl());assert not (paths.sessions/name).exists() and gate.failure


def test_unknown_member_blocks_expiry_without_deleting_it(tmp_path):
    paths,clocks,store,name,gate,service=fixture(tmp_path)
    unknown=paths.sessions/name/'unexpected';unknown.write_bytes(b'not tracked')
    identity=unknown.stat().st_ino
    clocks[0]+=86401;assert service.expire_idle_sessions();wait(service)
    assert gate.failure and not store.retry_eligible(name) and name in store._tickets
    assert unknown.read_bytes()==b'not tracked'
    assert (paths.sessions/name/'private.pcm').exists()
    # The test actor, unlike the application, knows its exact inserted object.
    with Ownership() as actor:
        handle=actor.open(unknown,0x80010000,1,3);info=actor.info(handle)
        assert (info.id_high<<32)|info.id_low==identity
        actor.pending(handle,True)
    store.close(DubControl());assert not (paths.sessions/name).exists() and gate.failure


def test_cancelled_expiry_retains_authority_and_cannot_restore_retry(tmp_path):
    paths,clocks,store,name,gate,service=fixture(tmp_path);clocks[1]+=86401
    control=DubControl();control.cancel()
    with pytest.raises(LocalAiError) as error:store.expire_idle(control)
    assert error.value.code=='lifecycle_failed' and store in error.value.retained_resources
    assert control.cleanup_failed and not store.retry_eligible(name)
    assert store._tickets[name]['directory_owner'].handles and (paths.sessions/name).exists()
    store.close(DubControl());assert not (paths.sessions/name).exists()


def test_shutdown_retires_expired_owned_session_without_waiting_for_tick(tmp_path):
    paths,clocks,store,name,gate,service=fixture(tmp_path);clocks[0]+=86401
    service.shutdown()
    assert not (paths.sessions/name).exists() and not store._tickets and not gate.busy
    assert not service.expire_idle_sessions()


def test_shutdown_cancels_maintenance_before_releasing_its_gate(tmp_path,monkeypatch):
    paths,clocks,store,name,gate,service=fixture(tmp_path);clocks[0]+=86401
    entered=Event();original=store.expire_idle
    # Use the public cancellation check instead of relying on provider internals.
    def cancellable(control):
        entered.set()
        while True:
            try:control.check()
            except Exception:return original(control)
            time.sleep(.005)
    monkeypatch.setattr(store,'expire_idle',cancellable)
    assert service.expire_idle_sessions() and entered.wait(1) and gate.busy
    with pytest.raises(LocalAiError):service.shutdown()
    assert not service.busy and gate.failure and gate.busy and name in store._tickets
    store.close(DubControl())
