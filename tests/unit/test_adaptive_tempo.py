"""Adaptive production policy is separate from historical strict timing."""
from dataclasses import replace
from fractions import Fraction as F
import pytest
from mediaforge.features.dub.block_timing import (
    ADAPTIVE_TEMPO as A,STRICT_TEMPO as S,PhraseTime,split_blocks,
    lowest_measured,schedule_measured,tempo_factors,
)
from mediaforge.features.dub.speech import TimingError

def block(neutral=72000):
    return split_blocks(48000,(),(PhraseTime(0,0,48000,neutral),))[0]

def counts(b,t):return tuple((p.neutral_samples*t.denominator+t.numerator-1)//t.numerator for p in b.phrases)

def test_adaptive_real_count_callback_can_exceed_retired_ceiling():
    b=block();seen=[]
    def measure(t):seen.append(t);return counts(b,t)
    result,trials=lowest_measured(b,measure,policy=A)
    assert F(5,4)<result.tempo<F(8,5) and result.placements[0].end<=b.end
    assert len(seen)==len(set(seen))<=17
    assert result.tempo==min(F(k,10000) for k,(_,r) in trials.items() if r is not None)
    assert trials[int(result.tempo*10000)-1][1] is None
    with pytest.raises(TimingError):lowest_measured(b,lambda t:counts(b,t),policy=S)

def test_natural_fit_performs_one_measurement_and_keeps_positions():
    b=block(20000);calls=[]
    result,trials=lowest_measured(b,lambda t:calls.append(t) or counts(b,t),policy=A)
    assert calls==[F(1)] and result.tempo==1 and result.maximum_absolute_delta==0

@pytest.mark.parametrize('tempo,factors',[(F(1),(F(1),)),(F(8,5),(F(8,5),)),(F(2),(F(2),)),(F(5,2),(F(2),F(5,4))),(F(4),(F(2),F(2)))])
def test_pitch_preserving_chain_has_no_stage_above_two(tempo,factors):
    assert tempo_factors(tempo,A)==factors
    product=F(1)
    for f in factors:assert 1<=f<=2;product*=f
    assert product==tempo and len(factors)<=2

@pytest.mark.parametrize('tempo',[0,-1,F(40001,10000),float('nan'),float('inf')])
def test_invalid_or_unbounded_speed_is_not_a_media_success(tempo):
    with pytest.raises(TimingError):tempo_factors(tempo,A)

@pytest.mark.parametrize('tempo,above16,above2',[(F(8,5),False,False),(F(16001,10000),True,False),(F(2),True,False),(F(20001,10000),True,True)])
def test_quality_thresholds_are_diagnostic_exact_boundaries(tempo,above16,above2):
    r=schedule_measured(block(),(1000,),tempo,policy=A)
    assert ('tempo_above_1_6' in r.warnings)==above16
    assert ('tempo_above_2_0' in r.warnings)==above2

def test_infeasible_at_technical_limit_does_not_omit_audio():
    b=block(240000)
    with pytest.raises(TimingError) as e:lowest_measured(b,lambda t:counts(b,t),policy=A)
    assert e.value.timing_facts['tempo']=='4' and e.value.timing_facts['policy']==A.name
    assert e.value.timing_facts['deficit_samples']>0

@pytest.mark.parametrize('bad',[(0,),(-1,),(True,),(1,2)])
def test_malformed_actual_counts_are_not_capacity_predictions(bad):
    with pytest.raises(TimingError):lowest_measured(block(),lambda t:bad,policy=A)

def test_cancelled_measurement_is_not_refitted_or_completed():
    seen=[]
    def cancelled(t):seen.append(t);raise TimingError('cancelled','cancelled actual render')
    with pytest.raises(TimingError) as e:lowest_measured(block(),cancelled,policy=A)
    assert e.value.code=='cancelled' and seen==[1]

def test_no_cross_barrier_borrowing_or_clock_extension():
    a,b=split_blocks(144000,((48000,96000),),(PhraseTime(0,0,48000,20000),PhraseTime(1,96000,144000,72000)))
    out,_=lowest_measured(b,lambda t:counts(b,t),policy=A)
    assert out.start==96000 and out.end==144000
    assert all(p.start>=96000 and p.end<=144000 for p in out.placements)

def test_retry_binding_authenticates_versioned_adaptive_policy():
    from mediaforge.application.services.tutorial_speech import binding
    assert binding()['elastic']==A.name and A.name!=S.name

def test_unknown_policy_cannot_select_a_new_technical_range():
    with pytest.raises(TimingError):tempo_factors(5,replace(A,maximum_grid=50000))

def test_old_retry_policy_is_not_adopted_by_adaptive_session(tmp_path):
    from types import SimpleNamespace as NS
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
    from mediaforge.features.dub.contracts import DubControl
    store=SpeechSessions(ProfilePaths.for_test(tmp_path/'portable'));c=DubControl()
    with store.open(NS(retry_session=None,retain_private_recovery=True),dict(elastic=S.name),c) as session:name=session.path.name
    with pytest.raises(Exception) as e:
        with store.open(NS(retry_session=name,retain_private_recovery=True),dict(elastic=A.name),c):pass
    assert e.value.code=='checkpoint_ineligible'
    store.retire(name,c)

def test_production_composition_uses_measured_adaptive_counts_after_gpu_retirement(tmp_path):
    from contextlib import contextmanager
    from types import SimpleNamespace as NS
    import array,hashlib
    from mediaforge.application.services.tutorial_composition import compose
    from mediaforge.application.contracts.local_ai import PcmFragment,CompletionReceipt
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
    from mediaforge.features.dub.contracts import DubControl
    store=SpeechSessions(ProfilePaths.for_test(tmp_path/'portable'));c=DubControl();active=[False];calls=[]
    class Tts:
        last_statistics=None
        @contextmanager
        def open(self,*args):
            active[0]=True
            def synthesize(text,voice):
                calls.append(text)
                return PcmFragment(b'\x64\x00'*48000,24000,'fixed',voice,CompletionReceipt('normal_eos',True,12,12,13,1500))
            try:yield NS(synthesize=synthesize),c
            finally:active[0]=False;self.last_statistics=dict(retired=True)
    class Audio:
        def convert(self,source,target,control,*,rate,tempo=None,tempo_policy=None,**kw):
            n=source.stat().st_size//2
            if rate==24000:assert active[0] and tempo is None;n*=2
            else:
                assert not active[0] and tempo_policy==A
                n=(n*tempo.denominator+tempo.numerator-1)//tempo.numerator
            target.write_bytes(b'\x64\x00'*n);return n
        def level(self,source,target,control,total):
            assert not active[0];target.write_bytes(source.read_bytes());return (-16.,-2.),()
    service=NS(tts=Tts(),audio=Audio(),providers=NS(session_owner=object()),last_receipt={'tutorial_v2':{}})
    segment=NS(text='Keep every instruction.');anchor=dict(start=0,end=48000,phrase=dict(sha256='a'*64))
    plan=dict(pauses=dict(mandatory=[]),source_binding='b'*64,uncertainty_count=0)
    with store.admission(c),store.open(NS(retry_session=None,retain_private_recovery=True),dict(policy=A.name),c) as session:
        name=session.path.name;session.put('v2-translate-0.json',dict(text='Сохраните все инструкции.',english_sha256=hashlib.sha256(segment.text.encode()).hexdigest()))
        result=compose(service,NS(voice='aiden',plan=NS(sample_count=48000)),session,c,lambda *a:None,iter([(segment,(0,),anchor)]),{},'adaptive',1,plan)
        record=session.get('v2-schedule-adaptive-0.json')
        assert F(record['tempo'])>2 and record['policy']==A.name and record['schema']=='tutorial-adaptive-fit-v1'
        assert result.samples==48000 and result.path.stat().st_size==96000
        assert 'tempo_above_1_6' in result.warnings and 'tempo_above_2_0' in result.warnings
        assert calls==['Сохраните все инструкции.'] and not active[0]
        assert service.last_receipt['tutorial_v2']['maximum_tempo']==record['tempo']
        assert not session.owns_name('e-body-0.pcm')
    store.retire(name,c)
