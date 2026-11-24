"""Final native EN->RU speech composition; listening artifact, NOT Dub export."""
import argparse
import hashlib
import json
from pathlib import Path
from uuid import uuid4
import wave

from probe_dub_qwen import ordinary_token
from mediaforge.application.contracts.speech import SpeechRequest
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.bootstrap.dub_models import compose_dub_models
from mediaforge.bootstrap.speech import compose_speech
from mediaforge.features.dub.contracts import DubControl, OutputMode
from mediaforge.features.dub.planning import output_plan
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.ffmpeg.dub_probe import FfprobeDubInspector
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
from mediaforge.infrastructure.models.qwen_tts import QwenTts


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--receipt',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--speaker',choices=QwenTts.voices,default='ryan')
    args=parser.parse_args()
    paths=ProfilePaths.for_test(args.root);paths.activate_temporary()
    if any(not p.is_relative_to(paths.root) or p.exists() for p in (args.receipt,args.output)):
        parser.error('New explicit portable artifact paths required.')
    gate=MediaProcessingGate();control=DubControl();token=ordinary_token()
    components=compose_dub_models(paths,gate)
    runtime=resolve_ffmpeg_runtime(profile=paths)
    tts=QwenTts(paths,components.requirements.qwen)
    speech=compose_speech(components.local_ai,runtime,tts=tts)
    result=dict(ok=False,token=token,internal_only=True,speaker=args.speaker)
    stages=[]
    def progress(stage,done,total):
        if not stages or stages[-1]['stage']!=stage:
            row=dict(stage=stage,done=done,total=total)
            if len(stages)<256:stages.append(row)
            print(json.dumps(row),flush=True)
    try:
        source=FfprobeDubInspector(runtime,paths,timeout=600).inspect(args.source,control)
        mode=OutputMode.VIDEO_AUDIO_ONLY if source.video else OutputMode.AUDIO
        plan=output_plan(source,mode)
        request=SpeechRequest(source,plan,'final-'+uuid4().hex,voice=args.speaker,retain_private_recovery=True)
        prepared=speech.prepare(request,control,progress)
        with ReadLocks() as locks:
            locks.parents(prepared.path.parent);locks.add(prepared.path)
            with prepared.path.open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=prepared.sha256 or prepared.path.stat().st_size!=2*prepared.samples:
                    raise ValueError('Internal artifact changed.')
            with args.output.open('xb') as stream,wave.open(stream,'wb') as wav,prepared.path.open('rb') as raw:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(48000)
                while block:=raw.read(65536):wav.writeframesraw(block)
        result.update(ok=True,source_sha256=source.identity.sha256,samples=prepared.samples,
                      sha256=prepared.sha256,peak=prepared.peak,integrated_lufs=prepared.integrated_lufs,
                      warnings=prepared.warnings,output=str(args.output));code=0
    except Exception as error:
        result.update(ok=False,code=str(getattr(error,'code','speech_failed')),detail=str(error));code=2
    # Normal application-style shutdown retires only THIS process's tickets.
    # Older process remnants remain counted and are never adopted/deleted here.
    if not gate.failure and not control.cleanup_failed:
        try:
            speech.sessions.close(DubControl())
            result['owned_sessions_retired']=True
        except Exception as error:
            result.update(ok=False,code='lifecycle_failed',detail=str(error));code=2
    result.update(stages=stages,speech=speech.last_receipt,
                  qwen=getattr(tts.runtime,'last_statistics',{}),cpu=speech.providers.receipts,
                  audio=speech.audio.receipts,gate_busy=gate.busy,gate_failure=gate.failure)
    with args.receipt.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:result.get(k) for k in ('ok','code','detail','samples','gate_busy','gate_failure')},ensure_ascii=False),flush=True)
    return code


if __name__=='__main__':
    raise SystemExit(main())
