"""Operation reuse retains a verified runtime capability, never a stat cache."""
from contextlib import contextmanager
from types import SimpleNamespace as NS
import pytest
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.features.dub.contracts import DubControl
from mediaforge.infrastructure.models.dub_providers import SpeechProviders


@pytest.mark.parametrize('failure',[False,True])
def test_runtime_lease_stays_owned_until_operation_retirement(failure):
    events=[];capability=NS(verification_seconds=1.25,handles=(object(),),native_ids={})
    class Runtime:
        @contextmanager
        def acquire(self,paths,control):
            events.append('authenticated_and_locked')
            try:yield capability
            finally:events.append('released')
    runtime=Runtime();gate=MediaProcessingGate();provider=SpeechProviders(NS(),runtime,gate)
    provider.reuse_runtime=True
    def operation():
        with provider.session(DubControl()):
            assert events==['authenticated_and_locked'] and gate.busy
            assert provider._owners.runtime_guard==(runtime,capability)
            assert provider.statistics['runtime_verification_seconds']==1.25
            if failure:raise ValueError('controlled inference failure')
    if failure:
        with pytest.raises(ValueError):operation()
    else:operation()
    assert events==['authenticated_and_locked','released']
    assert not hasattr(provider._owners,'runtime_guard')
    assert not gate.busy and not gate.failure
    with provider.session(DubControl()):
        assert events==['authenticated_and_locked','released','authenticated_and_locked']
    assert events[-1]=='released' and len(events)==4
