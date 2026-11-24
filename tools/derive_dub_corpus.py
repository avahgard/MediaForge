"""One declared eligible hour fixture from the retained public OCW recording.

Fixture creation ONLY: video is explicitly converted to H.264 High/25 fps and
extended by its final picture to cover the complete original audio. AAC packet
bytes and their clocks must remain identical. The product never does this
conversion and does not claim the original multi-video archive is copy-eligible.
"""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import time


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True)
    args=p.parse_args()
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.infrastructure.filesystem.image_output import ReadLocks
    from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
    from mediaforge.infrastructure.ffmpeg.preview_job import PreviewJobs
    from mediaforge.infrastructure.ffmpeg.dub_process import ProbeProcess
    from mediaforge.infrastructure.ffmpeg.dub_export import DubExportExecutor
    from mediaforge.features.dub.contracts import DubControl
    paths=ProfilePaths.for_test(args.root);paths.activate_temporary();runtime=resolve_ffmpeg_runtime(profile=paths)
    source=paths.root/'fixtures/MIT6_006F11_rec09_300k.mp4';output=paths.root/'fixtures/corpus-rec09-qualified.mp4'
    if output.exists() or args.receipt.exists() or not args.receipt.is_relative_to(paths.logs):p.error('New explicit corpus objects required.')
    jobs=PreviewJobs(512*1024**2);control=DubControl();process=None;started=time.monotonic();result=dict(ok=False)
    try:
        with ReadLocks() as locks:
            for path in (source,runtime.ffmpeg,runtime.ffprobe):locks.parents(path.parent);locks.add(path)
            with source.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
            if sha!='b6c5142e506e850ab0f69928d79c9cff6e07d63bd710eb4d47870bf2ed4b1e06':
                raise ValueError('Retained public corpus source identity differs.')
            command=[str(runtime.ffmpeg),'-v','error','-nostdin','-n','-threads','2','-filter_threads','1',
                '-protocol_whitelist','file','-copyts','-i',str(source),'-map','0:0','-map','0:2',
                '-map_metadata','-1','-map_chapters','-1','-sn','-dn',
                '-vf','fps=25,tpad=stop_mode=clone:stop_duration=2','-c:v','libx264','-profile:v','high',
                '-pix_fmt','yuv420p','-preset','veryfast','-crf','24','-threads:v','2',
                '-c:a','copy','-avoid_negative_ts','disabled','-t','3660.6','-fs',str(256*1024**2),str(output)]
            process=jobs(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                cwd=paths.temporary,env=paths.child_environment({}),shell=False)
            _,diagnostic=process.communicate(timeout=900)
            result.update(encoder_exit=process.returncode,diagnostic=diagnostic.decode(errors='replace')[-4096:],command=command)
            if process.returncode:raise RuntimeError('Declared corpus fixture conversion failed.')
            runner=ProbeProcess(jobs,paths,control,300);executor=DubExportExecutor(paths,runtime)
            before=executor.packet_digest(runner,source,2,Fraction(1,48000))
            after=executor.packet_digest(runner,output,1,Fraction(1,48000))
            if before!=after:raise ValueError('Corpus audio packet payload/clocks changed; no fixture admission.')
            with output.open('rb') as stream:output_sha=hashlib.file_digest(stream,'sha256').hexdigest()
            result.update(ok=True,source_sha256=sha,output_sha256=output_sha,output=str(output),
                source_audio_packet_digest=before,audio_packets_preserved=True,bytes=output.stat().st_size,
                intended_video_seconds='3660.6',declared_changes=['select primary video and sole English audio',
                    'omit secondary image/subtitle streams and metadata','transcode fixture video to H.264 High/25 fps',
                    'clone final source picture to cover the complete original audio span'],
                origin='https://ocw.mit.edu/courses/6-006-introduction-to-algorithms-fall-2011/resources/recitation-9-rolling-hashes-amortized-analysis/',
                attribution='Victor Costan. MIT OCW 6.006 Fall 2011, Recitation 9. CC BY-NC-SA. Modified fixture; no MIT endorsement.',
                source_preservation_scope='Product compares against THIS derived source; not archive-video picture identity.')
    except Exception as error:result['detail']=str(error)
    finally:
        if process is not None:
            if process.poll() is None:process.terminate();process.wait(timeout=3)
            if process.stderr:process.stderr.close()
        jobs.close();result.update(job_peak=jobs.peak,retired=True,seconds=time.monotonic()-started)
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:result.get(k) for k in ('ok','detail','encoder_exit','bytes','seconds','retired')},ensure_ascii=False))
    if not result['ok']:raise SystemExit(2)


if __name__=='__main__':main()
