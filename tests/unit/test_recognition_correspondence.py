"""A resampled boundary is not a native digital-zero predicate."""
from fractions import Fraction as F
from types import SimpleNamespace as NS

import pytest

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.features.dub.contracts import DubControl
from mediaforge.infrastructure.ffmpeg.speech import SpeechAudio
from mediaforge.infrastructure.ffmpeg import speech


@pytest.fixture
def correspondence(tmp_path,monkeypatch):
    # Pure comparator controls; real leases/decoder are exercised in integration.
    class Locks:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def parents(self,*args):pass
        def add(self,*args):pass
    monkeypatch.setattr(speech,'ReadLocks',Locks)
    path=tmp_path/'recognition.pcm'
    audio=SpeechAudio(None,None);plan=NS(origin=F(0),duration=F(1,1000),audio_index=1)
    reference=bytes(range(32));path.write_bytes(reference)
    calls=[]
    def pipe(args,control,**kw):
        calls.append((args,kw))
        for start in range(0,len(reference),7):kw['consume'](reference[start:start+7])
    audio.pipe=pipe
    return audio,plan,path,reference,calls


def test_complete_correspondence_uses_identical_extraction_command_and_locks(correspondence):
    audio,plan,path,reference,calls=correspondence
    audio.verify_extraction(path,plan,path,DubControl())
    assert len(calls)==1
    assert calls[0][0]==audio.extraction(path,plan)[1]
    assert calls[0][1]['maximum']==len(reference)
    assert calls[0][1]['locks']==(path,path)
    assert path.read_bytes()==reference


@pytest.mark.parametrize('offset',[0,1,15,30,31])
def test_changed_byte_anywhere_refuses_without_mutating_pcm(correspondence,offset):
    audio,plan,path,reference,calls=correspondence
    changed=bytearray(reference);changed[offset]^=1;path.write_bytes(changed)
    with pytest.raises(LocalAiError,match='bytes differ'):
        audio.verify_extraction(path,plan,path,DubControl())
    assert path.read_bytes()==changed


@pytest.mark.parametrize('delta',[-2,2])
def test_wrong_spool_length_refuses_before_decoder(correspondence,delta):
    audio,plan,path,reference,calls=correspondence
    path.write_bytes(reference[:delta] if delta<0 else reference+bytes(delta))
    with pytest.raises(LocalAiError,match='length or ownership'):
        audio.verify_extraction(path,plan,path,DubControl())
    assert not calls


def test_short_successful_decoder_is_not_complete_correspondence(correspondence):
    audio,plan,path,reference,calls=correspondence
    audio.pipe=lambda args,control,**kw:kw['consume'](reference[:-2])
    with pytest.raises(LocalAiError,match='incomplete'):
        audio.verify_extraction(path,plan,path,DubControl())


def test_cancelled_comparison_never_succeeds(correspondence):
    audio,plan,path,reference,calls=correspondence
    class Cancel:
        def check(self):raise LocalAiError('cancelled','Controlled cancellation')
    with pytest.raises(LocalAiError,match='Controlled cancellation'):
        audio.verify_extraction(path,plan,path,Cancel())


def test_fractional_origin_keeps_original_command_and_rounding():
    audio=SpeechAudio(None,None)
    samples,args=audio.extraction('source',NS(origin=F(1,48000),duration=F(1001,30000),audio_index=3))
    assert samples==534
    assert args==['-v','error','-copyts','-i','source','-map','0:3','-vn','-sn','-dn',
        '-af','aresample=16000:async=1:first_pts=0,atrim=start=2.0833333333333333e-05:end=0.033387500000000001,asetpts=PTS-STARTPTS,apad,atrim=end_sample=534',
        '-ac','1','-ar','16000','-f','s16le','pipe:1']
