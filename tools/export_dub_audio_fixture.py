"""Native audio publication of an actual completed internal Dub composition.

The private listening WAV is checked against its real speech-run PCM receipt;
this tool does not synthesize mock success or claim whole video qualification.
"""
import argparse
import hashlib
import json
from pathlib import Path
import wave
from uuid import uuid4

from mediaforge.application.contracts.speech import SpeechComposition
from mediaforge.features.dub.contracts import DubControl,OutputMode,AudioFormat
from mediaforge.features.dub.planning import output_plan
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
from mediaforge.infrastructure.ffmpeg.dub_probe import FfprobeDubInspector
from mediaforge.infrastructure.ffmpeg.dub_export import DubExportExecutor
from mediaforge.infrastructure.ffmpeg.speech import PcmWriter


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--speech-receipt',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--format',choices=('mp3','wav'),required=True)
    args=parser.parse_args()
    paths=ProfilePaths.for_test(args.root);paths.activate_temporary()
    if args.receipt.exists() or not args.receipt.is_relative_to(paths.logs):parser.error('New portable receipt required.')
    previous=json.loads(args.speech_receipt.read_bytes())
    if previous.get('ok') is not True or previous.get('internal_only') is not True:
        parser.error('Successful real internal speech receipt required.')
    control=DubControl();runtime=resolve_ffmpeg_runtime(profile=paths)
    source=FfprobeDubInspector(runtime,paths).inspect(args.source,control)
    if source.identity.sha256!=previous['source_sha256']:parser.error('Native source differs from speech run.')
    plan=output_plan(source,OutputMode.AUDIO,audio_format=AudioFormat(args.format))
    listening=Path(previous['output'])
    pcm=paths.root/'fixtures'/('export-input-'+args.format+'-'+uuid4().hex+'.pcm')
    if pcm.exists():parser.error('New input fixture required; no replacement.')
    with ReadLocks() as locks:
        locks.parents(listening.parent);locks.add(listening)
        with wave.open(str(listening),'rb') as wav,PcmWriter(pcm,control,plan.sample_count) as writer:
            if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate(),wav.getnframes())!=(1,2,48000,plan.sample_count):
                raise ValueError('Native speech listening format differs.')
            while raw:=wav.readframes(16384):writer.write(raw)
            if writer.digest.hexdigest()!=previous['sha256']:raise ValueError('Native speech PCM receipt differs.')
    composition=SpeechComposition(pcm,plan.sample_count,previous['sha256'],previous['peak'],previous['integrated_lufs'],(), 'native-fixture')
    executor=DubExportExecutor(paths,runtime)
    result=dict(ok=False,source_sha256=source.identity.sha256,format=args.format)
    try:
        exported=executor.execute(source,plan,composition,args.destination,control,
                                  lambda *p:print(json.dumps(dict(stage=p[0])),flush=True))
        result.update(ok=True,path=str(exported.output),identity=vars(exported.identity.files[0]));code=0
    except Exception as error:
        result.update(code=str(getattr(error,'code','export_failed')),detail=str(error),diagnostic=executor.last_diagnostic);code=2
    result.update(export=executor.last_receipt,cleanup_failed=control.cleanup_failed)
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:result.get(k) for k in ('ok','code','detail','diagnostic','path','cleanup_failed')},ensure_ascii=False))
    return code


if __name__=='__main__':raise SystemExit(main())
