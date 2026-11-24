"""Provisional quiet cuts cannot own words before linguistic/pause verification."""
import array
from copy import deepcopy
from fractions import Fraction as F
import io
from types import SimpleNamespace as NS
import pytest
from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.application.services import speech_islands as service
from mediaforge.features.dub.observation_envelopes import plan,fingerprint
from mediaforge.features.dub.practical_timing import admit_hard_island
from mediaforge.features.dub.alignment import Support
from mediaforge.features.dub.speech import TimingError
from tests.unit.test_observation_envelopes import case,Provider,Session,support
from tests.unit.test_practical_timing import fixture

def test_false_provisional_word_cut_does_not_override_complete_phrase_evidence():
    prepared,value,pcm=case();original=prepared['hard_support']
    provisional=(support(4096,11000),support(11200,20480))
    prepared.update(support=provisional,envelopes=plan(provisional,len(pcm)//2),
        fingerprint=fingerprint(plan(provisional,len(pcm)//2),provisional,len(pcm)//2))
    before=deepcopy(value)
    rows,pauses=service.admit_envelope(prepared,0,io.BytesIO(pcm),len(pcm)//2,Provider(value),Session(),NS(check=lambda:None),lambda *a:None)
    assert len(rows)==1 and rows[0][0].text=='Keep every word.' and not pauses
    assert rows[0][2]['start']==4096*3 and rows[0][2]['end']==20480*3
    assert prepared['hard_support']==original and value==before

def test_joint_phrase_still_requires_complete_word_pause_and_native_confidence():
    source,activity,zeros,window,words,text,pcm=fixture(pause=True)
    prepared=dict(hard_support=(source,),support=(support(4096,10496),support(13056,20480)),zeros=zeros,brackets=zeros,pauses=[])
    prepared['envelopes']=plan(prepared['support'],window[1]);prepared['fingerprint']=fingerprint(prepared['envelopes'],prepared['support'],window[1])
    value=dict(rows=[dict(start_sample=4096,end_sample=20480,text=text)],words=words,
        confidence=[dict(avg_logprob=-.1,no_speech_prob=.01)],activity=activity)
    rows,pauses=service.admit_envelope(prepared,0,io.BytesIO(pcm),window[1],Provider(value),Session(),NS(check=lambda:None),lambda *a:None)
    assert [row[0].text for row in rows]==['Keep','every word.']
    assert pauses==((F(10496),F(13056)),)
    assert rows[0][2]['end']==10496*3 and rows[1][2]['start']==13056*3
    value['confidence'][0]['avg_logprob']=-.70001
    with pytest.raises(LocalAiError,match='confidence'):
        service.admit_envelope(prepared,0,io.BytesIO(pcm),window[1],Provider(value),Session(),NS(check=lambda:None),lambda *a:None)

def test_original_hard_silence_cannot_be_bridged_by_joint_phrase():
    prepared,value,pcm=case()
    prepared['hard_support']=(support(4096,11000),support(11200,20480))
    with pytest.raises(TimingError,match='one acoustic support owner'):
        service.admit_envelope(prepared,0,io.BytesIO(pcm),len(pcm)//2,Provider(value),Session(),NS(check=lambda:None),lambda *a:None)

def test_short_leading_hard_zero_keeps_exact_clock_and_file_edge_semantics():
    source,activity,zeros,window,words,text,pcm=fixture()
    source=Support(F(448,3),source.end,448,61440,48000)
    zeros=[(F(0),source.start),zeros[-1]];words[0]['start']=0
    raw=array.array('h',pcm);raw[150:4096]=array.array('h',[1000]*(4096-150))
    for row in activity:row['scores'][:8]=[.95]*8
    before=deepcopy(words)
    with pytest.raises(TimingError,match='non-speech context'):
        admit_hard_island(source,activity,zeros,window,words,text,raw.tobytes(),estimated_outer=True,file_samples=window[1])
    parts,pauses=admit_hard_island(source,activity,zeros,window,words,text,raw.tobytes(),estimated_outer=True,file_samples=window[1],hard_zeros=zeros)
    assert parts[0].start==F(448,3) and parts[0].end==20480 and not pauses
    assert parts[0].evidence['exact_file_edges']['start'] and words==before
    words[0]['probability']=.0461
    with pytest.raises(TimingError,match='confidence'):
        admit_hard_island(source,activity,zeros,window,words,text,raw.tobytes(),estimated_outer=True,file_samples=window[1],hard_zeros=zeros)
