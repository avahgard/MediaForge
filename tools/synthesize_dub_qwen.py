"""Explicit final-delivery real Russian synthesis and resource receipt."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import wave

from probe_dub_qwen import ordinary_token
from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.features.media.processing_control import ProcessingControl
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.models.optional_resources import OptionalResources
from mediaforge.infrastructure.models.qwen_runtime import QwenRuntime
from mediaforge.infrastructure.models.qwen_session import synthesis_session, SPEAKERS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--speaker', default='ryan', choices=SPEAKERS)
    parser.add_argument('--text', default='Привет! Сегодня мы изучим основы работы с видео. Сначала откройте файл, затем выберите голос.')
    args = parser.parse_args()
    paths = ProfilePaths.for_test(args.root)
    for path in (args.output, args.receipt):
        if not path.is_absolute() or not path.is_relative_to(paths.root) or path.exists():
            parser.error('New explicit paths inside the portable root are required.')
    token = ordinary_token()
    gate = MediaProcessingGate(); resources = OptionalResources(paths, gate)
    runtime = QwenRuntime(paths, resources)
    control = ProcessingControl(lambda: LocalAiError('cancelled', 'Synthesis cancelled.'))
    result = dict(ok=False, token=token, speaker=args.speaker,
                  text_sha256=hashlib.sha256(args.text.encode()).hexdigest())
    engine = None
    try:
        with synthesis_session(runtime, control) as engine:
            fragment = engine.synthesize(args.text, args.speaker)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('xb') as stream, wave.open(stream, 'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(fragment.sample_rate)
                wav.writeframes(fragment.pcm16)
            result.update(ok=True, samples=fragment.samples, sample_rate=fragment.sample_rate,
                          pcm_sha256=hashlib.sha256(fragment.pcm16).hexdigest(), wav=str(args.output))
        code = 0
    except Exception as error:
        result.update(ok=False, code=getattr(error, 'code', 'synthesis_failed'), detail=str(error),
                      retained=bool(getattr(error, 'retained_resources', ())))
        code = 2
    result.update(statistics=getattr(runtime, 'last_statistics', {}),
                  driver_authentication=runtime.last_driver_proof,
                  gate_busy=gate.busy, gate_failure=gate.failure)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    with args.receipt.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: result.get(k) for k in ('ok','code','detail','samples','sample_rate','gate_busy','gate_failure')}, ensure_ascii=False))
    return code


if __name__ == '__main__':
    sys.exit(main())
