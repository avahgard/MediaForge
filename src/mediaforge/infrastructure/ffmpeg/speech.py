"""Bounded C3 PCM pipes over the single authenticated managed FFmpeg pair."""
import array
import ctypes as C
from ctypes import wintypes as W
import hashlib
import math
from pathlib import Path
from queue import Queue, Empty, Full
import re
import subprocess
from threading import Event, Thread
import time
from uuid import uuid4

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.features.dub.speech import half_up
from mediaforge.features.dub.block_timing import STRICT_TEMPO,tempo_factors
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.images.resources import _Memory
from mediaforge.infrastructure.ffmpeg.preview_job import PreviewJobs
from mediaforge.infrastructure.models.owned import Ownership


class PcmWriter:
    """Fresh delete-pending object; no pathname writer or persistent DACL change.

    Partial/cancelled files disappear on handle close, including abrupt exit.
    Successful files are immutable stage receipts, never final output publication.
    """
    def __init__(self,path,control,max_samples):
        self.path=path;self.control=control;self.maximum=max_samples*2
        self.owner=Ownership();self.handle=None;self.count=0;self.digest=hashlib.sha256()
    def __enter__(self):
        try:
            self.owner.parents.parents(self.path.parent)
            self.handle=self.owner.open(self.path.parent/('.pcm-'+uuid4().hex),0xC0010000,0,1)
            self.owner.pending(self.handle,True);self.owner.info(self.handle,links=0)
            return self
        except BaseException:self.owner.close();raise
    def write(self,raw):
        if len(raw)%2 or self.count+len(raw)>self.maximum:
            raise LocalAiError('disk_budget','PCM exceeds its reserved sample bound.')
        for position in range(0,len(raw),32768):
            self.control.check();block=raw[position:position+32768]
            self.owner.info(self.handle,links=0)
            written=W.DWORD()
            self.owner.require(self.owner.api.WriteFile(self.handle,C.c_char_p(block),len(block),C.byref(written),None))
            if written.value!=len(block):raise LocalAiError('storage_refused','Incomplete PCM write.')
            self.digest.update(block);self.count+=len(block)
    def silence(self,samples):
        if samples<0:raise LocalAiError('timing_unfit','Overlapping speech composition refused.')
        block=bytes(32768)
        while samples:
            n=min(samples,16384);self.write(block[:2*n]);samples-=n
    def __exit__(self,kind,error,tb):
        try:
            if kind is None:
                self.control.check();self.owner.require(self.owner.api.FlushFileBuffers(self.handle))
                self.owner.pending(self.handle,False)
                try:self.owner.rename(self.handle,self.path)
                except BaseException:self.owner.pending(self.handle,True);raise
        finally:self.owner.close()


class SpeechAudio:
    def __init__(self,paths,runtime):
        self.paths=paths;self.runtime=runtime;self.receipts=[]
    def binding(self):
        from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest
        return FfmpegManifest.load().ffmpeg_sha256

    def support_binding(self):
        from mediaforge.features.dub import alignment
        from mediaforge.infrastructure.ffmpeg import speech_support
        return dict(policy=alignment.POLICY,vad=alignment.VAD_SHA256,
            helpers=[hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in (alignment,speech_support)])

    def source_support(self,source,plan,track,recognition,control):
        from mediaforge.infrastructure.ffmpeg.speech_support import scan_source
        return scan_source(self,source,plan,track,recognition,control)

    def validate_source_silence(self,path,ledger,total,control):
        from mediaforge.infrastructure.ffmpeg.speech_support import verify_protected_zeros
        verify_protected_zeros(path,ledger,total,control)

    def admission(self):
        kernel=C.WinDLL('kernel32',use_last_error=True)
        kernel.GlobalMemoryStatusEx.argtypes=[C.c_void_p];kernel.GlobalMemoryStatusEx.restype=W.BOOL
        memory=_Memory();memory.length=C.sizeof(memory)
        if not kernel.GlobalMemoryStatusEx(C.byref(memory)):
            raise LocalAiError('resource_unavailable','Native memory admission is unavailable.')
        physical=max(0,memory.avail_phys-max(2*1024**3,memory.total_phys*15//100)-64*1024**2)
        commit=max(0,memory.avail_commit-max(2*1024**3,memory.total_commit//10)-64*1024**2)
        limit=min(2*1024**3,physical//4,commit//4)
        if limit<256*1024**2:raise LocalAiError('resource_unavailable','Insufficient headroom for bounded speech decoding.')
        return limit

    def recognition_windows(self,path,samples,control):
        from mediaforge.features.dub.speech import Window
        # Only digital-zero pauses qualify as an additional seam. No threshold
        # can discard quiet speech/noise or authorize a clipped word.
        candidates=[];position=0;zero_start=None
        for raw in blocks(path,control):
            values=array.array('h',raw)
            for value in values:
                if value==0:
                    if zero_start is None:zero_start=position
                elif zero_start is not None:
                    if position-zero_start>=8000:candidates.append((position+zero_start)//2)
                    zero_start=None
                position+=1
        if position!=samples:raise LocalAiError('invalid_timing','Recognition spool count differs.')
        # A trailing pause belongs to the complete timeline, not a new speech core.
        if len(candidates)>5000:raise LocalAiError('resource_unavailable','Excessive recognition seam metadata.')
        start=0
        while start<samples:
            maximum=min(samples,start+26*16000)
            choices=[n for n in candidates if start+4*16000<=n<maximum-2*16000]
            # Commit at the first reliable long pause. Combining several known
            # pauses in one recognition core can collapse their independent
            # speech starts into a single coarse model segment.
            end=choices[0] if choices else maximum
            yield Window(max(0,start-32000),min(samples,end+32000),start,end)
            start=end

    def pipe(self,args,control,*,consume=lambda block:None,maximum=0,timeout=300,locks=()):
        """Lossless bounded stdout queue and bounded stderr tail; no transcript logs."""
        queue=Queue(maxsize=8);stop=Event();diagnostic=bytearray();readers=[];process=None
        jobs=PreviewJobs(0,admission=self.admission);total=0;started=time.monotonic()
        verified_at=spawned_at=None
        def stdout():
            try:
                while not stop.is_set():
                    block=process.stdout.read(32768)
                    while not stop.is_set():
                        try:queue.put(block,timeout=.05);break
                        except Full:pass
                    if not block:break
            except BaseException as error:
                while not stop.is_set():
                    try:queue.put(error,timeout=.05);break
                    except Full:pass
        def stderr():
            try:
                while block:=process.stderr.read(4096):
                    diagnostic.extend(block);del diagnostic[:-16384]
            except OSError:pass
        owned=ReadLocks()
        try:
            owned.parents(self.runtime.ffmpeg.parent);owned.add(self.runtime.ffmpeg)
            # Rehash exact FFmpeg under deny-write/delete ownership each use.
            from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest
            if digest(self.runtime.ffmpeg,control)!=FfmpegManifest.load().ffmpeg_sha256:
                raise LocalAiError('runtime_missing','Managed FFmpeg digest differs.')
            verified_at=time.monotonic()
            for path in locks:
                owned.parents(path.parent);owned.add(path)
                if path.stat().st_nlink!=1:raise LocalAiError('source_changed','Aliased speech artifact refused.')
            command=[str(self.runtime.ffmpeg),'-nostdin','-hide_banner','-xerror','-threads','2','-filter_threads','1',*args]
            with control.starting():
                process=jobs(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    shell=False,cwd=self.paths.temporary,env=self.paths.child_environment({}))
            spawned_at=time.monotonic()
            readers=[Thread(target=stdout,daemon=True),Thread(target=stderr,daemon=True)]
            for thread in readers:thread.start()
            while True:
                control.check()
                if time.monotonic()-started>timeout:raise LocalAiError('provider_timeout','Bounded speech audio operation timed out.')
                try:block=queue.get(timeout=.05)
                except Empty:continue
                if isinstance(block,BaseException):raise LocalAiError('invalid_output','PCM pipe failed.') from block
                if not block:break
                total+=len(block)
                if total>maximum:raise LocalAiError('disk_budget','Audio decoder exceeded admitted output bound.')
                consume(block)
            process.wait(timeout=2)
            for thread in readers:thread.join(timeout=2)
            if process.returncode:raise LocalAiError('invalid_output','Managed speech audio operation failed; no partial result accepted.')
            return bytes(diagnostic)
        finally:
            stop.set()
            try:
                if process is not None:
                    if process.poll() is None:
                        process.terminate()
                        try:process.wait(timeout=2)
                        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
                    for thread in readers:thread.join(timeout=2)
                    if any(t.is_alive() for t in readers):raise RuntimeError('Unretired speech pipe reader.')
                    for stream in (process.stdout,process.stderr):stream.close()
                jobs.close()
                if process is not None:process._handle.Close()
                owned.close()  # identities remain leased until the child is reaped
            except BaseException as error:
                control.retain_unreaped_child()
                raise LocalAiError('lifecycle_failed','Speech child/Job/reader ownership unresolved.',retained_resources=(jobs,process,owned,*readers)) from error
            self.receipts.append(dict(seconds=time.monotonic()-started,pcm_bytes=total,job_commit_peak=jobs.peak,
                verification_seconds=(verified_at-started if verified_at else None),
                spawn_seconds=(spawned_at-verified_at if spawned_at and verified_at else None),
                limits=jobs.applied,retired=True))
            self.receipts[:]=self.receipts[-64:]

    def extraction(self,source,plan):
        # Copy source PTS into a sample-clock filter; gaps are explicit silence.
        # resampling preserves normalized audio timing, then clips/pads picture span.
        origin=format(float(plan.origin),'.17g');end=format(float(plan.origin+plan.duration),'.17g')
        samples=half_up(plan.duration*16000)
        first=half_up(plan.origin*16000)
        filters=f'aresample=16000:async=1:first_pts={first},atrim=start={origin}:end={end},asetpts=PTS-STARTPTS,apad,atrim=end_sample={samples}'
        return samples,['-v','error','-copyts','-i',str(source),'-map',f'0:{plan.audio_index}','-vn','-sn','-dn',
            '-af',filters,'-ac','1','-ar','16000','-f','s16le','pipe:1']

    def extract(self,source,plan,target,control):
        samples,args=self.extraction(source,plan)
        with PcmWriter(target,control,samples) as writer:
            self.pipe(args,control,consume=writer.write,
                maximum=samples*2,timeout=max(300,float(plan.duration)*10),locks=(source,))
            if writer.count!=samples*2:raise LocalAiError('invalid_timing','Incomplete extraction span.')
        return samples

    def verify_extraction(self,source,plan,recognition,control):
        """Authenticate the complete ASR representation, without changing it.

        A resampling filter uses neighboring samples: an exact native zero at a
        boundary need not yield a zero S16 sample at the same rational instant.
        Compare against the identical authenticated decode instead of inferring
        resampled zeros. Native all-channel zeros remain the silence authority.
        No extra spool, tolerance, trimming, source shift or filter change.
        """
        samples,args=self.extraction(source,plan);position=0
        with ReadLocks() as owned:
            owned.parents(recognition.parent);owned.add(recognition)
            if recognition.stat().st_nlink!=1 or recognition.stat().st_size!=samples*2:
                raise LocalAiError('speech_boundary_uncertain','Recognition/source representation length or ownership differs.')
            with recognition.open('rb') as stream:
                def compare(raw):
                    nonlocal position
                    control.check()
                    if stream.read(len(raw))!=raw:
                        raise LocalAiError('speech_boundary_uncertain','Recognition/source representation bytes differ.')
                    position+=len(raw)
                self.pipe(args,control,consume=compare,maximum=samples*2,
                    timeout=max(300,float(plan.duration)*10),locks=(source,recognition))
                if position!=samples*2 or stream.read(1):
                    raise LocalAiError('speech_boundary_uncertain','Recognition/source representation is incomplete.')

    def convert(self,source,target,control,*,rate=22050,tempo=None,maximum=120*48000,gain=None,limiter=False,tempo_policy=STRICT_TEMPO):
        filters=self.level_filters(gain) if limiter else ['aresample=48000']
        if tempo is not None:
            factors=tempo_factors(tempo,tempo_policy)
            if tempo!=1:filters.extend('atempo='+format(float(f),'.17g') for f in factors)
        if gain is not None and not limiter:filters.append('volume='+format(gain,'.12g')+'dB')
        with PcmWriter(target,control,maximum) as writer:
            self.pipe(['-v','error','-f','s16le','-ar',str(rate),'-ac','1','-i',str(source),'-af',','.join(filters),
                '-ar','48000','-ac','1','-f','s16le','pipe:1'],control,consume=writer.write,maximum=2*maximum,locks=(source,))
        return writer.count//2

    def level(self,source,target,control,samples):
        before=self.measure(source,control)
        integrated,peak=before
        gain=min(12.,-16.-integrated)
        # One fixed calibration analysis into a NULL sink. No extra PCM spool,
        # resynthesis, unbounded normalization loop or dynamic loudnorm fallback.
        trial=self.measure(source,control,filters=self.level_filters(gain))
        gain=min(12.,gain+(-16.-trial[0]))
        count=self.convert(source,target,control,rate=48000,maximum=samples,gain=gain,limiter=True)
        after=self.measure(target,control)
        if count!=samples or not all(math.isfinite(n) for n in after) or after[1]>-1.3:
            raise LocalAiError('validation_failed','Speech level/sample validation failed.')
        return after,('low_level_speech',) if after[0]<-17 else ()

    @staticmethod
    def level_filters(gain):
        # Fixed float oversampling prevents intermediate S16 clipping. Filter
        # lookahead is explicitly latency-compensated; no time stretching here.
        return ['aresample=192000:osf=fltp','volume='+format(gain,'.12g')+'dB',
            'alimiter=limit=0.8222426499:level=false:attack=5:release=50:latency=true',
            'aresample=48000:osf=s16:dither_method=none']

    def measure(self,path,control,*,filters=()):
        tail=self.pipe(['-v','info','-f','s16le','-ar','48000','-ac','1','-i',str(path),'-af',','.join([*filters,'ebur128=peak=true']),
            '-f','null','-'],control,locks=(path,),timeout=max(300,path.stat().st_size/9600))
        text=tail.decode('ascii',errors='replace')
        found=re.findall(r'Integrated loudness:\s*I:\s*([-\w.]+) LUFS.*?True peak:\s*Peak:\s*([-\w.]+) dBFS',text,re.S)
        if not found:raise LocalAiError('validation_failed','Independent speech loudness measurement unavailable.')
        values=tuple(float(n) for n in found[-1])
        if not all(math.isfinite(n) for n in values) or values[0]<=-70:
            raise LocalAiError('no_speech','Silent/unmeasurable speech composition refused.')
        return values

def digest(path,control):
    hashed=hashlib.sha256()
    with path.open('rb') as file:
        while block:=file.read(32768):control.check();hashed.update(block)
    return hashed.hexdigest()

def blocks(path,control):
    with path.open('rb') as stream:
        while block:=stream.read(32768):control.check();yield block

def trim_silence(raw):
    # Only digital-zero exterior samples are proven silence. Keep 20 ms guards.
    # No energy threshold, interior editing or phoneme inference.
    values=array.array('h',raw)
    if not values or not any(values):raise LocalAiError('invalid_output','Silent voice fragment refused.')
    first=next(i for i,v in enumerate(values) if v);last=len(values)-next(i for i,v in enumerate(reversed(values)) if v)
    guard=960
    return raw[max(0,first-guard)*2:min(len(values),last+guard)*2]
