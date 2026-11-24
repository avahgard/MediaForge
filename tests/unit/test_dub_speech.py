from dataclasses import replace
from fractions import Fraction as F
from types import SimpleNamespace as NS
import array

import pytest

from mediaforge.application.contracts.local_ai import SpeechSegment as S, LocalAiError, PcmFragment
from mediaforge.features.dub.speech import *
from mediaforge.features.media.processing_control import ProcessingControl
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.infrastructure.models.dub_providers import SpeechProviders
from mediaforge.infrastructure.models.edge_speech import EdgeSpeech, EdgeTransient, VOICE
from mediaforge.infrastructure.ffmpeg.speech import trim_silence, SpeechAudio
from mediaforge.application.services.speech import JobControl
from mediaforge.features.dub.contracts import DubControl

def control():return ProcessingControl(lambda:LocalAiError('cancelled','Test cancelled.'))

def test_hour_window_clock_bounded_and_complete():
    rows=list(windows(3600*16000))
    assert rows[0].core_start==0 and rows[-1].core_end==3600*16000
    assert all(b.core_start==a.core_end for a,b in zip(rows,rows[1:]))
    assert all(w.end-w.start<=30*16000 for w in rows)
    assert half_up(F(1,96000)*48000)==1

def test_measured_zero_pause_preserves_independent_speech_cores(tmp_path):
    raw=bytes(4*32000)+b'\x01\x00'*16000+bytes(6*32000)+b'\x01\x00'*16000+bytes(6*32000)+b'\x01\x00'*16000+bytes(6*32000)
    path=tmp_path/'source.pcm';path.write_bytes(raw)
    rows=list(SpeechAudio(None,None).recognition_windows(path,len(raw)//2,control()))
    assert [w.core_end//16000 for w in rows]==[8,15,25]
    assert all(a.core_end==b.core_start for a,b in zip(rows,rows[1:]))
    assert rows[0].start==0 and rows[-1].end==len(raw)//2

def test_midpoint_ownership_and_context_seam_refusal():
    first,second=list(windows(60*16000))[:2]
    item=S(25*16000,27*16000,'Do not delete the backup.')
    assert not owned(first,(item,)) and owned(second,(item,))==(item,)
    seam((item,),(replace(item,start_sample=item.start_sample+100),),26*16000)
    with pytest.raises(TimingError,match='seam'):seam((item,),(replace(item,text='Delete the backup.'),),26*16000)

@pytest.mark.parametrize('a,b',[(-100,100),(100,-100),(-100,-100),(100,100)])
def test_seam_jitter_never_duplicates_or_loses_complete_words(a,b):
    first,second=list(windows(60*16000))[:2]
    s=S(25*16000+a,27*16000+a,'Keep every complete word.')
    t=S(25*16000+b,27*16000+b,'Keep every complete word.')
    left,right=reconcile(first,(s,),second,(t,))
    assert len(left)+len(right)==1 and (left+right)[0].text==s.text

def test_group_provenance_and_no_silent_lost_text():
    rows=list(group((S(10,16000,'One.'),S(15500,30000,'Two.'),S(32000,40000,'Three.'))))
    assert rows[0][0]==S(10,30000,'One. Two.') and rows[0][1]==(0,1)
    assert rows[1][0].text=='Three.'
    with pytest.raises(TimingError):list(group((S(0,20000,'One.'),S(1000,22000,'Two.'))))

@pytest.mark.parametrize('length,ratio,warning',[(8000,F(1),False),(9500,F(1),False),(10925,F(23,20),False),(11875,F(5,4),True)])
def test_fit_gap_and_exact_cap(length,ratio,warning):
    row=fit(0,10000,length)
    # min(1920, 500)=500 reserved samples for this slot.
    assert row.usable==9500
    assert row.ratio==ratio and row.accelerated_warning==warning

def test_timing_unfit_no_overlay_or_word_cut():
    for args in ((0,1,1),(0,10000,11876),(100,100,1)):
        if args==(0,1,1):assert fit(*args).usable==1;continue
        with pytest.raises(TimingError) as error:fit(*args)
        assert error.value.code=='timing_unfit'

def test_complete_bounded_synthesis_partition_and_only_zero_outer_trim():
    text=('Предложение с числом 12. '*60).strip()
    parts=list(synthesis_units(text));assert ''.join(parts)==text and all(len(p)<=500 for p in parts)
    raw=array.array('h',[0]*2000+[20,0,0,-25]+[0]*2000).tobytes()
    trimmed=array.array('h',trim_silence(raw))
    assert len(trimmed)==1924 and trimmed[960:964]==array.array('h',[20,0,0,-25])

def test_shared_gate_owned_for_whole_speech_job_and_poison_on_unresolved():
    gate=MediaProcessingGate();p=SpeechProviders(NS(),NS(),gate)
    with p.session(control()):
        assert gate.busy and gate.acquire() is None
        with pytest.raises(LocalAiError):
            with p.session(control()):pass
    assert not gate.busy
    with pytest.raises(LocalAiError):
        with p.session(control()):raise LocalAiError('lifecycle_failed','retained',retained_resources=(object(),))
    assert gate.failure and gate.busy

def test_original_model_smoke_and_c3_adapter_share_the_same_owner():
    old=SpeechProviders(NS(),NS(),MediaProcessingGate())
    adapted=SpeechProviders.from_local(NS(providers=old,models=object()))
    with adapted.session(control()):
        assert old._owners.token is adapted._owners.token
    assert adapted.receipts is old.receipts and not old.gate.busy

def test_final_orchestrator_lends_same_gate_without_early_release():
    gate=MediaProcessingGate();p=SpeechProviders(NS(),NS(),gate)
    token=gate.acquire()
    with p.session(control(),owner=token):
        assert p.session_owner is token
    assert gate.owned_by(token) and gate.acquire() is None
    with pytest.raises(LocalAiError):
        with p.session(control(),owner=object()):pass
    assert gate.owned_by(token)
    gate.release(token)

def test_selected_revision_lease_spans_all_stage_calls_until_job_retirement():
    from contextlib import contextmanager
    from mediaforge.application.contracts.models import ModelSpec
    active=[];closed=[];spec=ModelSpec('whisper-base','revision',(),'',False)
    @contextmanager
    def acquire(*args,**kwargs):
        assert not active;active.append('owned')
        try:yield object()
        finally:active.clear();closed.append(True)
    p=SpeechProviders(NS(),NS(),MediaProcessingGate());p.models=NS(acquire=acquire,spec=lambda key:spec)
    p._run=lambda *args,**kwargs:dict(ok=True)
    with p.session(control()):
        with p.selected_models(('whisper-base',),control()):
            assert active and not closed
            p._selected('whisper-base','recognize',{},control())
            p._selected('whisper-base','recognize',{},control())
            assert active and not closed and p.gate.busy
        assert closed==[True] and p.gate.busy
    assert not p.gate.busy

def test_c3_cancellation_uses_the_accepted_model_error_boundary():
    original=DubControl();wrapped=JobControl(original);original.cancel()
    with pytest.raises(LocalAiError) as error:wrapped.check()
    assert error.value.code=='cancelled'
    with pytest.raises(LocalAiError):
        with wrapped.starting():pass

@pytest.mark.parametrize('code',['missing','corrupt','in_use'])
def test_selected_model_errors_remain_truthful_and_gate_is_retired(code):
    from contextlib import contextmanager
    @contextmanager
    def acquire(*args,**kwargs):raise LocalAiError(code,'Synthetic model state.');yield
    p=SpeechProviders(NS(),NS(),MediaProcessingGate());p.models=NS(acquire=acquire)
    with pytest.raises(LocalAiError) as error:
        with p.session(control()):p._selected('whisper-base','recognize',{},control())
    assert error.value.code==code and not p.gate.busy

def request(**kw):return NS(voice='dmitry',job_id='new-job',online_consent_job=kw.get('consent','new-job'),online_text_transmission=kw.get('transmit',True))

def test_edge_consent_denied_before_any_network_and_no_inherited_consent():
    calls=[]
    edge=EdgeSpeech(NS(synthesize=lambda *a:calls.append(a)),lambda *a:None,fingerprint='synthetic-contract-test')
    for req in (request(consent='old-job'),request(transmit=False)):
        with pytest.raises(LocalAiError) as error:edge.synthesize('Проверка.',req,control())
        assert error.value.code=='online_consent_required'
    assert calls==[]

def test_edge_bounded_retry_no_fallback_and_cancel_before_network():
    calls=[]
    def transport(*args):calls.append(args);raise EdgeTransient(503)
    edge=EdgeSpeech(NS(synthesize=transport),lambda *a:None,fingerprint='synthetic-contract-test')
    with pytest.raises(LocalAiError) as error:edge.synthesize('Проверка.',request(),control())
    assert error.value.code=='online_unavailable' and len(calls)==2
    assert all(call[1].voice==VOICE and call[1].absolute_timeout==90 for call in calls)
    c=control();c.cancel()
    with pytest.raises(LocalAiError):edge.synthesize('Проверка.',request(),c)
    assert len(calls)==2

def test_edge_cancel_after_response_and_permanent_error_never_fallback():
    c=control();decoded=[]
    def transport(*args):c.cancel();return b'untrusted-audio'
    edge=EdgeSpeech(NS(synthesize=transport),lambda *a:decoded.append(a),fingerprint='contract-test-only')
    with pytest.raises(LocalAiError) as error:edge.synthesize('Проверка.',request(),c)
    assert error.value.code=='cancelled' and not decoded
    calls=[]
    def denied(*args):calls.append(args);raise EdgeTransient(403)
    edge=EdgeSpeech(NS(synthesize=denied),lambda *a:None,fingerprint='contract-test-only')
    with pytest.raises(LocalAiError):edge.synthesize('Проверка.',request(),control())
    assert len(calls)==1

@pytest.mark.parametrize('raw,speaker,pin',[(b'\x01','dmitry','contract'),(bytes(200),'dmitry','contract'),(b'\x00\x10'*100,'ruslan','contract'),(b'\x00\x10'*100,'dmitry','other')])
def test_edge_untrusted_decoded_pcm_refuses_invalid_or_wrong_voice(raw,speaker,pin):
    edge=EdgeSpeech(NS(synthesize=lambda *args:b'compressed-fixture'),
        lambda *args:PcmFragment(raw,22050,pin,speaker),fingerprint='contract')
    with pytest.raises(LocalAiError) as error:edge.synthesize('Проверка.',request(),control())
    assert error.value.code=='invalid_output'
