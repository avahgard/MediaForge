"""One bounded production-engine session: built-in Russian voice comparison.

Fixed public synthetic sentence, seed and model; no user transcript or tuning,
rerolls, provider fallback or separate research milestone. Retain actual WAVs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import wave

from probe_dub_qwen import ordinary_token
from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.features.media.processing_control import ProcessingControl
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.models.optional_resources import OptionalResources
from mediaforge.infrastructure.models.qwen_runtime import QwenRuntime
from mediaforge.infrastructure.models.qwen_session import synthesis_session, SPEAKERS

TEXT='Сегодня мы узнаем, как компьютер хранит информацию.'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--receipt',required=True,type=Path)
    parser.add_argument('--directory',required=True,type=Path)
    parser.add_argument('--text',default=TEXT)
    args=parser.parse_args()
    if not 1<=len(args.text)<=160 or not any('А'<=c<='я' for c in args.text):parser.error('One fixed public Russian phrase of at most 160 characters required.')
    paths=ProfilePaths.for_test(args.root);paths.activate_temporary()
    if (not args.receipt.is_relative_to(paths.logs) or args.receipt.exists()
            or not args.directory.is_relative_to(paths.root/'fixtures') or args.directory.exists()):
        parser.error('New portable receipt and fixed-corpus listening directory required.')
    args.directory.mkdir()
    token=ordinary_token();gate=MediaProcessingGate();resources=OptionalResources(paths,gate)
    runtime=QwenRuntime(paths,resources)
    control=ProcessingControl(lambda:LocalAiError('cancelled','Voice qualification cancelled.'))
    result=dict(ok=False,token=token,fixed_public_text=args.text,voices=[],standalone_delivery_milestone=False)
    try:
        with synthesis_session(runtime,control) as engine:
            for speaker in SPEAKERS:
                fragment=engine.synthesize(args.text,speaker,seconds=15)
                path=args.directory/(speaker+'.wav')
                with path.open('xb') as stream,wave.open(stream,'wb') as wav:
                    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(fragment.pcm16)
                row=dict(speaker=speaker,samples=fragment.samples,seconds=fragment.samples/24000,
                         sha256=hashlib.sha256(fragment.pcm16).hexdigest(),wav=str(path))
                result['voices'].append(row);print(json.dumps(row),flush=True)
        result['ok']=True;code=0
    except Exception as error:
        result.update(code=str(getattr(error,'code','voice_failed')),detail=str(error));code=2
    result.update(statistics=getattr(runtime,'last_statistics',{}),gate_busy=gate.busy,gate_failure=gate.failure)
    with args.receipt.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(ok=result['ok'],voices=len(result['voices']),code=result.get('code'),detail=result.get('detail'))),flush=True)
    return code


if __name__=='__main__':
    raise SystemExit(main())
