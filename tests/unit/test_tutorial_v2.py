"""Approved tutorial policy: lossless text lineage and separate dub scheduling."""
import array,base64,hashlib,json
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace as NS
from fractions import Fraction
import pytest
from mediaforge.features.dub.phrases import NativeRow,assemble,Phrase
from mediaforge.features.dub.phrase_timing import PauseMap,pause_map,slot_for,fit_phrase
from mediaforge.features.dub.speech import TimingError
from mediaforge.application.services.tutorial_speech import _sustained_empty,write_record,read_record
from mediaforge.application.contracts.local_ai import RecognitionRequest,LocalAiError
from mediaforge.infrastructure.models.final_speech_providers import FinalSpeechProviders
from tests.unit.test_final_word_activity import response

def assert_coverage(rows,phrases):
    original={(r.observation,r.row):r.text for r in rows};owned={key:[] for key in original}
    for phrase in phrases:
        actual=''
        for ref in phrase.refs:
            text=original[(ref.observation,ref.row)]
            assert hashlib.sha256(text.encode()).hexdigest()==ref.row_sha256
            owned[(ref.observation,ref.row)].append((ref.first,ref.last))
            actual+=ref.separator+text[ref.first:ref.last]
        assert actual==phrase.text and 0<len(phrase.text)<=4000 and len(phrase.text.encode())<=4096
    for key,text in original.items():
        cursor=0
        for a,b in owned[key]:assert a==cursor<b;cursor=b
        assert cursor==len(text)

def test_sentence_crosses_native_window_without_word_ownership_or_character_loss():
    rows=[NativeRow(0,0,27*48000,30*48000,'Do NOT replace x != 42;'),NativeRow(1,0,30*48000,37*48000,' keep the original + operator. Next sentence.')]
    result=list(assemble(rows,()))
    assert_coverage(rows,result)
    assert result[0].text.startswith('Do NOT replace x != 42; keep')
    assert result[0].start==27*48000 and result[-1].end==37*48000

def test_exact_exterior_zero_and_long_pause_partition_whole_tokens():
    rows=[NativeRow(0,0,0,20*48000,'Keep every complete token and number 17. Do not erase tails.')]
    before=deepcopy(rows);barriers=((0,448),(8*48000,10*48000))
    result=list(assemble(rows,barriers));assert_coverage(rows,result)
    assert rows==before and result[0].start==448
    assert all(not(p.start<b and p.end>a) for p in result for a,b in barriers)

def test_tiny_isolated_response_is_not_silently_discarded():
    rows=[NativeRow(0,0,48000,60000,'Yes.'),NativeRow(0,1,4*48000,6*48000,'Stop!')]
    result=list(assemble(rows,()));assert_coverage(rows,result)
    assert [p.text for p in result]==['Yes.','Stop!']

def test_long_unpunctuated_text_has_bounded_complete_partitions():
    rows=[NativeRow(i,0,i*30*48000,(i+1)*30*48000,'word '*700) for i in range(3)]
    result=list(assemble(rows,()));assert_coverage(rows,result)
    assert all(p.end-p.start<=60*48000 for p in result)


def test_thousands_of_phrases_remain_ordered_and_character_complete():
    # A structural clock fixture, explicitly not hour-long native qualification.
    rows=(NativeRow(i//6,i%6,i*6*48000,(i*6+5)*48000,'Keep 42; do not discard the ending.') for i in range(2000))
    count=0;previous=0;owned=0
    for phrase in assemble(rows,()):
        assert previous<=phrase.start<phrase.end and len(phrase.text.encode())<=4096
        previous=phrase.end;owned+=sum(r.last-r.first for r in phrase.refs);count+=1
    assert count==2000 and owned==2000*len('Keep 42; do not discard the ending.')


def test_short_loan_needs_complete_real_activity_coverage(tmp_path):
    from mediaforge.application.services.tutorial_speech import loan_qualified
    class Metadata:
        def __init__(self):self.items={}
        def get(self,name):return self.items.get(name)
        def name(self,name):return tmp_path/name
        def sha(self,path):return hashlib.sha256(path.read_bytes()).hexdigest()
        def put_bytes(self,name,raw):p=tmp_path/name;p.write_bytes(raw);return p
        def put(self,name,value):self.items[name]=value
    session=Metadata()
    # A grid-edge hole cannot be padded into affirmative quiet evidence.
    write_record(session,'v2-asr-0',dict(activity=[dict(offset=g,scores=[.1]*30) for g in (0,256)]))
    pauses=PauseMap(48000,(),((0,48000),),())
    assert not loan_qualified(session,pauses,0,19200)
    assert loan_qualified(session,pauses,1536,19200)
    assert loan_qualified(session,PauseMap(48000,((0,20000),),(),()),0,19200)

@pytest.mark.parametrize('damage',['nan','clock','count','asset','word_range'])
def test_tutorial_parser_keeps_structural_refusal(damage):
    row=response();row['word_segments'][0].update(word_first=0,word_last=2)
    if damage=='nan':row['word_segments'][0]['avg_logprob']=float('nan')
    elif damage=='clock':row['segments'][0]['end']=16001
    elif damage=='count':row['samples']=16001
    elif damage=='asset':row['activity_support']['asset_sha256']='0'*64
    else:row['word_segments'][0]['word_first']=1
    provider=object.__new__(FinalSpeechProviders);provider._selected=lambda *a:(row,NS(revision='fixed'))
    with pytest.raises(LocalAiError):provider.recognize_tutorial(RecognitionRequest(bytes(32000)),None)

def test_core16_low_scores_point_words_and_text_disagreement_are_not_v2_vetoes():
    row=response();row['word_segments'][0].update(word_first=0,word_last=2,avg_logprob=-1.2,no_speech_prob=.145)
    row['words'][0].update(start=0,end=0,probability=.589,text='Different')
    provider=object.__new__(FinalSpeechProviders);provider._selected=lambda *a:(row,NS(revision='fixed'))
    result=provider.recognize_tutorial(RecognitionRequest(bytes(32000)),None)
    assert result['rows'][0]['text']=='A word.' and result['rows'][0]['estimates']==[]
    assert result['confidence'][0]['no_speech_prob']==.145 and result['words'][0]['probability']==.589
    assert 'word_row_text_difference' in result['uncertainty'][0]['flags']

def test_usable_rows_do_not_require_word_metadata():
    row=response();row['words']=[];row['word_segments'][0].update(word_first=0,word_last=0)
    provider=object.__new__(FinalSpeechProviders);provider._selected=lambda *a:(row,NS(revision='fixed'))
    assert provider.recognize_tutorial(RecognitionRequest(bytes(32000)),None)['rows'][0]['text']=='A word.'

def feature(values):
    raw=array.array('H',values).tobytes()
    return dict(rate=48000,samples=len(values)*384,zeros=[],native_quiet=dict(policy='native-quiet-v1-allchannels-f64-maxrms8ms-u16ceil-3900s',rate=125,count=len(values),data=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest()))

def test_source_facts_are_immutable_and_loud_relative_quiet_is_not_a_barrier():
    source=feature([1000]*375);source['zeros']=[[0,448],[96000,144000]];before=deepcopy(source)
    pauses=pause_map(source,144000)
    assert pauses.mandatory==((0,448),(96000,144000)) and source==before
    activity=[dict(offset=g,scores=[.95]*((48000-g)//512)) for g in (0,256)]
    assert _sustained_empty(activity,source,0,48000)
    assert not _sustained_empty([dict(offset=g,scores=[.1]*((48000-g)//512)) for g in (0,256)],source,0,48000)

@pytest.mark.parametrize('gap,loan',[(.344,.264),(.8,.4),(1.,0),(3.,0)])
def test_gap_admission_cap_and_long_pause(gap,loan):
    phrase=NS(ordinal=0,start=0,end=10*48000);following=NS(start=phrase.end+round(gap*48000))
    pauses=PauseMap(20*48000,(),(),())
    slot=slot_for(phrase,following,pauses,True)
    assert slot.loan==round(loan*48000)
    assert slot.maximum_end<=following.start-1920
    assert slot_for(phrase,following,pauses,False).loan==0
    assert slot_for(phrase,None,pauses,True).loan==0

def test_natural_then_minimum_loan_then_tempo_then_actionable_refusal():
    phrase=NS(ordinal=0,start=0,end=48000);following=NS(start=86400)
    slot=slot_for(phrase,following,PauseMap(96000,(),(),()),True)
    assert fit_phrase(slot,30000).ratio==1 and fit_phrase(slot,30000).end==48000
    loaned=fit_phrase(slot,50000);assert loaned.ratio==1 and 48000<loaned.end<slot.maximum_end
    accelerated=fit_phrase(slot,70000);assert 1<accelerated.ratio<=Fraction(5,4)
    with pytest.raises(TimingError) as e:fit_phrase(slot,100000)
    assert e.value.code=='phrase_timing_unfit' and e.value.timing_facts['neutral_samples']==100000

def test_barrier_collision_and_no_following_delay():
    pauses=PauseMap(240000,(),(),((96000,144000),))
    with pytest.raises(TimingError):slot_for(NS(ordinal=0,start=48000,end=120000),None,pauses)
    slot=slot_for(NS(ordinal=0,start=0,end=90000),NS(start=144000),pauses,True)
    assert slot.maximum_end<=96000

def test_real_owned_metadata_pages_hmac_and_retirement(tmp_path):
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
    from mediaforge.features.dub.contracts import DubControl
    paths=ProfilePaths.for_test(tmp_path/'portable');store=SpeechSessions(paths);control=DubControl()
    request=NS(retry_session=None,retain_private_recovery=True)
    value=dict(text='число != 42; '*6000)
    with store.admission(control),store.open(request,dict(policy='TUTORIAL-DUB-V2'),control) as session:
        write_record(session,'v2-test',value);assert read_record(session,'v2-test')==value
        write_record(session,'v2-clock',dict(clock=(0,16000)))
        count=len(session.ticket['files']);write_record(session,'v2-clock',dict(clock=(0,16000)))
        assert read_record(session,'v2-clock')['clock']==[0,16000] and len(session.ticket['files'])==count
        assert session.budget['metadata']>=len(json.dumps(value,ensure_ascii=False).encode())
        assert all(row['bytes']<=49152 for name,row in session.ticket['files'].items())
        session.admit_records(100,1024)
        with pytest.raises(LocalAiError):session.admit_records(16000,0)
        with pytest.raises(LocalAiError):session.admit_records(0,32*1024**2)
        with pytest.raises(LocalAiError):write_record(session,'v2-test',dict(text='substituted'))
        name=session.path.name
    store.retire(name,control);assert not store._tickets

@pytest.mark.parametrize('mode',['text','missing_speech','silence'])
def test_complete_v2_observation_coverage_and_no_blank_success(tmp_path,mode):
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
    from mediaforge.features.dub.contracts import DubControl
    from mediaforge.application.services.tutorial_speech import source_phrase_rows
    paths=ProfilePaths.for_test(tmp_path/'portable');store=SpeechSessions(paths);control=DubControl();calls=[]
    ledger=feature([1000 if mode!='silence' else 0]*625)
    if mode=='silence':ledger['zeros']=[[0,240000]]
    before=deepcopy(ledger)
    def recognize(request,control):
        calls.append(len(request.pcm16)//2)
        return dict(schema='tutorial-asr-observation-v2',window=[0,80000],rows=[dict(start=0,end=240000,text='Do not remove number 17.',estimates=[])] if mode=='text' else [],
                    activity=[dict(offset=g,scores=[.95]*((80000-g)//512)) for g in (0,256)],uncertainty=[dict(row=0,flags=['low_native_confidence'])])
    with store.admission(control),store.open(NS(retry_session=None,retain_private_recovery=True),dict(policy='TUTORIAL-DUB-V2'),control) as session:
        extraction=session.put_bytes('extract.pcm',bytes(160000));name=session.path.name
        stream=source_phrase_rows(ledger,extraction,80000,NS(recognize_tutorial=recognize),session,control,lambda *a:None,total_samples=240000)
        if mode!='text':
            with pytest.raises(LocalAiError) as error:list(stream)
            assert error.value.code==('unrecognized_speech' if mode=='missing_speech' else 'no_speech')
        else:
            result=list(stream);plan=read_record(session,'v2-plan')
            assert ''.join(x[0].text for x in result)=='Do not remove number 17.'
            assert plan['native_characters']==plan['owned_characters']==24
            assert plan['coverage'][0]['window']==[0,80000] and plan['pauses']['mandatory']==[]
        assert ledger==before and calls==([] if mode=='silence' else [80000])
    store.retire(name,control)


@pytest.mark.parametrize('mode',['complete','missing_eos','cancel'])
def test_v2_composition_requires_eos_keeps_microzero_policy_and_retires(tmp_path,mode):
    """Real session/HMAC/PCM lifecycle, synthetic audio; not native AI evidence."""
    from mediaforge.application.services.speech import DubSpeechService
    from mediaforge.application.contracts.local_ai import PcmFragment,CompletionReceipt
    from mediaforge.application.contracts.speech import PhraseSegmentV2
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
    from mediaforge.features.dub.contracts import DubControl
    paths=ProfilePaths.for_test(tmp_path/'portable');store=SpeechSessions(paths);control=DubControl()
    class Audio:
        def convert(self,source,target,control,*,rate,**kw):
            assert rate==24000 and not kw
            v=array.array('h',source.read_bytes());target.write_bytes(array.array('h',(x for x in v for _ in (0,1))).tobytes())
            return target.stat().st_size//2
        def level(self,source,target,control,total):
            target.write_bytes(source.read_bytes());return (-18.,-4.),()
    def synthesize(text,voice):
        if mode=='cancel':control.cancel()
        completion=None if mode=='missing_eos' else CompletionReceipt('normal_eos',True,12,12,13,1500)
        return PcmFragment(b'\x01\x00'*24000,24000,'fixed',voice,completion)
    speech=DubSpeechService(None,Audio(),store,None,tts=NS(sample_rate=24000));speech.last_receipt={'tutorial_v2':{}}
    row=PhraseSegmentV2(1600,33333,'Do not erase 42.')
    phrase=Phrase(0,4800,100000,row.text,())
    anchor=dict(start=4800,end=100000,phrase=phrase.record())
    pauses=PauseMap(144000,((0,4800),(30000,30100),(120000,144000)),(),((0,4800),(120000,144000)))
    plan=dict(pauses=asdict(pauses),phrases=1,uncertainty_count=0)
    with store.admission(control),store.open(NS(retry_session=None,retain_private_recovery=True),dict(policy='TUTORIAL-DUB-V2'),control) as session:
        name=session.path.name
        session.put('v2-translate-0.json',dict(english_sha256=hashlib.sha256(row.text.encode()).hexdigest(),text='Не стирайте 42.'))
        run=lambda:speech._compose(NS(plan=NS(sample_count=144000),voice='aiden'),session,control,lambda *a:None,iter([(row,(0,),anchor)]),dict(rate=48000,zeros=pauses.hard),'testgen',NS(synthesize=synthesize),1,tutorial=plan)
        if mode=='complete':
            result=run();raw=array.array('h',result.path.read_bytes())
            assert len(raw)==144000 and result.protected_silence==pauses.mandatory
            assert any(raw[30000:30100])  # short source microzero isn't a V2 output barrier
            assert not any(raw[:4800]) and not any(raw[120000:])
            assert session.get('v2-eos-testgen-0.json')['units'][0]['completion']['terminal_eos']
            assert session.get('v2-schedule-testgen-0.json')['tempo']=='1'
            assert not session.owns_name('v2-neutral-0.pcm') and not session.owns_name('v2-voice-0.json')
        else:
            with pytest.raises(Exception) as error:run()
            assert error.value.code==('invalid_output' if mode=='missing_eos' else 'cancelled')
            assert not session.owns_name('v2-schedule-testgen-0.json')
    store.retire(name,DubControl());assert not store._tickets


def test_real_qwen_reply_exposes_immutable_authenticated_eos():
    from dataclasses import FrozenInstanceError
    from tests.unit.test_qwen_session import engine,fragment
    result=engine(fragment()).synthesize('Привет.','ryan')
    assert result.completion.reason=='normal_eos' and result.completion.terminal_eos
    assert result.completion.generated_steps==result.completion.codec_frames+1
    with pytest.raises(FrozenInstanceError):result.completion.terminal_eos=False


def test_v1_retry_binding_and_changed_v2_page_are_refused(tmp_path):
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
    from mediaforge.features.dub.contracts import DubControl
    paths=ProfilePaths.for_test(tmp_path/'portable');store=SpeechSessions(paths);control=DubControl()
    request=NS(retry_session=None,retain_private_recovery=True)
    with store.admission(control),store.open(request,dict(policy='strict-v1'),control) as session:
        write_record(session,'v2-private',dict(text='Keep every character.'));name=session.path.name;page=session.name('v2-private-p0.json')
    with pytest.raises(LocalAiError) as error:
        with store.open(NS(retry_session=name,retain_private_recovery=True),dict(policy='TUTORIAL-DUB-V2'),control):pass
    assert error.value.code=='checkpoint_ineligible'
    page.write_bytes(b'changed')
    with pytest.raises(LocalAiError) as error:
        with store.open(NS(retry_session=name,retain_private_recovery=True),dict(policy='strict-v1'),control) as session:read_record(session,'v2-private')
    assert error.value.code=='checkpoint_ineligible'
    store.retire(name,control);assert not store._tickets
