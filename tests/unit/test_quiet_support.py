import array
import base64
import hashlib
import json
from fractions import Fraction as F
from types import SimpleNamespace as NS

import numpy as np
import pytest

from mediaforge.features.dub.alignment import supports
from mediaforge.features.dub.quiet_support import estimated_support
from mediaforge.features.dub.speech import TimingError
from mediaforge.infrastructure.ffmpeg.qwen_support import NativeQuietScan,POLICY
from mediaforge.features.dub.practical_timing import admit_hard_island
from tests.unit.test_practical_timing import fixture


def ledger(values,*,rate=16000):
    raw=array.array('H',values).tobytes()
    return dict(rate=rate,channels=2,samples=len(values)*rate//125,zeros=[],native_quiet=dict(
        policy=POLICY,rate=125,count=len(values),data=base64.b64encode(raw).decode(),
        sha256=hashlib.sha256(raw).hexdigest()))


@pytest.mark.parametrize('rate',[8000,16000,44100,48000,192000])
def test_native_quiet_is_chunk_independent_all_channel_and_exact_clock(rate):
    count=rate+137;position=np.arange(count)
    channel=np.sin(position*.2)*.1
    # Opposite channels cancel in the mono ASR view. Native evidence must NOT
    # call the source silent, or erase one channel merely because of downmix.
    raw=np.stack([channel,-channel],axis=1).astype('<f8').tobytes()
    scans=[]
    for block in (493,len(raw)):
        scan=NativeQuietScan(rate,2,count,NS(check=lambda:None))
        for i in range(0,len(raw),block):scan.consume(raw[i:i+block])
        scans.append(scan.finish())
    assert scans[0]==scans[1]
    data=scans[0]['native_quiet']
    assert data['count']==(count*125+rate-1)//rate and data['count']<=487500
    assert min(array.array('H',base64.b64decode(data['data'])))>1000
    assert scans[0]['zeros']==[(0,1)] # the sine starts at one exact zero sample


def test_nonzero_quiet_is_only_a_provisional_split_with_original_clock_and_zero_facts_retained():
    values=([4]*125+[1600]*1250+[4]*125)*3
    row=ledger(values);row['zeros']=[(0,128)]
    old=supports(row['zeros'],row['samples'],16000)
    result,brackets,estimated=estimated_support(row,old)
    assert len(result)==3 and estimated and brackets[0][0]==0
    assert row['zeros']==[(0,128)] # no invented exact-zero facts
    assert result[0].start==F(123*128) and result[0].end==F(1377*128)
    assert all(s.end-s.start<26*16000 for s in result)
    assert result[-1].end<row['samples']
    assert estimated_support(json.loads(json.dumps(row)),old)==(result,brackets,estimated)


def test_continuous_unseparated_speech_or_unknown_features_refuse_instead_of_clipped_seams():
    row=ledger([1200]*(31*125));old=supports([],row['samples'],16000)
    with pytest.raises(TimingError,match='No distinct source speech'):estimated_support(row,old)
    row['native_quiet']['sha256']='0'*64
    with pytest.raises(TimingError,match='identity'):estimated_support(row,old)


def test_qualified_short_hard_silence_branch_remains_identical_without_estimates():
    row=dict(rate=16000,samples=160000,zeros=[(0,16000),(128000,160000)])
    original=supports(row['zeros'],row['samples'],16000)
    assert estimated_support(row,original)==(original,(),())


def test_estimated_outer_boundaries_never_skip_word_confidence_via_the_strict_branch():
    values=list(fixture())
    for word in values[4]:word['probability']=.75
    with pytest.raises(TimingError,match='confidence'):
        admit_hard_island(*values,estimated_outer=True)
