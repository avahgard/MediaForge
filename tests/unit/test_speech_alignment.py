"""Bounded reconciliation/cap/cache controls, separate from real-model evidence."""
from fractions import Fraction as F
from types import SimpleNamespace as NS
import json
import pytest
from mediaforge.application.contracts.local_ai import SpeechSegment,LocalAiError
from mediaforge.application.services.speech_alignment import anchored_rows
from mediaforge.features.dub.alignment import Support

class Cache:
    def __init__(self):self.rows={}
    def get(self,key):return self.rows.get(key)
    def put(self,key,value):assert key not in self.rows;self.rows[key]=json.loads(json.dumps(value))

def fixture(tmp_path,*,two=False,missing=False):
    path=tmp_path/'pcm';path.write_bytes(bytes(128000*2));calls=[]
    support=[Support(F(32000),F(48000),32000,48000,16000)]
    if two:support.append(Support(F(80000),F(96000),80000,96000,16000))
    zeros=[(0,32000),(48000,80000),(96000,128000)] if two else [(0,32000),(48000,128000)]
    def activity(pcm,offset,control):
        rows=[]
        for shift in (0,256):
            probabilities=[]
            for j in range((len(pcm)//2-shift)//512):
                a=offset+shift+j*512;b=a+512
                probabilities.append(.95 if any(s.start<=a and b<=s.end for s in support) else .05)
            rows.append(dict(offset=offset+shift,scores=probabilities))
        return rows
    def recognize(request,control):
        calls.append((request.offset_samples,len(request.pcm16)//2))
        row=SpeechSegment(32000,48000,'One.') if len(calls)==1 else SpeechSegment(80000,96000,'Wrong.' if missing else 'Two.')
        return NS(segments=(row,))
    return path,support,dict(rate=16000,zeros=zeros),NS(activity=activity,recognize=recognize),calls

def collect(tmp_path,raw,*,two=False,missing=False,cache=None):
    path,support,ledger,providers,calls=fixture(tmp_path,two=two,missing=missing)
    result=list(anchored_rows(iter([(raw,(0,))]),support,ledger,path,128000,providers,cache or Cache(),NS(check=lambda:None),lambda *a:None))
    return result,calls

@pytest.mark.parametrize('delta',[-8000,8000])
def test_local_signed_500ms_correction_does_not_change_source_clock(tmp_path,delta):
    rows,calls=collect(tmp_path,SpeechSegment(32000-delta,48000,'One.'))
    assert rows[0][2]['start']==96000 and rows[0][2]['end']==144000 and rows[0][2]['correction']==str(delta)
    assert not calls

@pytest.mark.parametrize('delta',[-8001,8001])
def test_over_500ms_refuses_without_a_global_offset(tmp_path,delta):
    with pytest.raises(LocalAiError) as error:collect(tmp_path,SpeechSegment(32000-delta,48000,'One.'))
    assert error.value.code=='speech_boundary_uncertain'

def test_pause_crossing_row_has_one_bounded_reanalysis_per_island_and_full_coverage(tmp_path):
    cache=Cache();rows,calls=collect(tmp_path,SpeechSegment(30000,96000,'One. Two.'),two=True,cache=cache)
    assert [r[0].text for r in rows]==['One.','Two.'] and [r[2]['start'] for r in rows]==[96000,240000]
    assert len(calls)==2 and all(n<=480000 for _,n in calls)
    assert len([k for k in cache.rows if k.startswith('boundary-recognize')])==2
    again,second_calls=collect(tmp_path,SpeechSegment(30000,96000,'One. Two.'),two=True,cache=cache)
    assert again==rows and second_calls==[] # same-process authenticated cache injected at this boundary
    assert json.loads(json.dumps(rows[0][2]))==rows[0][2] # native retry regression: nested anchor must remain equal

def test_missing_boundary_word_refuses_instead_of_splitting_text(tmp_path):
    with pytest.raises(LocalAiError,match='complete ordered word coverage'):
        collect(tmp_path,SpeechSegment(30000,96000,'One. Two.'),two=True,missing=True)

def test_unmatched_nonzero_source_island_cannot_be_silently_dropped(tmp_path):
    with pytest.raises(LocalAiError):collect(tmp_path,SpeechSegment(80000,96000,'Two.'),two=True)
