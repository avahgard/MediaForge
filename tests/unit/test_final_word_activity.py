import hashlib
from types import SimpleNamespace as NS
import pytest

from mediaforge.application.contracts.local_ai import RecognitionRequest,LocalAiError
from mediaforge.infrastructure.models.final_speech_providers import FinalSpeechProviders
from mediaforge.infrastructure.models.final_dub_runtime import ENTRY,ENTRY_SHA256
from mediaforge.features.dub.alignment import VAD_SHA256


def response():
    return dict(samples=16000,word_evidence='accepted-whisper-base-word-estimates-v2',
        word_time_basis='attention-estimates-not-acoustic-boundaries',
        segments=[dict(start=1600,end=12800,text='A word.')],
        words=[dict(start=1600,end=3200,text='A',probability=.95),dict(start=4000,end=12800,text='word.',probability=.95)],
        word_segments=[dict(avg_logprob=-.1,no_speech_prob=.001)],
        activity_support=dict(asset_sha256=VAD_SHA256,observations=[
            dict(grid=0,scores=[.1]*31),dict(grid=256,scores=[.1]*30)]))


def test_final_word_activity_is_one_request_with_exact_native_offset_and_identity():
    assert hashlib.sha256(ENTRY.read_bytes()).hexdigest()==ENTRY_SHA256
    provider=object.__new__(FinalSpeechProviders);calls=[]
    def selected(key,action,payload,control):
        calls.append((key,action,payload));return response(),NS(revision='fixed')
    provider._selected=selected
    result,words,confidence,activity=provider.recognize_words_with_activity(RecognitionRequest(bytes(32000),32000),None)
    assert len(calls)==1 and calls[0][2]['activity_support'] is True
    assert result.segments[0].start_sample==33600 and words[1]['end']==44800
    assert [row['offset'] for row in activity]==[32000,32256]
    assert confidence[0]['avg_logprob']==-.1


@pytest.mark.parametrize('damage',['asset','grid','score','missing'])
def test_combined_evidence_cannot_publish_partial_or_untrusted_activity(damage):
    row=response();value=row['activity_support']
    if damage=='asset':value['asset_sha256']='0'*64
    elif damage=='grid':value['observations'][1]['grid']=128
    elif damage=='score':value['observations'][0]['scores'][0]=float('nan')
    else:row.pop('activity_support')
    provider=object.__new__(FinalSpeechProviders);provider._selected=lambda *args:(row,NS(revision='fixed'))
    with pytest.raises(LocalAiError,match='Combined recognition/activity'):
        provider.recognize_words_with_activity(RecognitionRequest(bytes(32000)),None)
