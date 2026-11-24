from types import SimpleNamespace as NS
import time
import pytest
from mediaforge.application.contracts.local_ai import LocalAiError,PcmFragment
from mediaforge.infrastructure.models.edge_network import validate_mp3,EdgeNetworkQualification
from mediaforge.infrastructure.models.edge_speech import EdgeSpeech
from mediaforge.features.dub.contracts import DubControl

def frame():return bytes.fromhex('fff364c0')+bytes(140)
def test_exact_mp3_format_partial_and_extra_stream_refusal():
    assert validate_mp3(frame()*2)==2
    for raw in (b'',b'ID3'+frame(),frame()[:-1],frame()+b'junk',bytes.fromhex('fffb90c0')+bytes(140),frame()*5003):
        with pytest.raises(LocalAiError):validate_mp3(raw)

def test_production_origin_not_implicitly_authorized():
    for endpoint in (None,'wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1',
        'ws://127.0.0.1:10/edge/v1','wss://127.0.0.1:10/edge/v1?secret=one','wss://user@127.0.0.1:10/edge/v1'):
        with pytest.raises(LocalAiError) as e:EdgeNetworkQualification(None,None,None,endpoint=endpoint)
        assert e.value.code=='dependency_unavailable'

def test_absolute_deadline_includes_audio_decode(monkeypatch):
    clock=[10.0];monkeypatch.setattr('mediaforge.infrastructure.models.edge_speech.time.monotonic',lambda:clock[0])
    def decode(*args):clock[0]=101;return PcmFragment(b'\x01\x01'*10,22050,'pin','dmitry')
    edge=EdgeSpeech(NS(synthesize=lambda *a:b'x'),decode,fingerprint='pin')
    request=NS(voice='dmitry',job_id='new',online_consent_job='new',online_text_transmission=True)
    with pytest.raises(LocalAiError) as e:edge.synthesize('Проверка.',request,DubControl())
    assert e.value.code=='provider_timeout'

def test_deadline_spawn_wrapper_preserves_nonreentrant_control():
    import threading
    from mediaforge.infrastructure.models.edge_speech import EdgeDeadline
    ready=threading.Event()
    def execute():
        with EdgeDeadline(DubControl(),time.monotonic()+5).starting():ready.set()
    thread=threading.Thread(target=execute,daemon=True);thread.start();thread.join(1)
    assert ready.is_set() and not thread.is_alive()

@pytest.mark.parametrize('job,text,code',[(None,'Проверка.','online_consent_required'),('','Проверка.','online_consent_required'),
    ('new','bad\x01text','invalid_request'),('new','bad\ud800text','invalid_request')])
def test_invalid_generation_or_xml_input_never_reaches_transport(job,text,code):
    calls=[];edge=EdgeSpeech(NS(synthesize=lambda *a:calls.append(a)),lambda *a:None,fingerprint='pin')
    request=NS(voice='dmitry',job_id=job,online_consent_job=job,online_text_transmission=True)
    with pytest.raises(LocalAiError) as e:edge.synthesize(text,request,DubControl())
    assert e.value.code==code and not calls
