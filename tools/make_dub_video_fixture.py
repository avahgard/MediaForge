"""Create one small owned public-speech video fixture using managed FFmpeg."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime,FfmpegManifest
from mediaforge.infrastructure.ffmpeg.preview_job import PreviewJobs


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--audio',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True)
    args=p.parse_args();paths=ProfilePaths.for_test(args.root);paths.activate_temporary()
    if (args.output.exists() or args.receipt.exists() or not args.output.is_relative_to(paths.root/'fixtures')
        or not args.receipt.is_relative_to(paths.logs) or args.output.suffix not in ('.mp4','.mov','.mkv')):
        p.error('New explicit fixture/receipt required.')
    runtime=resolve_ffmpeg_runtime(profile=paths);jobs=PreviewJobs(512*1024**2)
    with ReadLocks() as locks:
        for path in (args.audio,runtime.ffmpeg):locks.parents(path.parent);locks.add(path)
        if hashlib.sha256(runtime.ffmpeg.read_bytes()).hexdigest()!=FfmpegManifest.load().ffmpeg_sha256:
            raise ValueError('Managed fixture generator changed.')
        codec=['-c:v','prores_ks','-profile:v','2','-pix_fmt','yuv422p10le'] if args.output.suffix=='.mov' else [
            '-c:v','libx264','-profile:v','high','-pix_fmt','yuv420p','-crf','24']
        audio=['-c:a','pcm_s16le'] if args.output.suffix=='.mov' else ['-c:a','aac','-b:a','128k']
        command=[str(runtime.ffmpeg),'-v','error','-nostdin','-n','-threads','2','-filter_threads','1',
            '-f','lavfi','-i','testsrc2=size=320x180:rate=24','-i',str(args.audio),
            '-map','0:v','-map','1:a',*codec,*audio,'-shortest',str(args.output)]
        process=jobs(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
            cwd=paths.temporary,env=paths.child_environment({}),shell=False)
        try:
            _,error=process.communicate(timeout=120)
            if process.returncode:raise RuntimeError(error.decode(errors='replace')[-2048:])
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=3)
            if process.stderr:process.stderr.close()
            jobs.close()
        receipt=dict(source_sha256=hashlib.sha256(args.audio.read_bytes()).hexdigest(),
            output=str(args.output),sha256=hashlib.sha256(args.output.read_bytes()).hexdigest(),
            bytes=args.output.stat().st_size,command=command,job_peak=jobs.peak,retired=True)
        with args.receipt.open('x',encoding='utf-8') as stream:json.dump(receipt,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(ok=True,bytes=receipt['bytes'])))


if __name__=='__main__':main()
