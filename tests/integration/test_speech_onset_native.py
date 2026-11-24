"""Independent physical leading-silence oracle over retained real C3 output.

Known qualification gap: frozen ASR starts can precede all nonzero source PCM.
This never retimes speech or converts that gap into an accepted listening PASS.
"""
import array,json,os,wave
from pathlib import Path
import pytest

pytestmark=[pytest.mark.native,pytest.mark.integration]

def first_signal(path,threshold):
    with wave.open(str(path),'rb') as reader:
        assert reader.getnchannels()==1 and reader.getsampwidth()==2
        rate=reader.getframerate();position=0
        while raw:=reader.readframes(16384):
            for value in array.array('h',raw):
                if abs(value)>=threshold:return rate,position
                position+=1
    raise AssertionError('Silent witness cannot qualify source onset')

def test_real_composition_does_not_advance_into_source_digital_silence():
    source=os.environ.get('MEDIAFORGE_TEST_C3_ONSET_SOURCE')
    output=os.environ.get('MEDIAFORGE_TEST_C3_ONSET_OUTPUT')
    if not source or not output:pytest.skip('Explicit retained real source and C3 listening output required.')
    rate,start=first_signal(Path(source),1);out_rate,voiced=first_signal(Path(output),32)
    assert rate==16000 and out_rate==48000
    # Existing fixed-level filter's measured boundary response allowance. The
    # source prefix is exactly zero, not a loudness threshold that drops speech.
    assert voiced>=max(0,start*3-64),dict(source_first_nonzero_16k=start,output_first_threshold32_48k=voiced,
        source_seconds=start/rate,voice_seconds=voiced/out_rate)
