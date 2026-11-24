"""Bounded native qualification at the first/longest retained hour-source unit.

This is not a Dub output or a successful hour run. The prior source ledger only
selects the declared test interval; immutable source bytes are reauthenticated.
No model changes, alternate phase, transcript reroll, TTS or network.
"""
import argparse
from dataclasses import asdict
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import time
import wave


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True)
    p.add_argument('--which',choices=('first','longest'),required=True)
    args=p.parse_args()
    from mediaforge.features.dub.contracts import DubControl
    from mediaforge.features.dub.alignment import supports
    from mediaforge.features.dub.quiet_support import estimated_support
    from mediaforge.features.dub.practical_timing import admit_hard_island
    from mediaforge.application.services.speech_islands import island_window,observe_complete
    from mediaforge.application.services.processing_gate import MediaProcessingGate
    from mediaforge.bootstrap.dub_models import compose_dub_models
    from mediaforge.infrastructure.models.final_speech_providers import FinalSpeechProviders
    from mediaforge.infrastructure.ffmpeg.qwen_speech import QwenSpeechAudio
    from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.infrastructure.filesystem.dub_source import ProtectedDubSource
    paths=ProfilePaths.for_test(args.root);paths.activate_temporary()
    if args.receipt.exists() or not args.receipt.is_relative_to(paths.logs):p.error('New managed receipt required.')
    prior=json.loads((paths.logs/'hour-native-support-observation-01.json').read_bytes());ledger=prior['ledger']
    rate=ledger['rate'];samples=ledger['samples']*16000//rate
    original=supports(ledger['zeros'],ledger['samples'],rate)
    source_support,combined,centers=estimated_support(ledger,original)
    index=0 if args.which=='first' else max(range(len(source_support)),key=lambda i:source_support[i].end-source_support[i].start)
    target=source_support[index];begin,end=island_window(source_support,index,samples)
    control=DubControl();gate=MediaProcessingGate();models=compose_dub_models(paths,gate)
    providers=FinalSpeechProviders.from_local(models.local_ai)
    audio=QwenSpeechAudio(paths,resolve_ffmpeg_runtime(profile=paths));source=paths.root/'fixtures/corpus-rec09-qualified.mp4'
    started=time.monotonic();result=dict(ok=False,which=args.which,index=index,source_sha256=prior['source_sha256'],
        source=[str(target.start),str(target.end)],window=[begin,end],diagnostic_only=True,
        controller_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    checkpoints={}
    class Session:
        def get(self,name):return checkpoints.get(name)
        def put(self,name,value):
            if len(json.dumps(value,separators=(',',':')).encode())>60000:raise ValueError('Native journal exceeds real checkpoint row bound.')
            checkpoints[name]=value
    try:
        with ProtectedDubSource(source,control) as protected:
            if protected.identity.sha256!=prior['source_sha256']:raise ValueError('Source changed; diagnostic selection is invalid.')
            pcm=bytearray()
            filters=f'aresample=16000:async=1:first_pts=0,atrim=start_sample={begin}:end_sample={end},asetpts=PTS-STARTPTS'
            audio.pipe(['-v','error','-copyts','-i',str(source),'-map','0:1','-vn','-sn','-dn','-af',filters,
                '-ac','1','-ar','16000','-f','s16le','pipe:1'],control,consume=pcm.extend,
                maximum=(end-begin)*2,timeout=90,locks=(source,))
            if len(pcm)!=(end-begin)*2:raise ValueError('Native context count differs.')
            class Context(io.BytesIO):
                def seek(self,offset,whence=0):
                    if whence!=0:raise ValueError('Unexpected context addressing.')
                    return super().seek(offset-begin*2)
            with providers.session(control),providers.selected_models(('whisper-base',),control):
                observed=observe_complete(Context(pcm),begin,end,index,providers,Session(),control,lambda *a:None,samples)
            result['observation']=observed
            brackets=[(Fraction(a*16000,rate),Fraction(b*16000,rate)) for a,b in combined]
            zeros=[(Fraction(a*16000,rate),Fraction(b*16000,rate)) for a,b in ledger['zeros']]
            outer=not any(b==target.start for a,b in zeros) or not any(a==target.end for a,b in zeros)
            parts,excluded=admit_hard_island(target,observed['activity'],brackets,(begin,end),observed['words'],
                ' '.join(row['text'] for row in observed['rows']),bytes(pcm),estimated_outer=outer,file_samples=samples)
            result.update(ok=True,parts=[dict(start=str(p.start),end=str(p.end),text=p.text,evidence=p.evidence) for p in parts],
                excluded=[[str(a),str(b)] for a,b in excluded])
            result['source_window_pcm_sha256']=hashlib.sha256(pcm).hexdigest()
    except Exception as error:result.update(code=str(getattr(error,'code','observation_failed')),detail=str(error))
    result.update(seconds=time.monotonic()-started,cpu=providers.receipts,gate_busy=gate.busy,gate_failure=gate.failure,
                  native_windows=len(checkpoints),native_checkpoints=checkpoints)
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:result.get(k) for k in ('ok','which','index','source','window','code','detail','seconds','native_windows','gate_busy','gate_failure')},ensure_ascii=False))
    if not result['ok']:raise SystemExit(2)


if __name__=='__main__':main()
