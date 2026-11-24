"""Disposable video preview, never an export/source identity or publication route."""
from dataclasses import dataclass
from fractions import Fraction
from math import lcm
import hashlib,json,logging,shutil,tempfile,time
from pathlib import Path
from threading import RLock
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.filesystem.export_identity import file_identity
from mediaforge.infrastructure.ffmpeg.child_process import ChildProcesses
from mediaforge.infrastructure.ffmpeg.preview_job import PreviewJobs
from mediaforge.application.contracts.source_timeline import SourceSignature,TimelineRequest,TimelineBuildControl
from mediaforge.infrastructure.ffmpeg.source_timeline import FfprobeTimelineIndexer


@dataclass(frozen=True)
class VideoPreview:
    source: object
    path: str
    width: int
    height: int
    divisor: int
    milliseconds: float
    peak_job: int
    source_hash: str
    source_guard: object
    output_hash: str = ''


def matching_clock(original,preview):
    """Exact rational relative PTS/durations, including VFR and nonzero origin."""
    def records(data):
        stream=data['streams'][0];clock=Fraction(stream['time_base'])
        frames=data['frames'];origin=int(frames[0]['best_effort_timestamp'])*clock
        return [(int(f['best_effort_timestamp'])*clock-origin,int(f['duration'])*clock) for f in frames]
    a,b=records(original),records(preview)
    if not a or len(a)>12000 or a!=b:raise ValueError('Preview frame timestamps do not exactly match the source.')
    return len(a)


def _audio_timing(stream):
    try:
        sample = Fraction(1, int(stream['sample_rate']))
        clock = Fraction(stream['time_base'])
        if sample <= 0 or clock <= 0:
            raise ValueError('Invalid audio clock')
        return sample, tuple(int(stream[field]) * clock for field in ('start_pts', 'duration_ts'))
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        raise ValueError('Exact audio timing is unavailable for this reduced preview.') from error


def matching_audio(original,preview):
    a=[s for s in original['streams'] if s.get('codec_type')=='audio']
    b=[s for s in preview['streams'] if s.get('codec_type')=='audio']
    if len(a)!=len(b):raise ValueError('Video preview lost an audio stream.')
    for source,result in zip(a,b):
        if any(source.get(k)!=result.get(k) for k in ('codec_name','sample_rate','channels')):
            raise ValueError('Video preview changed audio format.')
        sample, source_timing = _audio_timing(source)
        _, output_timing = _audio_timing(result)
        if any(abs(left-right)>sample for left,right in zip(source_timing,output_timing)):
            raise ValueError('Video preview changed audio timing.')


def _require_opaque_preview(pixel_format):
    if any(tag in (pixel_format or '') for tag in ('yuva','gbrap','rgba','bgra','argb','abgr')):
        raise ValueError('Reduced preview does not preserve this source’s alpha channel.')


def _aac_tail_copy_options(streams, video_clock):
    """Represent the declared audio end exactly in a disposable MOV proxy."""
    options = []
    movie_timescale = video_clock.denominator
    audio = [stream for stream in streams if stream.get('codec_type') == 'audio']
    for index, stream in enumerate(audio):
        if stream.get('codec_name') != 'aac':
            continue
        start, duration = stream.get('start_pts'), stream.get('duration_ts')
        if type(start) is not int or type(duration) is not int or duration <= 0:
            continue
        try:
            rate = int(stream['sample_rate'])
            if rate <= 0 or Fraction(stream['time_base']) != Fraction(1, rate):
                continue
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        end = start + duration
        scale = lcm(movie_timescale, rate)
        if abs(end) >= 2**53 or scale > 2**31 - 1:
            continue
        movie_timescale = scale
        # Some recorders leave AAC padding packets beyond the declared track
        # end. Keep copied payloads inside the span and cap the final duration
        # in the source's sample clock. The amount=0 filter only drops packets.
        options += [f'-bsf:a:{index}',
                    rf'noise=amount=0:drop=gte(pts\,{end}),setts=duration=min(DURATION\,{end}-PTS)']
    return ['-movie_timescale', str(movie_timescale), *options] if options else []


class FfmpegVideoPreviews:
    disk_limit=128*1024**2

    def __init__(self,runtime,resources,root,*,future_parent=None,timeline_budget=None):
        self.runtime,self.resources,self.root=runtime,resources,Path(root)
        self.entries=[];self.last_metrics=None;self.retained=[]
        self.future_parent=future_parent or (lambda sample:256*1024**2)
        self._cache_lock=RLock();self._users=set();self._originals={}
        self.timeline_budget=timeline_budget;self._timeline_lease=None

    def prepare(self,metadata,divisor,control):
        if self.runtime is None:raise ValueError('Managed video preview runtime is unavailable.')
        if divisor not in (2,4):raise ValueError('Invalid video preview tier.')
        formats = set(metadata.format_name.split(','))
        if not formats.intersection({'mov','mp4','matroska'}):
            raise ValueError('Reduced preview is unavailable for this container.')
        if metadata.width*metadata.height>3840*2160:
            raise ValueError('Reduced preview is limited to the UHD pixel budget.')
        if not metadata.duration_seconds or not 0<metadata.duration_seconds<=300:
            raise ValueError('Reduced preview requires a known duration of at most five minutes.')
        if metadata.info_facts is not None:
            _require_opaque_preview(metadata.info_facts.pixel_format)
        folder=None;jobs=None;children=None;locks=ReadLocks();keep=False;failed=False;start=time.perf_counter();stages=[]
        try:
            if self.timeline_budget is not None and self._timeline_lease is None:
                self._timeline_lease=self.timeline_budget.reserve_peak(16*1024**2)
            headroom=[]
            def admit_cpu():
                sample=self.resources.sample();future=self.future_parent(sample)
                head=max(0,min(sample['available_physical']-max(2*1024**3,int(sample['total_physical']*.15))-future,
                    sample['available_commit']-max(2*1024**3,int(sample['total_commit']*.10))-future))//4
                cap=min(512*1024**2,head)
                headroom.append(dict(cap=cap,future_parent=future,physical=sample['available_physical'],commit=sample['available_commit']))
                if cap<256*1024**2:raise ValueError('Not enough host headroom for a reduced video preview.')
                return cap
            cap=admit_cpu()
            # Qualified four-thread decode at the existing full Job allowance;
            # keep the encoder at two threads to limit retained frame backing.
            # Smaller host-derived allowances use the conservative two-thread path.
            decode_threads=4 if cap>=512*1024**2 else 2
            if not self.root.is_absolute() or str(self.root).startswith('\\\\'):
                raise ValueError('Video preview scratch requires an owned absolute local directory.')
            for parent in (self.root,*self.root.parents):
                if parent.exists() and parent.lstat().st_file_attributes&0x400:
                    raise ValueError('Video preview scratch cannot traverse reparse paths.')
            self.root.mkdir(parents=True,exist_ok=True)
            if self.root.lstat().st_file_attributes&0x400:raise ValueError('Preview scratch cannot be a reparse path.')
            if shutil.disk_usage(self.root).free<512*1024**2:raise ValueError('Not enough temporary space for a video preview.')
            source=Path(metadata.source_path);locks.parents(self.root);locks.parents(source.parent);locks.add(source)
            identity=file_identity(source,source.name,control)
            with self._cache_lock:
                hit=next((v for v in self.entries if v.source==metadata and v.divisor==divisor and v.source_hash==identity.sha256),None)
                if hit is not None:self._users.add(id(hit))
                # At most two completed tiers plus one incoming. Idle entries
                # are evicted before allocating any further owned disk space.
                self._trim_cache(2)
                if len(self.entries)>=3:raise ValueError('Video preview cache has no safe incoming slot.')
            if hit is not None:
                try:
                    if file_identity(Path(hit.path),'picture.mov',control).sha256!=hit.output_hash:
                        self.discard(hit);raise ValueError('Cached video preview identity changed.')
                    control.check()
                except BaseException:
                    with self._cache_lock:self._users.discard(id(hit))
                    raise
                self.last_metrics=dict(ms=(time.perf_counter()-start)*1000,reused=True,peak_job=0,
                    bytes=Path(hit.path).stat().st_size,width=hit.width,height=hit.height,divisor=divisor,source_hash=hit.source_hash)
                return hit
            folder=Path(tempfile.mkdtemp(prefix='preview-',dir=self.root));path=folder/'picture.mov'
            jobs=PreviewJobs(cap,admission=admit_cpu);children=ChildProcesses(jobs,RuntimeError,logging.getLogger('video-preview'))
            def run(cmd):
                t=time.perf_counter();value=children.run(cmd,control,90)
                stages.append(dict(executable=Path(cmd[0]).name,ms=(time.perf_counter()-t)*1000));return value
            probe=json.loads(run([str(self.runtime.ffprobe),'-v','error','-show_streams','-of','json',str(source)]))
            video=next(s for s in probe['streams'] if s.get('codec_type')=='video')
            _require_opaque_preview(video.get('pix_fmt'))
            if (video.get('color_transfer') not in (None,'unknown','bt709','iec61966-2-1')
                    or video.get('color_space') not in (None,'unknown','bt709','bt470bg','smpte170m','rgb')
                    or any(x.get('rotation',0)!=0 for x in video.get('side_data_list',[]))):
                raise ValueError('Reduced preview for this color/rotation class is not qualified. Use Auto or 1:1.')
            for track in probe['streams']:
                if track.get('codec_type') == 'audio':
                    _audio_timing(track)
            width=max(2,metadata.width//divisor//2*2);height=max(2,metadata.height//divisor//2*2)
            clock=Fraction(video['time_base'])
            command=[str(self.runtime.ffmpeg),'-nostdin','-hide_banner','-v','error','-n',
                '-threads',str(decode_threads),'-filter_threads','1','-copyts','-noautorotate','-i',str(source),
                '-map','0:v:0','-map','0:a?','-sn','-dn','-vf',f'scale={width}:{height}:flags=area',
                '-c:v','libx264','-preset','ultrafast','-tune','zerolatency','-crf','18','-pix_fmt','yuv420p',
                '-threads','2','-c:a','copy','-enc_time_base:v','filter','-fps_mode:v','passthrough',
                '-video_track_timescale',str(clock.denominator),'-avoid_negative_ts','disabled',
                '-map_chapters','-1','-fs',str(self.disk_limit)]
            if formats.intersection({'mov', 'mp4'}):
                command.extend(_aac_tail_copy_options(probe['streams'], clock))
            command.append(str(path))
            run(command)
            # Freeze both file object and namespace before any validation, and
            # keep them frozen through Qt use; no validate-then-reopen gap.
            locks.parents(folder);locks.add(path)
            if not 0<path.stat().st_size<=self.disk_limit:raise ValueError('Video preview exceeded its disk allowance.')
            class ProbeControl(TimelineBuildControl):
                def __init__(self):
                    super().__init__()
                    class Canceled:
                        def is_set(_):
                            try:control.check();return False
                            except RuntimeError:return True
                    self.cancelled=Canceled()
                def spawn(self,factory,command,**options):
                    with control.starting(),self._lock:
                        self._process=factory(command,**options);return self._process
            def timing(p):
                owned=ProbeControl()
                t=time.perf_counter()
                result=FfprobeTimelineIndexer(self.runtime.ffprobe,process_factory=jobs,decoder_threads=decode_threads,maximum_bytes=4*1024**2).build(
                    TimelineRequest('preview',SourceSignature.capture(p),0),owned)
                stages.append(dict(phase='source-timing' if p==source else 'proxy-timing',ms=(time.perf_counter()-t)*1000))
                if owned.owns_process:control.retain_unreaped_child();raise RuntimeError('Preview timing child ownership unresolved.')
                if result.timeline is None:raise ValueError(result.error)
                return result.timeline
            with self._cache_lock:original=self._originals.get(identity.sha256)
            if original is None:
                original=timing(source)
                with self._cache_lock:
                    if len(self._originals)>=2:self._originals.pop(next(iter(self._originals)))
                    self._originals[identity.sha256]=original
            else:stages.append(dict(phase='source-timing-reused-under-immutable-hash',ms=0))
            reduced=timing(path)
            if (original.count!=reduced.count or original.last_duration_us!=reduced.last_duration_us
                    or any(a-original.timestamps[0]!=b-reduced.timestamps[0]
                           for a,b in zip(original.timestamps,reduced.timestamps))):
                raise ValueError('Video preview does not preserve source presentation ordinals/timing.')
            count=original.count
            output_probe=json.loads(run([str(self.runtime.ffprobe),'-v','error','-show_streams','-of','json',str(path)]))
            matching_audio(probe,output_probe)
            actual=next(s for s in output_probe['streams'] if s.get('codec_type')=='video')
            if (actual['width'],actual['height'])!=(width,height):raise ValueError('Video preview raster disagrees with its tier.')
            if file_identity(source,source.name,control)!=identity:raise ValueError('Video source changed during preview preparation.')
            control.check();jobs.close()
            output_hash=file_identity(path,'picture.mov',control).sha256
            value=VideoPreview(metadata,str(path),width,height,divisor,(time.perf_counter()-start)*1000,jobs.peak,identity.sha256,locks,output_hash)
            with self._cache_lock:
                self.entries.append(value);self._users.add(id(value));self._trim_cache(2)
            keep=True
            self.last_metrics=dict(ms=value.milliseconds,peak_job=jobs.peak,limit=cap,frames=count,bytes=path.stat().st_size,
                width=width,height=height,divisor=divisor,source_hash=identity.sha256,command=command,stages=stages,
                decoder_threads=decode_threads,encoder_threads=2,headroom=headroom,applied_limits=jobs.applied,assigned_before_resume=True,active_process_limit=1)
            return value
        finally:
            try:
                if children:children.check_reaped()
                if jobs:jobs.close()
                if not keep:locks.close()
                if folder is not None and not keep:self._remove(folder)
                if control.cleanup_failed:raise RuntimeError('Preview child ownership unresolved.')
            except BaseException:
                self.retained.append((folder,jobs,locks,children))
                failed=True
                raise

    def _remove(self,folder):
        folder=Path(folder)
        if folder.parent!=self.root or folder.lstat().st_file_attributes&0x400:raise RuntimeError('Preview scratch ownership changed.')
        files=list(folder.iterdir())
        if any(p.name!='picture.mov' or p.lstat().st_file_attributes&0x400 for p in files):raise RuntimeError('Unexpected preview scratch member.')
        for p in files:p.unlink()
        folder.rmdir()

    def discard(self,value):
        with self._cache_lock:
            if value in self.entries:
                value.source_guard.close();self._remove(Path(value.path).parent);self.entries.remove(value);self._users.discard(id(value))

    def _trim_cache(self,maximum):
        while len(self.entries)>maximum:
            idle=next((v for v in self.entries if id(v) not in self._users),None)
            if idle is None:break
            self.discard(idle)

    def release(self,value):
        with self._cache_lock:
            self._users.discard(id(value));self._trim_cache(2)

    def close(self):
        if self.retained:raise RuntimeError('Video preview child/scratch cleanup remains unresolved.')
        for value in tuple(self.entries):self.discard(value)
        self._originals.clear()
        if self.timeline_budget is not None and self._timeline_lease is not None:
            self.timeline_budget.release(self._timeline_lease);self._timeline_lease=None
