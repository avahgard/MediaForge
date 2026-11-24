import numpy as np
import pytest

from mediaforge.features.dub.audio_validation import AudioSyncValidation
from mediaforge.features.dub.speech import TimingError


def signal():
    rng=np.random.default_rng(120012)
    values=np.rint(rng.normal(0,1000,48000)).astype('<i2')
    values[16000:24000]=0
    return values


def validate(original,actual):
    check=AudioSyncValidation(((16000,24000),),48000,lossless=False)
    for start in range(0,len(original),16384):
        check.consume(original[start:start+16384].tobytes(),actual[start:start+16384].tobytes())
    return check.receipt()


def test_streaming_reference_alignment_and_bounded_codec_edge_ringing():
    original=signal();actual=original.copy()
    actual[16000:16500]=20 # fixed codec-edge guard, not a relocated silence interval
    actual[19000:19100]=4
    result=validate(original,actual)
    assert result['minimum_correlation']>.999 and result['active_blocks']==3
    assert result['protected_center_peak']==4 and result['protected_center_samples']==8000-2304


def test_time_shift_cannot_be_repaired_or_hidden_by_complete_duration():
    original=signal();actual=np.roll(original,4800)
    with pytest.raises(TimingError,match='source-clock composition'):validate(original,actual)


@pytest.mark.parametrize('level',[17,65])
def test_protected_center_noise_refuses_before_publication(level):
    original=signal();actual=original.copy();actual[17152:22848]=level
    with pytest.raises(TimingError,match='protected pause center'):validate(original,actual)


def test_invalid_or_overlapping_silence_clocks_are_not_accepted():
    for intervals in [((2,1),),((1,10),(9,11)),((0,50000),)]:
        with pytest.raises(TimingError):AudioSyncValidation(intervals,48000,lossless=True)
