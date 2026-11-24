from copy import deepcopy
import io
import json
from types import SimpleNamespace as NS

import pytest

from mediaforge.features.dub.observation_join import windows,join
from mediaforge.features.dub.practical_timing import bounded_runs
from mediaforge.features.dub.alignment import activity_runs
from mediaforge.features.dub.speech import TimingError
from mediaforge.application.services.speech_islands import observe_complete


def observation(a,b):
    words=[dict(start=i*16000+1600,end=i*16000+12000,text='word'+str(i),probability=.95)
           for i in range(56) if a<=i*16000+1600 and i*16000+12000<=b]
    text=' '.join(w['text'] for w in words)
    return dict(words=words,rows=[dict(start_sample=words[0]['start'],end_sample=words[-1]['end'],text=text)],
        confidence=[dict(avg_logprob=-.1,no_speech_prob=.01)],
        activity=[dict(offset=a+grid,scores=[.9]*((b-a-grid)//512)) for grid in (0,256)])


def test_two_predetermined_native_windows_cover_complete_utterance_without_a_source_seam():
    assert windows(100,100+30*16000)==((100,480100),)
    assert windows(0,56*16000)==((0,480000),(416000,896000))
    a,b=observation(0,480000),observation(416000,896000)
    result=join(a,b,0,896000)
    assert [w['text'] for w in result['words']]==['word'+str(i) for i in range(56)]
    assert [r['offset'] for r in result['activity']]==[0,256]
    assert [len(r['scores']) for r in result['activity']]==[1750,1749]
    assert len(result['confidence'])==2 and len(result['rows'])==1
    assert a==observation(0,480000) and b==observation(416000,896000)
    with pytest.raises(TimingError):windows(0,56*16000+1)


@pytest.mark.parametrize('change',['text','low_word','word_clock','ownership','vad'])
def test_seam_disagreement_refuses_without_reroll_clipping_or_other_join(change):
    a,b=observation(0,480000),observation(416000,896000)
    word=next(w for w in b['words'] if w['text']=='word28')
    if change=='text':word['text']='different'
    elif change=='low_word':word['probability']=.79
    elif change=='word_clock':word['end']+=1281
    elif change=='ownership':word.update(start=28*16000-6400,end=28*16000)
    else:b['activity'][1]['scores'][62]=.1
    with pytest.raises(TimingError):join(a,b,0,896000)


def test_extended_vad_runs_keep_exact_original_threshold_and_three_bin_semantics():
    values=([.1,.9,.9,.9,.9,.1,.1,.1,.5]*210)[:1750]
    def oracle(speech):
        selected=[i for i,p in enumerate(values) if (p>=.8 if speech else p<=.2)]
        groups=[]
        for i in selected:
            if groups and i==groups[-1][-1]+1:groups[-1].append(i)
            else:groups.append([i])
        return tuple((100+g[0]*512,100+(g[-1]+1)*512) for g in groups if len(g)>=3)
    for speech in (True,False):
        assert bounded_runs(values,100,speech=speech)==oracle(speech)
        assert bounded_runs(values[:938],100,speech=speech)==activity_runs(values[:938],100,speech=speech)
    with pytest.raises(TimingError):bounded_runs(values+[.5],0,speech=True)


def test_orchestration_persists_only_native_window_rows_and_each_request_is_at_most_30_seconds():
    calls=[];cached={}
    class Provider:
        def recognize_words_with_activity(self,request,control):
            a=request.offset_samples;b=a+len(request.pcm16)//2;calls.append((a,b))
            value=observation(a,b)
            result=NS(window_samples=b-a,segments=tuple(NS(**r) for r in value['rows']))
            # The real result uses dataclasses as required by orchestration.
            from mediaforge.application.contracts.local_ai import SpeechSegment,RecognitionResult
            result=RecognitionResult(tuple(SpeechSegment(**r) for r in value['rows']),b-a,'test')
            return result,value['words'],value['confidence'],value['activity']
    class Session:
        def get(self,name):return cached.get(name)
        def put(self,name,value):
            assert len(json.dumps(value).encode())<60000
            cached[name]=deepcopy(value)
    result=observe_complete(io.BytesIO(b'\0'*1792000),0,896000,0,Provider(),Session(),NS(check=lambda:None),lambda *a:None,896000)
    assert calls==[(0,480000),(416000,896000)] and len(result['words'])==56
    assert set(cached)=={'envelope-native-0-0.json','envelope-native-0-1.json'}
    again=observe_complete(io.BytesIO(),0,896000,0,Provider(),Session(),NS(check=lambda:None),lambda *a:None,896000)
    assert again==result and len(calls)==2
