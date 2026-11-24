"""Raw attention estimates cannot move acoustically qualified source anchors."""
from copy import deepcopy
from fractions import Fraction as F
import io
from types import SimpleNamespace as NS
import pytest
from mediaforge.features.dub.alignment import Support
from mediaforge.features.dub.observation_envelopes import plan,assign
from mediaforge.features.dub.speech import TimingError
from mediaforge.application.services import speech_islands as service
from tests.unit.test_observation_envelopes import case,Provider,Session,word,support


def test_source_leading_zero_and_raw_word_estimate_both_survive_assignment():
    source=(Support(F(448,3),F(10000),448,30000,48000),)
    words=[word(0,1600,'A'),word(1600,3200,'word')];before=deepcopy(words)
    view=assign(plan(source,16000)[0],source,words,'A word')[0]
    assert words==before and view.words[0]['start']==0
    assert source[0].start==F(448,3) and source[0].native_start==448


def test_point_estimate_keeps_word_and_no_fabricated_sample_duration():
    source=(support(100,500),)
    words=[word(100,300,'A'),word(300,300,'small'),word(300,500,'word')]
    view=assign(plan(source,1000)[0],source,words,'A small word')[0]
    assert view.word_indices==(0,1,2) and view.words[1]['start']==view.words[1]['end']==300
    with pytest.raises(TimingError,match='Point-only'):
        assign(plan(source,1000)[0],source,[word(300,300,'word')],'word')


@pytest.mark.parametrize('a,b',[(0,50),(500,700),(100,801)])
def test_no_acoustic_owner_or_two_owners_cannot_be_rescued_by_estimate(a,b):
    source=(support(100,500),support(800,1300))
    with pytest.raises(TimingError):
        assign(plan(source,1600)[0],source,[word(a,b,'one'),word(900,1200,'two')],'one two')


def test_local_uncertainty_remains_bounded_to_500ms():
    source=(support(16000,32000),);envelope=plan(source,40000)[0]
    assert assign(envelope,source,[word(8000,30000,'word')],'word')
    with pytest.raises(TimingError,match='bounds'):
        assign(envelope,source,[word(7999,30000,'word')],'word')


def test_real_timing_predicate_keeps_original_anchors_and_uncertain_raw_clock():
    prepared,value,pcm=case();value['words'][0]['start']-=1
    provider=Provider(value);session=Session();before=deepcopy(value)
    rows,pauses=service.admit_envelope(prepared,0,io.BytesIO(pcm),len(pcm)//2,provider,session,NS(check=lambda:None),lambda *a:None)
    assert len(rows)==1 and not pauses
    segment,_,anchor=rows[0]
    assert (anchor['start'],anchor['end'])==(4096*3,20480*3)
    assert anchor['correction']=='1' and segment.start_sample==4095
    assert value==before and anchor['derived']['word_time_basis']=='estimates-not-acoustic-boundaries'


def test_uncertain_clock_does_not_rescue_low_confidence_or_grant_fitting_space():
    prepared,value,pcm=case();value['words'][0].update(start=4095,probability=.01)
    with pytest.raises(TimingError,match='confidence'):
        service.admit_envelope(prepared,0,io.BytesIO(pcm),len(pcm)//2,Provider(value),Session(),NS(check=lambda:None),lambda *a:None)
