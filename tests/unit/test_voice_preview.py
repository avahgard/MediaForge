from contextlib import contextmanager
from threading import Event
import time

from mediaforge.application.contracts.local_ai import PcmFragment,LocalAiError
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.application.services.voice_preview import VoicePreviewService


def idle(service):
    end=time.monotonic()+2
    while service.busy and time.monotonic()<end:time.sleep(.005)
    assert not service.busy


def fake_service(*,raw=b'\x01\0'*2400):
    gate=MediaProcessingGate();entered=Event();release=Event();closed=[];calls=[]
    @contextmanager
    def factory(control,owner):
        assert gate.owned_by(owner)
        class Engine:
            statistics={}
            def synthesize(self,text,voice,seconds):
                calls.append((voice,seconds));entered.set()
                while not release.wait(.005):control.check()
                control.check();return PcmFragment(raw,24000,'exact-test-revision',voice)
        try:yield Engine()
        finally:closed.append(True)
    version={'value':'1'}
    service=VoicePreviewService(factory,lambda:dict(revision=version['value']),('aiden','ryan'),gate)
    return service,gate,entered,release,closed,calls,version


def test_preview_is_explicit_and_ready_only_after_engine_retirement():
    service,gate,entered,release,closed,calls,_=fake_service()
    assert calls==[] and service.fragment() is None
    assert service.start('aiden') and entered.wait(1)
    assert gate.busy and service.fragment() is None and not service.start('ryan')
    release.set();idle(service)
    assert closed and not gate.busy
    assert service.snapshot().state=='ready' and service.fragment().sample_rate==24000
    service.shutdown()


def test_preview_cancel_cannot_publish_ready_or_fill_cache():
    service,gate,entered,release,closed,calls,_=fake_service()
    service.start('aiden');assert entered.wait(1)
    service.cancel();idle(service)
    assert service.snapshot().state=='cancelled' and service.fragment() is None and closed and not gate.busy
    release.set();assert service.start('aiden');idle(service)
    assert len(calls)==2 and service.snapshot().state=='ready'
    service.shutdown()


def test_preview_cache_binds_voice_and_exact_runtime_revision():
    service,gate,entered,release,closed,calls,version=fake_service()
    release.set();service.start('aiden');idle(service)
    assert service.start('aiden') and service.snapshot().cached and len(calls)==1
    version['value']='2'
    service.start('aiden');idle(service)
    assert len(calls)==2 and not service.snapshot().cached
    service.start('ryan');idle(service)
    assert len(calls)==3 and service.fragment().speaker=='ryan'
    token=gate.acquire();assert not service.start('aiden');gate.release(token)
    service.shutdown()


def test_oversized_or_silent_preview_is_never_ready():
    for raw in (bytes(48),b'\x01\0'*(8*24000+1)):
        service,gate,entered,release,closed,calls,_=fake_service(raw=raw)
        release.set();service.start('aiden');idle(service)
        assert service.snapshot().state=='failed' and service.snapshot().code=='invalid_output'
        assert service.fragment() is None and closed and not gate.busy
        service.shutdown()
