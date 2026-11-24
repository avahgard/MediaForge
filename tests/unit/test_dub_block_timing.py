from fractions import Fraction as F
from dataclasses import asdict
import pytest
from mediaforge.features.dub.block_timing import PhraseTime,split_blocks,schedule_measured,lowest_measured,minimum_nominal_tempo
from mediaforge.features.dub.speech import TimingError

def test_idle_capacity_can_be_shared_beyond_old_400ms_without_overlap():
    phrases=(PhraseTime(0,0,2*48000,4*48000),PhraseTime(1,3*48000,8*48000,48000))
    block,=split_blocks(10*48000,(),phrases);before=asdict(block)
    result,trials=lowest_measured(block,lambda ratio:tuple((p.neutral_samples*ratio.denominator+ratio.numerator-1)//ratio.numerator for p in phrases))
    assert result.tempo==1 and len(trials)==1 and asdict(block)==before
    a,b=result.placements
    assert a.end<=b.start and b.delta>19200 and b.end<=block.end

def test_mandatory_barrier_prevents_global_aggregate_capacity_borrowing():
    phrases=(PhraseTime(0,0,4*48000,48000),PhraseTime(1,6*48000,8*48000,4*48000))
    first,second=split_blocks(9*48000,((4*48000,6*48000),),phrases)
    assert minimum_nominal_tempo(first)==1
    with pytest.raises(TimingError) as e:schedule_measured(second,(4*48000*4//5,),F(5,4))
    assert e.value.code=='phrase_timing_unfit' and e.value.timing_facts['deficit_samples']>0

@pytest.mark.parametrize('bad',[((0,200),),((100,90),),((30,60),(50,70))])
def test_invalid_or_crossed_exclusions_are_not_erased(bad):
    with pytest.raises(TimingError):split_blocks(100,bad,(PhraseTime(0,0,99,10),))

def test_minimax_has_deterministic_tie_break_and_signed_clock_deviations():
    block,=split_blocks(480000,(),(PhraseTime(0,200000,220000,240000),PhraseTime(1,300000,350000,190000)))
    a=schedule_measured(block,(240000,190000),1);b=schedule_measured(block,(240000,190000),1)
    assert a==b and a.placements[0].delta<0 and a.maximum_absolute_delta>48000
    assert 'phrase_timing_shifted' in a.warnings
    assert a.placements[0].end+1920<=a.placements[1].start

def test_lowest_actual_trial_is_bounded_and_does_not_infer_nominal_success():
    block,=split_blocks(480000,(),(PhraseTime(0,0,480000,550000),))
    calls=[]
    def measure(ratio):
        calls.append(ratio);return ((550000*ratio.denominator+ratio.numerator-1)//ratio.numerator+137,)
    result,trials=lowest_measured(block,measure)
    assert F(1)<result.tempo<=F(5,4) and len(calls)<=14 and len(calls)==len(set(calls))
    assert result.tempo==min(r for r in calls if trials[int(r*10000)][1] is not None)
    assert result.placements[0].end<=480000

def test_large_sequence_uses_exact_clock_and_resets_at_barriers():
    phrases=tuple(PhraseTime(i,i*9600,(i+1)*9600,5000) for i in range(2000))
    block,=split_blocks(2000*9600,(),phrases);result=schedule_measured(block,(5000,)*2000,1)
    assert len(result.placements)==2000 and result.maximum_absolute_delta==0
    assert all(b.start>=a.end+1920 for a,b in zip(result.placements,result.placements[1:]))

def test_measured_capacity_failure_has_no_partial_schedule_or_tempo_escape():
    block,=split_blocks(48000,(),(PhraseTime(0,0,48000,120000),))
    with pytest.raises(TimingError) as e:lowest_measured(block,lambda r:(120000*r.denominator//r.numerator,))
    assert e.value.timing_facts['tempo']=='5/4'
    with pytest.raises(TimingError):schedule_measured(block,(1000,),F(126,100))


@pytest.mark.parametrize('kind',['disputed','long_quiet','missing'])
def test_asr_only_exclusion_requires_independent_contradiction(tmp_path,kind):
    from types import SimpleNamespace as NS
    from mediaforge.application.services.tutorial_speech import assess_asr_gap,write_record
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
    from mediaforge.features.dub.contracts import DubControl
    from tests.unit.test_tutorial_v2 import feature
    paths=ProfilePaths.for_test(tmp_path/'portable');store=SpeechSessions(paths);control=DubControl()
    with store.admission(control),store.open(NS(retry_session=None,retain_private_recovery=True),dict(policy='elastic'),control) as session:
        name=session.path.name;activity=[]
        for g in (0,256):
            scores=[.1 if (1<=((g+j*512)/16000)<3 if kind=='long_quiet' else 1.6<=(g+j*512)/16000<2.4) else .95 for j in range((64000-g)//512)]
            activity.append(dict(offset=g,scores=scores))
        write_record(session,'v2-asr-0',dict(activity=[] if kind=='missing' else activity))
        facts=assess_asr_gap(session,feature([1000]*500),48000,144000)
        assert facts['disputed']==(kind=='disputed')
        assert facts['start']==48000 and facts['end']==144000
    store.retire(name,control)


@pytest.mark.parametrize('kind',['speech','long_nonhigh','short_cores'])
def test_native_window_edge_unknown_is_conservative_and_never_a_score(monkeypatch,kind):
    from mediaforge.application.services import tutorial_speech as module
    from tests.unit.test_tutorial_v2 import feature
    records={}
    for index in (0,1):
        activity=[]
        for grid in (0,256):
            offset=index*480000+grid;scores=[]
            for j in range((480000-grid)//512):
                x=offset+j*512
                if kind=='speech':score=.95
                elif kind=='long_nonhigh':score=.1 if 29.8<=x/16000<31.2 else .95
                else:score=.95 if (479744-1024<=x<479744 or 480256<=x<480256+1024) else .1
                scores.append(score)
            activity.append(dict(offset=offset,scores=scores))
        records[f'v2-asr-{index}']=dict(activity=activity)
    monkeypatch.setattr(module,'read_record',lambda session,key:records[key])
    before=repr(records)
    a,b=(int(29.4*48000),int(30.6*48000)) if kind=='short_cores' else (28*48000,32*48000)
    result=module.assess_asr_gap(None,feature([1000]*5000),a,b)
    assert result['disputed']==(kind=='speech') and repr(records)==before
    assert all(F(g['unknown'])==256 for g in result['grids'])
    assert result['unknown_policy']=='possible_nonhigh_not_observed_score'
    if kind=='long_nonhigh':assert all(F(g['longest_nonhigh'])>=16000 for g in result['grids'])
    if kind=='short_cores':assert all(F(g['maximum_positive_core'])<1536 for g in result['grids'])


@pytest.mark.parametrize('mode',['complete','eos_refused','cancel'])
def test_owned_elastic_composition_closes_gpu_before_fit_and_never_clips(tmp_path,mode):
    from contextlib import contextmanager
    from types import SimpleNamespace as NS
    import array,hashlib
    from mediaforge.application.services.tutorial_composition import compose
    from mediaforge.application.contracts.local_ai import PcmFragment,CompletionReceipt,LocalAiError
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
    from mediaforge.features.dub.contracts import DubControl
    paths=ProfilePaths.for_test(tmp_path/'portable');store=SpeechSessions(paths);control=DubControl();active=[False];calls=[]
    class Tts:
        last_statistics=None
        @contextmanager
        def open(self,c,owner):
            active[0]=True
            def synthesize(text,voice):
                calls.append(text)
                if mode=='cancel':control.cancel()
                completion=None if mode=='eos_refused' else CompletionReceipt('normal_eos',True,12,12,13,1500)
                return PcmFragment(b'\x64\x00'*(96000 if len(calls)==1 else 24000),24000,'fixed',voice,completion)
            try:yield NS(synthesize=synthesize),control
            finally:active[0]=False;self.last_statistics=dict(retired=True)
    class Audio:
        def convert(self,source,target,c,*,rate,**kwargs):
            assert active[0] and rate==24000 and not kwargs
            v=array.array('h',source.read_bytes());target.write_bytes(array.array('h',(x for x in v for _ in (0,1))).tobytes());return len(v)*2
        def level(self,source,target,c,total):
            assert not active[0];target.write_bytes(source.read_bytes());return (-16.,-3.),()
    service=NS(tts=Tts(),providers=NS(session_owner=object()),audio=Audio(),last_receipt={'tutorial_v2':{}})
    request=NS(voice='aiden',plan=NS(sample_count=480000));plan=dict(pauses=dict(mandatory=[]),uncertainty_count=0,source_binding='a'*64)
    with store.admission(control),store.open(NS(retry_session=None,retain_private_recovery=True),dict(policy='elastic'),control) as session:
        name=session.path.name;rows=[]
        for i,(a,b) in enumerate(((0,96000),(144000,432000))):
            segment=NS(text=f'Phrase {i}.');anchor=dict(start=a,end=b,phrase=dict(sha256=hashlib.sha256(segment.text.encode()).hexdigest()))
            session.put(f'v2-translate-{i}.json',dict(text=f'Фраза {i}.',english_sha256=hashlib.sha256(segment.text.encode()).hexdigest()));rows.append((segment,(i,),anchor))
        if mode=='complete':
            result=compose(service,request,session,control,lambda *args:None,iter(rows),{},'generation',2,plan)
            assert result.samples==480000 and result.path.stat().st_size==960000
            a=session.get('v2-schedule-generation-0.json');b=session.get('v2-schedule-generation-1.json')
            assert a['end']+1920<=b['start'] and b['delta']>19200
            assert 'phrase_timing_shifted' in result.warnings and not active[0] and calls==['Фраза 0.','Фраза 1.']
            assert not session.owns_name('e-body-0.pcm') and not session.owns_name('e-body-0.json')
        else:
            with pytest.raises(Exception) as e:compose(service,request,session,control,lambda *args:None,iter(rows),{},'generation',2,plan)
            assert e.value.code==('invalid_output' if mode=='eos_refused' else 'cancelled') and not active[0]
    store.retire(name,DubControl());assert not store._tickets
