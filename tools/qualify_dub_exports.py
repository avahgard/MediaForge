"""Native short export variants using authenticated REAL Russian composition.

Video fixtures are explicitly prepared here, not converted by Dub. No TTS
reruns; the real pipeline receipt authenticates every listening PCM byte.
"""
import argparse
from dataclasses import asdict
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import time
import wave


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True)
    p.add_argument('--run',choices=('01','02','03'),default='01')
    p.add_argument('--cases',nargs='+',choices=('prores','matroska','vfr','audio'),default=('prores','matroska','audio'))
    args=p.parse_args()
    from mediaforge.application.contracts.speech import SpeechComposition
    from mediaforge.features.dub.contracts import DubControl,OutputMode,AudioFormat
    from mediaforge.features.dub.planning import output_plan
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.infrastructure.filesystem.image_output import ReadLocks
    from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
    from mediaforge.infrastructure.ffmpeg.preview_job import PreviewJobs
    from mediaforge.infrastructure.ffmpeg.dub_probe import FfprobeDubInspector
    from mediaforge.infrastructure.ffmpeg.dub_export import DubExportExecutor
    paths=ProfilePaths.for_test(args.root);paths.activate_temporary();runtime=resolve_ffmpeg_runtime(profile=paths)
    if args.receipt.exists() or not args.receipt.is_relative_to(paths.logs):p.error('New portable receipt required.')
    directory=paths.root/('fixtures/export-variants-'+args.run)
    if directory.exists():p.error('New fixture directory required.')
    directory.mkdir();control=DubControl();jobs=PreviewJobs(512*1024**2)
    previous=json.loads((paths.logs/'final-video-private-core-06-sync.json').read_bytes())
    if not previous.get('ok') or not previous.get('owned_sessions_retired'):raise ValueError('Real retired speech run required.')
    listening=Path(previous['listening']['path']);pcm=directory/'actual-russian.pcm'
    result=dict(ok=False,controller_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),variants=[],fixture_commands=[])
    started=time.monotonic();process=None
    try:
        with ReadLocks() as held:
            for path in (listening,runtime.ffmpeg,runtime.ffprobe):held.parents(path.parent);held.add(path)
            with wave.open(str(listening),'rb') as wav,pcm.open('xb') as out:
                if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate(),wav.getnframes())!=(1,2,48000,642000):
                    raise ValueError('Real composition format differs.')
                sha=hashlib.sha256()
                while raw:=wav.readframes(16384):sha.update(raw);out.write(raw)
            if sha.hexdigest()!=previous['listening']['sha256']:raise ValueError('Real composition authentication failed.')
            composition=SpeechComposition(pcm,642000,sha.hexdigest(),-3.,-18.,(),'native-export-variants',
                protected_silence=tuple(tuple(r) for r in previous['listening']['protected_silence']))
            english=paths.root/'fixtures/lecture-short.mp4';held.parents(english.parent);held.add(english)
            recipes={
                'prores':('.mov',24,['-c:v','prores_ks','-profile:v','2','-pix_fmt','yuv422p10le'],['-c:a','pcm_s16le']),
                # Matroska with omitted initial DTS cannot prove packet-copy
                # identity. This declared no-B-frame fixture exercises the
                # eligible exact-DTS class; the failed B-frame source remains.
                'matroska':('.mkv',8,['-c:v','libx264','-profile:v','high','-pix_fmt','yuv420p','-bf','0'],['-c:a','flac','-frame_size','4608']),
                'vfr':('.mp4',24,['-vf','settb=1/48000,setpts=N*2000+if(mod(N\\,2)\\,500\\,0)',
                    '-c:v','libx264','-profile:v','high','-pix_fmt','yuv420p','-bf','0',
                    '-enc_time_base:v','1/48000','-fps_mode:v','passthrough','-video_track_timescale','48000'],
                    ['-c:a','aac','-b:a','128k']),
            }
            for name,(suffix,rate,video,audio) in recipes.items():
                if name not in args.cases:continue
                source=directory/(name+suffix)
                command=[str(runtime.ffmpeg),'-v','error','-nostdin','-n','-threads','2','-filter_threads','1',
                    '-f','lavfi','-i',f'testsrc2=size=320x180:rate={rate}:duration=13.375','-i',str(english),
                    '-map','0:v:0','-map','1:a:0','-map_metadata','-1','-map_chapters','-1',
                    '-af','atrim=end_sample=642000,asetpts=PTS-STARTPTS',*video,*audio,'-threads:v','2',str(source)]
                process=jobs(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                    cwd=paths.temporary,env=paths.child_environment({}),shell=False)
                _,errors=process.communicate(timeout=120);process.stderr.close()
                if process.returncode:raise RuntimeError(errors.decode(errors='replace')[-2048:])
                result['fixture_commands'].append(command)
                inspector=FfprobeDubInspector(runtime,paths,qualified_mux_metadata=True)
                descriptor=inspector.inspect(source,control);plan=output_plan(descriptor,OutputMode.SAME_AS_SOURCE)
                if plan.sample_count!=composition.samples:raise ValueError('Fixture span differs from real composition.')
                executor=DubExportExecutor(paths,runtime)
                output=executor.execute(descriptor,plan,composition,directory/(name+'_RU_aiden'+suffix),control)
                result['variants'].append(dict(name=name,path=str(output.output),receipt=executor.last_receipt))
            # MP3/WAV use the SAME actual composition and protected pause ledger.
            descriptor=FfprobeDubInspector(runtime,paths).inspect(english,control)
            for fmt in (AudioFormat.MP3,AudioFormat.WAV):
                if 'audio' not in args.cases:continue
                plan=output_plan(descriptor,OutputMode.VIDEO_AUDIO_ONLY,audio_format=fmt)
                executor=DubExportExecutor(paths,runtime)
                output=executor.execute(descriptor,plan,composition,directory/('audio_RU_aiden.'+fmt.value),control)
                result['variants'].append(dict(name=fmt.value,path=str(output.output),receipt=executor.last_receipt))
        result['ok']=True
    except Exception as error:result.update(code=str(getattr(error,'code','qualification_failed')),detail=str(error))
    finally:
        if process is not None:
            if process.poll() is None:process.terminate();process.wait(timeout=3)
            if process.stderr and not process.stderr.closed:process.stderr.close()
        jobs.close();result.update(seconds=time.monotonic()-started,fixture_job_peak=jobs.peak,retired=True)
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k not in ('variants','fixture_commands')},ensure_ascii=False))
    if not result['ok']:raise SystemExit(2)


if __name__=='__main__':main()
