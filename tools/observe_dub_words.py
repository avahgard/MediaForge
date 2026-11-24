"""Bounded actual word-evidence check on the declared public synthetic corpus."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import wave

from probe_dub_qwen import ordinary_token
from mediaforge.application.contracts.local_ai import RecognitionRequest
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.bootstrap.dub_models import compose_dub_models
from mediaforge.features.dub.contracts import DubControl
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.filesystem.dub_source import ProtectedDubSource
from mediaforge.infrastructure.models.final_speech_providers import FinalSpeechProviders


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--receipt',required=True,type=Path)
    parser.add_argument('--combined',action='store_true',help='Retain both fixed native VAD grids with actual words.')
    args=parser.parse_args()
    paths=ProfilePaths.for_test(args.root);paths.activate_temporary()
    if not args.receipt.is_relative_to(paths.root/'fixtures') or args.receipt.exists():parser.error('New private corpus-evidence file required.')
    gate=MediaProcessingGate();components=compose_dub_models(paths,gate);control=DubControl()
    providers=FinalSpeechProviders.from_local(components.local_ai)
    result=dict(ok=False,token=ordinary_token())
    try:
        with ProtectedDubSource(args.source,control) as source:
            with wave.open(str(source.path),'rb') as wav:
                if (wav.getframerate(),wav.getsampwidth(),wav.getnchannels())!=(16000,2,1) or wav.getnframes()>480000:
                    raise ValueError('One bounded mono16k public corpus window required.')
                raw=wav.readframes(wav.getnframes())
            with providers.session(control),providers.selected_models(('whisper-base',),control):
                if args.combined:
                    recognized,words,confidence,activity=providers.recognize_words_with_activity(RecognitionRequest(raw),control)
                    result['activity']=activity
                else:recognized,words,confidence=providers.recognize_words(RecognitionRequest(raw),control)
                result.update(ok=True,source_sha256=source.identity.sha256,segments=[asdict(s) for s in recognized.segments],
                              words=words,confidence=confidence,resources=providers.statistics)
        code=0
    except Exception as error:
        result.update(ok=False,code=str(getattr(error,'code','word_failed')),detail=str(error));code=2
    result.update(gate_busy=gate.busy,gate_failure=gate.failure)
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(ok=result['ok'],code=result.get('code'),detail=result.get('detail'),words=len(result.get('words',())),gate_busy=gate.busy)))
    return code


if __name__=='__main__':raise SystemExit(main())
