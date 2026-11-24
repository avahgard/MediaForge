from contextlib import contextmanager
from copy import deepcopy
from fractions import Fraction as F
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from mediaforge.application.contracts.local_ai import LocalAiError, RecognitionResult, SpeechSegment
from mediaforge.application.services import speech_islands as service
from mediaforge.features.dub.alignment import Support, supports
from mediaforge.features.dub.observation_envelopes import plan, fingerprint, assign, native_word_rows
from mediaforge.features.dub.observation_join import windows
from mediaforge.features.dub.speech import TimingError
from tools.run_asr_envelope_prefix import prefix_gate, source_record
from tests.unit.test_practical_timing import fixture


def support(a, b):
    return Support(F(a), F(b), a, b, 16000)


class Session:
    def __init__(self): self.rows = {}
    def get(self, name): return deepcopy(self.rows.get(name))
    def put(self, name, row):
        if len(json.dumps(row).encode()) > 60000:
            raise LocalAiError('disk_budget', 'Checkpoint row exceeds 64 KiB.')
        self.rows[name] = deepcopy(row)


def test_short_support_has_unchanged_context_and_tiny_supports_are_not_timing_merged():
    original = (support(4096, 20480),)
    before = deepcopy(original)
    group = plan(original, 24576)
    assert len(group) == 1 and (group[0].begin, group[0].stop) == service.island_window(original, 0, 24576)
    tiny = (support(1000, 3688), support(7000, 9700), support(12000, 15000))
    grouped = plan(tiny, 18000)
    assert len(grouped) == 1 and (grouped[0].first, grouped[0].last) == (0, 2)
    assert original == before and grouped[0].start == tiny[0].start and grouped[0].end == tiny[-1].end
    assert fingerprint(grouped, tiny, 18000) == fingerprint(plan(tiny, 18000), tiny, 18000)


def test_exact_26_30_52_56_limits_gap_accounting_and_native_fractional_clock():
    groups = plan((support(32000, 28*16000), support(28*16000+1, 29*16000)), 32*16000)
    assert len(groups) == 2 and groups[0].end-groups[0].start == 26*16000
    assert windows(0, 30*16000) == ((0, 30*16000),)
    assert len(windows(0, 30*16000+1)) == 2
    long = plan((support(32000, 54*16000),), 56*16000)[0]
    assert (long.begin, long.stop) == (0, 56*16000)
    assert windows(long.begin, long.stop) == ((0,480000),(416000,896000))
    with pytest.raises(TimingError): plan((support(0, 52*16000+1),), 56*16000)
    with pytest.raises(TimingError): windows(0, 56*16000+1)
    separated = plan((support(0, 100), support(26*16000, 26*16000+100)), 27*16000)
    assert len(separated) == 2  # gaps count; no speech-only length packing
    fractional = Support(F(16000,44100), F(10001*16000,44100), 1,10001,44100)
    assert plan((fractional,), 16000)[0].start == fractional.start
    with pytest.raises(TimingError): plan((support(10,100), support(90,200)), 1000)


def word(a,b,text='word',p=.95): return dict(start=a,end=b,text=text,probability=p)


def test_whole_word_views_keep_immutable_native_words_parent_rows_and_exact_text():
    original = (support(100,500), support(800,1300))
    envelope = plan(original, 1500)[0]
    words = [word(100,300,' One,'), word(800,1300,' two.')]
    before = deepcopy(words)
    rows = [dict(text='One, two.')]
    assert native_word_rows(rows, words) == (0,0)
    views = assign(envelope, original, words, 'One, two.')
    assert [v.index for v in views] == [0,1] and [v.word_indices for v in views] == [(0,),(1,)]
    assert words == before and [v.text for v in views] == ['One,','two.']
    with pytest.raises(TypeError): views[0].words[0]['start'] = 99


@pytest.mark.parametrize('damage', ['cross','pause','missing','duplicate','reverse','extra','nan','text','context'])
def test_complete_word_ownership_refuses_every_ambiguous_or_incomplete_partition(damage):
    original = (support(100,500), support(800,1300))
    words = [word(100,400,'one'), word(800,1200,'two')]
    text = 'one two'
    if damage == 'cross': words[0]['end'] = 801 # two genuinely overlapping acoustic owners
    elif damage == 'pause': words.insert(1, word(600,700,'extra')); text = 'one extra two'
    elif damage == 'missing': words.pop(); text = 'one'
    elif damage == 'duplicate': words.insert(1,deepcopy(words[0])); text = 'one one two'
    elif damage == 'reverse': words.reverse()
    elif damage == 'extra': words.append(word(1250,1400,'extra')); text += ' extra'
    elif damage == 'nan': words[1]['probability'] = float('nan')
    elif damage == 'text': text = 'one different'
    else: words.insert(0,word(0,50,'context')); text = 'context '+text
    with pytest.raises(TimingError): assign(plan(original,1500)[0],original,words,text)


def test_native_parent_row_partition_never_fabricates_per_support_confidence():
    words = [word(10,20,'one'),word(30,40,'two'),word(50,60,'three')]
    assert native_word_rows([dict(text='one two'),dict(text='three')],words) == (0,0,1)
    with pytest.raises(TimingError): native_word_rows([dict(text='one'),dict(text='different')],words)


class Provider:
    def __init__(self, value): self.value=value; self.calls=[]
    def binding(self): return dict(model='frozen-test-model',runtime='frozen-test-runtime')
    def recognize_words_with_activity(self, request, control):
        self.calls.append((request.offset_samples,len(request.pcm16)//2))
        value=deepcopy(self.value)
        result=RecognitionResult(tuple(SpeechSegment(**r) for r in value['rows']),len(request.pcm16)//2,'test')
        return result,value['words'],value['confidence'],value['activity']


def case():
    source,activity,zeros,window,words,text,pcm = fixture()
    value = dict(rows=[dict(start_sample=words[0]['start'],end_sample=words[-1]['end'],text=text)],
                 words=words, confidence=[dict(avg_logprob=-.1,no_speech_prob=.01)],activity=activity)
    original=(source,); envelopes=plan(original,window[1])
    prepared=dict(support=original,hard_support=original,zeros=zeros,brackets=zeros,envelopes=envelopes,
                  fingerprint=fingerprint(envelopes,original,window[1]),pauses=[])
    return prepared,value,pcm


def test_original_working_support_uses_real_timing_predicate_and_derived_native_references():
    prepared,value,pcm=case(); provider=Provider(value); session=Session()
    service.bind_envelope_plan(prepared,session)
    output,pauses=service.admit_envelope(prepared,0,io.BytesIO(pcm),len(pcm)//2,provider,session,NS(check=lambda:None),lambda *a:None)
    assert len(output)==1 and not pauses and len(provider.calls)==1
    segment,lineage,anchor=output[0]
    assert (anchor['start'],anchor['end'])==(4096*3,20480*3) and segment.text=='Keep every word.'
    assert lineage==(0,0,0) and anchor['raw']==[] and anchor['derived']['schema']=='support-view-v1-native-parent-references'
    refs=anchor['derived']['words']; assert [r['word'] for r in refs]==[0,1,2] and all(r['row']==0 for r in refs)
    assert value['words'][0]['start']==4096


@pytest.mark.parametrize('failure',['provider','confidence','no_speech','assignment','timing','cancel','quota'])
def test_envelope_refusals_emit_nothing_and_controller_never_starts_the_next_envelope(failure):
    prepared,value,pcm=case(); control=NS(check=lambda:None)
    if failure=='confidence': value['confidence'][0]['avg_logprob']=-.70001
    if failure=='no_speech': value['confidence'][0]['no_speech_prob']=.10001
    if failure=='assignment': value['words'][0]['end']=4095 # wholly outside its acoustic owner
    if failure=='timing': value['words'][0]['probability']=.1
    if failure=='cancel':
        def cancelled(): raise LocalAiError('cancelled','injected')
        control=NS(check=cancelled)
    provider=Provider(value);calls=[];retired=[];session=Session()
    if failure=='provider':
        def failed_provider(request,control):
            provider.calls.append('provider'); raise LocalAiError('runtime_integrity','injected native refusal')
        provider.recognize_words_with_activity=failed_provider
    if failure=='quota':
        def quota(*args): raise LocalAiError('disk_budget','injected metadata quota refusal')
        session.put=quota
    @contextmanager
    def owners():
        try: yield
        finally: retired.append(True)
    def admit(p,index):
        calls.append(index)
        return service.admit_envelope(p,index,io.BytesIO(pcm),len(pcm)//2,provider,session,control,lambda *a:None)
    with pytest.raises((TimingError,LocalAiError)) as error, owners(): prefix_gate(lambda:prepared,admit)
    assert calls==[0] and retired==[True] and len(provider.calls)==(0 if failure=='cancel' else 1)
    assert error.value.admission_context['envelope']==0


def test_planning_failure_preserves_first_error_and_does_not_enter_admission():
    error=LocalAiError('speech_boundary_uncertain','first planning refusal');called=[]
    def prepare(): raise error
    with pytest.raises(LocalAiError) as caught: prefix_gate(prepare,lambda *a:called.append(a))
    assert caught.value is error and called==[] and error.admission_context['stage']=='planning'


def test_controller_source_identity_is_json_safe_without_changing_file_facts(tmp_path):
    from mediaforge.features.dub.contracts import SourceIdentity
    identity=SourceIdentity(tmp_path/'Учебное видео.mp4',1,2,3,4,'a'*64)
    row=source_record(identity)
    assert json.loads(json.dumps(row))==row
    assert row==dict(path=str(identity.path),device=1,file_id=2,size=3,mtime_ns=4,sha256='a'*64)


def test_real_checkpoint_payload_cap_and_hmac_source_binding_are_not_relaxed(tmp_path):
    from mediaforge.features.dub.contracts import DubControl
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
    paths=ProfilePaths.for_test(tmp_path/'portable/Data/v2');paths.prepare()
    store=SpeechSessions(paths);control=DubControl()
    request=NS(retry_session=None,retain_private_recovery=True)
    with store.open(request,dict(source='one',track=1,plan='frozen'),control) as session:
        name=session.path.name
        with pytest.raises(LocalAiError,match='64 KiB'):
            session.put('envelope-native-0-0.json',dict(text='x'*60000))
        session.put('observation-plan.json',dict(plan='frozen'))
    with pytest.raises(LocalAiError):
        with store.open(NS(retry_session=name,retain_private_recovery=True),dict(source='changed',track=1,plan='frozen'),control): pass
    assert store.MAX_BYTES==1024**3 and store.MAX_METADATA==32*1024**2 and store.MAX_FILES==16000
    store.close(control)


def test_later_child_timing_failure_is_atomic_for_the_entire_envelope(monkeypatch):
    original=(support(4096,8192), support(12288,16384))
    envelope=plan(original,20480)[0]
    prepared=dict(support=original,hard_support=original,envelopes=(envelope,),zeros=[],brackets=[],fingerprint=fingerprint((envelope,),original,20480),pauses=[])
    value=dict(rows=[dict(start_sample=4096,end_sample=16384,text='one two')],
               words=[word(4096,8192,'one'),word(12288,16384,'two')],confidence=[dict(avg_logprob=-.1,no_speech_prob=.01)],activity=[])
    calls=[]
    def timing(source, activity, brackets, window, words, text, raw, **kw):
        calls.append((source,window,activity))
        if len(calls)==2: raise TimingError('speech_boundary_uncertain','second child refused')
        return (NS(start=source.start,end=source.end,words=words,text=text,estimated=True,evidence={}),),()
    monkeypatch.setattr(service,'admit_hard_island',timing)
    with pytest.raises(TimingError) as error:
        service.admit_envelope(prepared,0,io.BytesIO(bytes(40960)),20480,Provider(value),Session(),NS(check=lambda:None),lambda *a:None)
    assert len(calls)==2 and all(c[1]==(envelope.begin,envelope.stop) for c in calls)
    assert error.value.admission_context['support_index']==1


@pytest.mark.parametrize('change',['plan','source_boundary','runtime','word_index','old_namespace'])
def test_checkpoint_substitutions_cannot_reuse_native_evidence(change):
    prepared,value,pcm=case(); provider=Provider(value);session=Session();control=NS(check=lambda:None)
    service.bind_envelope_plan(prepared,session)
    service.admit_envelope(prepared,0,io.BytesIO(pcm),len(pcm)//2,provider,session,control,lambda *a:None)
    name='envelope-native-0-0.json'
    if change=='plan': session.rows[name]['identity']['plan']['sha256']='0'*64
    elif change=='runtime': session.rows[name]['identity']['runtime']['runtime']='different'
    elif change=='source_boundary': session.rows[name]['identity']['envelope']['start']='4095'
    elif change=='word_index':
        # Parent row/text mismatch; a session HMAC tamper is covered separately
        # by the actual session tests. No derived reference is accepted as native.
        session.rows[name]['words'][0]['text']='substituted'
    else:
        session.rows['source-recognize-0-0.json']=session.rows.pop(name)
        provider.value['confidence'][0]['avg_logprob']=-1
    with pytest.raises((LocalAiError,TimingError)):
        service.admit_envelope(prepared,0,io.BytesIO(pcm),len(pcm)//2,provider,session,control,lambda *a:None)
    assert len(provider.calls)==(2 if change=='old_namespace' else 1)


def test_frozen_nasa_geometry_has_exact_151_envelopes_without_recognition():
    location=os.environ.get('MEDIAFORGE_TEST_ASR_LEDGER')
    if not location: pytest.skip('Explicit preserved NASA ledger required; no automatic source acquisition.')
    raw=Path(location).read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='4494efea44b2808ba8b109614c4635cecc396c7bae8735e517e2984a3677f99c'
    ledger=json.loads(raw); original=supports(ledger['zeros'],ledger['samples'],ledger['rate'])
    prepared=service.prepare_envelopes(original,ledger,59329008)
    groups=prepared['envelopes']
    assert len(prepared['support'])==1434 and len(groups)==151
    assert [(e.first,e.last,e.begin,e.stop) for e in groups[:3]]==[
        (0,10,0,403392),(11,17,403392,801152),(18,24,801152,1219328)]
    assert [i for e in groups for i in range(e.first,e.last+1)]==list(range(1434))
    assert ledger['zeros']==[[0,448],[6002688,6690752],[177966080,177987024]]
