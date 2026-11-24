from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from threading import Event
import time
from types import SimpleNamespace as NS

from mediaforge.application.services.dub import DubService
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.features.dub.contracts import SourceKind,SourceIdentity,AudioTrack,ClockFacts


def wait(service):
    end=time.monotonic()+2
    while service.busy and time.monotonic()<end:time.sleep(.005)
    assert not service.busy


def fixture(tmp_path):
    clock=ClockFacts(Fraction(1,16000),Fraction(0),Fraction(10),1,'test',160000)
    source=NS(identity=SourceIdentity(tmp_path/'source.wav',1,2,3,4,'abc'),kind=SourceKind.AUDIO,
        audio_tracks=(AudioTrack(0,'pcm_s16le',16000,1,'mono',None,None,True,Fraction(1,16000),0,10,clock),),video=None)
    gate=MediaProcessingGate();events=[];entered=Event();release=Event()
    class Sessions:
        def close(self,control):events.append('sessions_closed')
        def retire_if_present(self,name,control):events.append('session_retired')
        def retry_eligible(self,name):return True
        def expiry_due(self):return False
    class Speech:
        sessions=Sessions();last_receipt=None
        def prepare(self,request,control,progress,gate_owner):
            assert gate.owned_by(gate_owner);self.last_receipt=dict(session_id='private-current-session')
            events.append(('prepare',request.retry_session));entered.set()
            while not release.wait(.005):control.check()
            return NS(session_id='private-current-session',warnings=('estimated_speech_boundaries',))
    class Export:
        last_receipt=None
        def execute(self,src,plan,prepared,destination,control,progress):
            assert gate.busy;events.append('export_validate')
            if getattr(self,'fail',False):raise LocalAiError('validation_failed','Independently decoded output differs.')
            control.publish(lambda:destination);self.last_receipt=dict(status='validated_published')
            return NS(output=destination)
    export=Export();service=DubService(NS(inspect=lambda p,c:source),Speech(),export,gate,('aiden','ryan'))
    assert service.inspect(source.identity.path);wait(service)
    return service,gate,events,entered,release,export,source


def test_gate_covers_synthesis_export_validation_and_session_retirement(tmp_path):
    service,gate,events,entered,release,_,_=fixture(tmp_path)
    assert service.start('aiden',tmp_path) and entered.wait(1)
    assert gate.busy and service.snapshot().state=='running'
    assert not service.inspect(tmp_path/'other.wav')
    release.set();wait(service)
    assert service.snapshot().state=='completed' and not gate.busy
    assert service.snapshot().destination.endswith('source_RU_aiden.mp3')
    assert events[-2:]==['export_validate','session_retired']
    service.shutdown()


def test_validation_failure_has_no_completed_result_and_retry_keeps_exact_choices(tmp_path):
    service,gate,events,entered,release,export,_=fixture(tmp_path)
    export.fail=True;release.set();assert service.start('aiden',tmp_path);wait(service)
    assert service.snapshot().state=='failed' and service.snapshot().destination=='' and service.can_retry()
    assert not gate.busy and 'session_retired' not in events
    try:service.start('ryan',tmp_path,retry=True)
    except LocalAiError as error:assert error.code=='checkpoint_ineligible'
    else:assert False
    export.fail=False;assert service.start('aiden',tmp_path,retry=True);wait(service)
    assert ('prepare','private-current-session') in events and service.snapshot().state=='completed'
    service.shutdown()


def test_cancel_never_exports_and_retires_before_gate_release(tmp_path):
    service,gate,events,entered,release,_,_=fixture(tmp_path)
    assert service.start('aiden',tmp_path) and entered.wait(1)
    service.cancel();wait(service)
    assert service.snapshot().state=='cancelled' and 'export_validate' not in events and not gate.busy
    assert service.can_retry();service.shutdown()


def test_unresolved_retirement_poison_prevents_following_job(tmp_path):
    service,gate,events,entered,release,export,_=fixture(tmp_path)
    def unsafe(*args):raise LocalAiError('lifecycle_failed','Owner remains held.',retained_resources=(object(),))
    export.execute=unsafe;release.set();service.start('aiden',tmp_path);wait(service)
    assert service.snapshot().state=='failed' and gate.failure and gate.busy
    assert not service.start('aiden',tmp_path) and not service.can_retry()


def test_active_job_rejects_option_invalidation(tmp_path):
    service,gate,events,entered,release,_,_=fixture(tmp_path)
    assert service.start('aiden',tmp_path) and entered.wait(1)
    before=service.snapshot()
    assert not service.options_changed() and service.snapshot()==before and gate.busy
    release.set();wait(service);service.shutdown()


def test_options_change_preserves_source_and_retires_session_only_at_next_start(tmp_path):
    service,gate,events,entered,release,export,source=fixture(tmp_path)
    export.fail=True;release.set();service.start('aiden',tmp_path);wait(service)
    assert service.can_retry();before=list(events)
    assert service.options_changed()
    assert service.source() is source and not service.can_retry() and service.snapshot().state=='ready'
    assert events==before and not gate.busy
    export.fail=False;assert service.start('ryan',tmp_path);wait(service)
    assert service.snapshot().destination.endswith('source_RU_ryan.mp3')
    service.shutdown()
