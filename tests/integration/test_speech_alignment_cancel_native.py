"""A8 native cancellation at source analysis, activity and tempo fitting."""
from dataclasses import replace
from fractions import Fraction
from types import SimpleNamespace as NS
import array,math,os
from pathlib import Path
import pytest
from test_speech_alignment_native import native,derived,request
from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.features.dub.contracts import DubControl
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest,FfmpegRuntimeResolver
from mediaforge.infrastructure.ffmpeg.speech import SpeechAudio,PcmWriter
from mediaforge.infrastructure.ffmpeg.speech_support import SupportScan

pytestmark=[pytest.mark.native,pytest.mark.integration]

@pytest.mark.parametrize('stage',['support','activity'])
def test_cancel_real_source_analysis_and_activity(native,monkeypatch,stage):
    service,_,_,_,cases=native;source,_,_=derived(native,1);c=DubControl();reached=[]
    if stage=='support':
        consume=SupportScan.consume
        def observed(scanner,raw):
            consume(scanner,raw);reached.append(len(raw));c.cancel()
        monkeypatch.setattr(SupportScan,'consume',observed)
    else:
        activity=service.providers.activity
        def observed(pcm,offset,control,**options):
            def phase(value):
                if value=='inference':reached.append(value);c.cancel()
            return activity(pcm,offset,control,on_phase=phase)
        monkeypatch.setattr(service.providers,'activity',observed)
    req=replace(request(service,source,'cancel-'+stage),retain_private_recovery=False)
    with pytest.raises(Exception) as refused:service.prepare(req,c)
    assert reached and getattr(refused.value,'code',None)=='cancelled'
    assert not c.cleanup_failed and not service.providers.gate.busy and not service.providers.gate.failure
    assert not (service.sessions.paths.sessions/service.last_receipt['session_id']).exists()
    cases.append(dict(case='cancel-'+stage,cleanup_failed=c.cleanup_failed,gate_clean=True,receipt=dict(service.last_receipt)))

def test_cancel_actual_tempo_pipe_discards_partial_pcm_and_releases_gate(tmp_path,monkeypatch):
    ff=os.environ.get('MEDIAFORGE_TEST_RUNTIME_ROOT')
    if not ff:pytest.skip('Explicit pinned FFmpeg required.')
    paths=ProfilePaths.for_test(tmp_path/'Tempo cancellation/Data/v2');paths.prepare()
    audio=SpeechAudio(paths,FfmpegRuntimeResolver(FfmpegManifest.load()).verify_directory(Path(ff)))
    source=paths.temporary/'source.pcm';source.write_bytes(array.array('h',[int(1200*math.sin(2*math.pi*440*n/48000)) for n in range(48000)]).tobytes())
    target=paths.temporary/'cancel-fit.pcm';c=DubControl();gate=MediaProcessingGate();token=gate.acquire();written=[];write=PcmWriter.write
    def observed(owner,raw):
        write(owner,raw)
        if owner.path==target:written.append(len(raw));c.cancel()
    monkeypatch.setattr(PcmWriter,'write',observed)
    try:
        with pytest.raises(Exception) as refused:audio.convert(source,target,c,rate=48000,tempo=Fraction(6,5))
        assert getattr(refused.value,'code',None)=='cancelled' and written and not c.cleanup_failed
    finally:
        if c.cleanup_failed:gate.poison(token,'Tempo owner remains unsettled.')
        else:gate.release(token)
    assert not target.exists() and not list(paths.temporary.glob('.pcm-*')) and not gate.busy
    assert audio.receipts[-1]['retired']
