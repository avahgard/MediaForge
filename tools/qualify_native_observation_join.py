"""Real two-worker join over a complete retained SAPI speech prefix.

ASR/VAD join evidence only: not a real-human speech, timing, Dub or hour PASS.
Select the first >=96 ms exact-zero gap starting after 34 s, ending before
56 s, and finish at its midpoint. This prevents a cropped final fixture word;
the actual two-worker join is still predetermined at 28 s, never searched.
"""
import argparse
import array
import io
import json
from pathlib import Path
import hashlib
import wave
import time


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True)
    args=p.parse_args()
    from mediaforge.application.services.speech_islands import observe_complete
    from mediaforge.application.services.processing_gate import MediaProcessingGate
    from mediaforge.bootstrap.dub_models import compose_dub_models
    from mediaforge.infrastructure.models.final_speech_providers import FinalSpeechProviders
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.infrastructure.filesystem.dub_source import ProtectedDubSource
    from mediaforge.features.dub.contracts import DubControl
    paths=ProfilePaths.for_test(args.root);paths.activate_temporary()
    if args.receipt.exists() or not args.receipt.is_relative_to(paths.logs):p.error('New managed receipt required.')
    gate=MediaProcessingGate();components=compose_dub_models(paths,gate)
    providers=FinalSpeechProviders.from_local(components.local_ai);control=DubControl();cached={}
    class Session:
        def get(self,name):return cached.get(name)
        def put(self,name,value):
            if len(json.dumps(value,separators=(',',':')).encode())>60000:raise ValueError('Native checkpoint row exceeds its real limit.')
            cached[name]=value
    result=dict(ok=False,scope='Native observation join only; synthetic corpus; no source-support/timing/hour qualification',
        controller_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    started=time.monotonic()
    try:
        with ProtectedDubSource(paths.root/'fixtures/lecture-minute.wav',control) as source:
            result['source_sha256']=source.identity.sha256
            with wave.open(str(source.path),'rb') as wav:
                if (wav.getframerate(),wav.getsampwidth(),wav.getnchannels())!=(16000,2,1) or wav.getnframes()<896000:
                    raise ValueError('Fixed SAPI source format/span differs.')
                raw=wav.readframes(896000)
            values=array.array('h',raw);zero=None;end=None
            for index,value in enumerate(values):
                if value==0 and zero is None:zero=index
                if value!=0 and zero is not None:
                    if zero>=34*16000 and index-zero>=1536:
                        end=(zero+index)//2;break
                    zero=None
            if end is None:raise ValueError('No complete fixed-prefix final silence before 56 s.')
            raw=raw[:end*2];result['window']=[0,end]
            with providers.session(control),providers.selected_models(('whisper-base',),control):
                observed=observe_complete(io.BytesIO(raw),0,end,0,providers,Session(),control,lambda *a:None,end)
            result.update(ok=True,observation=observed)
    except Exception as error:result.update(code=str(getattr(error,'code','qualification_failed')),detail=str(error))
    result.update(seconds=time.monotonic()-started,cpu=providers.receipts,native_checkpoints=cached,gate_busy=gate.busy,gate_failure=gate.failure)
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:result.get(k) for k in ('ok','code','detail','seconds','gate_busy','gate_failure')}))
    if not result['ok']:raise SystemExit(2)


if __name__=='__main__':main()
