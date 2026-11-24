"""Actual accepted recognition/translation adapters on the final portable profile."""
import argparse
import hashlib
import json
from pathlib import Path
import wave

from probe_dub_qwen import ordinary_token
from mediaforge.application.contracts.local_ai import RecognitionRequest, TranslationRequest
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.bootstrap.dub_models import compose_dub_models
from mediaforge.features.dub.contracts import DubControl
from mediaforge.infrastructure.filesystem.dub_source import ProtectedDubSource
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.models.dub_providers import SpeechProviders


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    args = parser.parse_args()
    paths = ProfilePaths.for_test(args.root); paths.activate_temporary()
    if not args.receipt.is_relative_to(paths.logs) or args.receipt.exists():
        parser.error('New portable receipt required.')
    gate = MediaProcessingGate(); control = DubControl(); token = ordinary_token()
    composed = compose_dub_models(paths, gate)
    providers = SpeechProviders.from_local(composed.local_ai)
    result = dict(ok=False, token=token)
    try:
        with ProtectedDubSource(args.source,control) as source:
            with wave.open(str(source.path),'rb') as stream:
                if (stream.getnchannels(),stream.getsampwidth(),stream.getframerate()) != (1,2,16000) or not 0 < stream.getnframes() <= 30*16000:
                    raise ValueError('Bounded mono16k PCM fixture required.')
                raw = stream.readframes(stream.getnframes())
            result['source_sha256'] = source.identity.sha256
            with providers.session(control), providers.selected_models(('whisper-base','argos-en-ru'),control):
                recognized = providers.recognize(RecognitionRequest(raw),control)
                english = ' '.join(r.text for r in recognized.segments)
                russian = providers.translate(TranslationRequest(english),control).text
                if not english or not russian or not any('А' <= c <= 'я' or c in 'Ёё' for c in russian):
                    raise ValueError('Actual speech/translation result missing.')
                result.update(ok=True,source_samples=len(raw)//2,segments=len(recognized.segments),
                              english_sha256=hashlib.sha256(english.encode()).hexdigest(),
                              russian_sha256=hashlib.sha256(russian.encode()).hexdigest())
        code = 0
    except Exception as error:
        result.update(ok=False,code=getattr(error,'code','cpu_failed'),detail=str(error)); code = 2
    result.update(gate_busy=gate.busy,gate_failure=gate.failure,statistics=getattr(providers,'statistics',{}),
                  operations=providers.receipts)
    with args.receipt.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:result.get(k) for k in ('ok','code','detail','segments','gate_busy','gate_failure')},ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
