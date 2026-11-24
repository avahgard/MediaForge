"""Explicit complete native final Dub command, with publication and retirement."""
import argparse
from dataclasses import asdict
import json
import hashlib
from pathlib import Path
import time
import wave

from probe_dub_qwen import ordinary_token
from mediaforge.application.services.dub import DubService
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.bootstrap.dub_models import compose_dub_models
from mediaforge.bootstrap.speech import compose_speech
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.ffmpeg.dub_probe import FfprobeDubInspector
from mediaforge.infrastructure.ffmpeg.dub_export import DubExportExecutor
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
from mediaforge.infrastructure.models.qwen_tts import QwenTts


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',required=True,type=Path);p.add_argument('--source',required=True,type=Path)
    p.add_argument('--folder',required=True,type=Path);p.add_argument('--receipt',required=True,type=Path)
    p.add_argument('--voice',default='aiden',choices=QwenTts.voices)
    p.add_argument('--mode',choices=('same_as_source','compatible_mp4','video_audio_only','audio'))
    p.add_argument('--format',default='mp3',choices=('mp3','wav'))
    p.add_argument('--listening-wav',type=Path,help='Optional <=90 s internal listening evidence; never a Dub publication.')
    p.add_argument('--cancel-at',choices=('recognize','load_voice','synthesize','export'))
    p.add_argument('--retry-after-cancel',action='store_true')
    args=p.parse_args();paths=ProfilePaths.for_test(args.root);paths.activate_temporary()
    if args.retry_after_cancel and not args.cancel_at:p.error('A declared cancellation stage is required.')
    if args.receipt.exists() or not args.receipt.is_relative_to(paths.logs):p.error('New portable receipt required.')
    if args.listening_wav and (args.listening_wav.exists() or not args.listening_wav.is_relative_to(paths.root/'fixtures')):
        p.error('New portable listening fixture required.')
    token=ordinary_token();gate=MediaProcessingGate();components=compose_dub_models(paths,gate)
    runtime=resolve_ffmpeg_runtime(profile=paths);tts=QwenTts(paths,components.requirements.qwen)
    speech=compose_speech(components.local_ai,runtime,tts=tts);listening={}
    class ObservedExport(DubExportExecutor):
        def execute(self,source,plan,composition,destination,control,progress=lambda *args:None):
            if args.listening_wav:
                from mediaforge.infrastructure.filesystem.image_output import ReadLocks
                if composition.samples>90*48000:raise ValueError('Listening evidence is bounded to 90 s; long output has its own final artifact.')
                with ReadLocks() as locks:
                    locks.parents(composition.path.parent);locks.add(composition.path)
                    sha=hashlib.sha256();count=0
                    with args.listening_wav.open('xb') as stream,wave.open(stream,'wb') as wav,composition.path.open('rb') as pcm:
                        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(48000)
                        while raw:=pcm.read(32768):
                            control.check();sha.update(raw);count+=len(raw);wav.writeframesraw(raw)
                    if count!=composition.samples*2 or sha.hexdigest()!=composition.sha256:
                        raise ValueError('Internal listening copy identity differs.')
                    listening.update(path=str(args.listening_wav),internal_only=True,sha256=composition.sha256,
                        samples=composition.samples,protected_silence=composition.protected_silence)
            return super().execute(source,plan,composition,destination,control,progress)
    export=ObservedExport(paths,runtime)
    service=DubService(FfprobeDubInspector(runtime,paths,timeout=3600,qualified_mux_metadata=True),speech,export,gate,tts.voices)
    stages=[];started=time.monotonic();result=dict(ok=False,token=token)
    cancel_sent=False
    def wait(*,allow_cancel=False):
        nonlocal cancel_sent
        previous=None
        while service.busy:
            state=service.snapshot()
            if allow_cancel and args.cancel_at and state.stage==args.cancel_at and not cancel_sent:
                cancel_sent=True;service.cancel()
            if state.stage!=previous:
                previous=state.stage
                if len(stages)<256:stages.append(dict(stage=state.stage,seconds=time.monotonic()-started))
                print(json.dumps(stages[-1]),flush=True)
            if time.monotonic()-started>24*3600:service.cancel()
            time.sleep(.1)
    try:
        if not service.inspect(args.source):raise RuntimeError('Source inspection admission refused.')
        wait()
        if service.snapshot().state!='ready':raise RuntimeError(service.snapshot().message)
        if not service.start(args.voice,args.folder,mode=args.mode,format_=args.format):
            raise RuntimeError('Dub command admission refused.')
        wait(allow_cancel=True);state=service.snapshot()
        if args.cancel_at:
            result['cancel_observation']=dict(sent=cancel_sent,operation=asdict(state),
                gate_busy=gate.busy,gate_failure=gate.failure,can_retry=service.can_retry())
            if (not cancel_sent or state.state!='cancelled' or state.destination or gate.busy or gate.failure
                    or any(p.name.startswith('.mediaforge-dub-') for p in args.folder.iterdir())):
                raise RuntimeError('Native cancellation was not cleanly retired without a result.')
            if args.retry_after_cancel:
                if not service.start(args.voice,args.folder,mode=args.mode,format_=args.format,retry=True):
                    raise RuntimeError('Explicit same-process Retry admission refused.')
                wait();state=service.snapshot()
        result.update(ok=state.state=='completed',operation=asdict(state),receipt=service.last_receipt)
        code=0 if result['ok'] else 2
    except Exception as error:
        result.update(code=str(getattr(error,'code','command_failed')),detail=str(error));code=2
    finally:
        try:service.shutdown();result['owned_sessions_retired']=True
        except Exception as error:result.update(ok=False,retirement_error=str(error));code=2
    result.update(seconds=time.monotonic()-started,stages=stages,gate_busy=gate.busy,gate_failure=gate.failure,
        speech=speech.last_receipt,cpu=speech.providers.receipts,qwen=getattr(tts.runtime,'last_statistics',{}),export=export.last_receipt,
        diagnostic=export.last_diagnostic,listening=listening,
        controller_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:result.get(k) for k in ('ok','code','detail','operation','seconds','gate_busy','gate_failure')},ensure_ascii=False),flush=True)
    return code


if __name__=='__main__':raise SystemExit(main())
