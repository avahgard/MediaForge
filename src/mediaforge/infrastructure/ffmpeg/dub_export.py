"""Dub copy, explicit compatible encoding and audio mux over managed FFmpeg.

Only the explicit Compatible MP4 route selects a video encoder. Seekable muxing uses an inherited owned
file descriptor; validation decodes that SAME still-unlinked output object.
"""
from contextlib import closing, ExitStack
from dataclasses import replace
from fractions import Fraction
import hashlib
import json
import shutil
import subprocess
from threading import Thread
import time

from mediaforge.features.dub.contracts import OutputMode, DubError, Reason, ClockFacts
from mediaforge.features.media.export import ExportError, ExportResult
from mediaforge.infrastructure.filesystem.dub_source import ProtectedDubSource
from mediaforge.infrastructure.filesystem.dub_output import DubOutput
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.ffmpeg.dub_probe import FfprobeDubInspector, parse_metadata, ratio, integer
from mediaforge.infrastructure.ffmpeg.dub_process import ProbeProcess
from mediaforge.infrastructure.ffmpeg.preview_job import PreviewJobs
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest
from mediaforge.infrastructure.ffmpeg.speech import SpeechAudio, digest
from mediaforge.features.dub.audio_validation import AudioSyncValidation


class OutputInspector(FfprobeDubInspector):
    def command(self, source):
        # Only this internally generated object may use fd. User imports keep
        # the accepted file-only protocol/source eligibility contract.
        return [str(self.runtime.ffprobe),'-v','error','-threads','2',
                '-protocol_whitelist','fd,file','-format_whitelist','mov,matroska,wav,mp3',
                '-enable_drefs','0','-use_absolute_path','0','-fd','0']


class DubExportExecutor:
    def __init__(self, paths, runtime):
        self.paths,self.runtime=paths,runtime
        self.last_receipt=None;self.last_diagnostic=''

    def execute(self, source, plan, composition, destination, control, progress=lambda *args:None):
        if self.runtime is None:
            raise ExportError('runtime_missing','Authenticated managed FFmpeg is required.')
        if (source.identity!=plan.source or not composition.internal_only or composition.samples!=plan.sample_count
                or (composition.sample_rate,composition.channels,composition.sample_width)!=(48000,1,2)):
            raise ExportError('invalid_request','Dub export requires the exact source plan and complete internal PCM.')
        self.paths.prepare();stack=ExitStack();jobs=PreviewJobs(512*1024**2)
        diagnostics=bytearray();started=time.monotonic();active=[]
        try:
            protected=stack.enter_context(ProtectedDubSource(plan.source.path,control))
            if protected.identity!=plan.source:
                raise ExportError('source_changed','Source differs from Dub inspection; no publication.')
            locks=stack.enter_context(ReadLocks())
            for path in (self.runtime.ffmpeg,self.runtime.ffprobe,composition.path):
                locks.parents(path.parent);locks.add(path)
                if path.stat().st_nlink!=1:raise ExportError('unsafe_alias','Export dependency is aliased.')
            manifest=FfmpegManifest.load()
            for path,expected in ((self.runtime.ffmpeg,manifest.ffmpeg_sha256),
                                  (self.runtime.ffprobe,manifest.ffprobe_sha256),(composition.path,composition.sha256)):
                if digest(path,control)!=expected:
                    raise ExportError('integrity_failed','Export source/runtime/PCM changed.')
            if composition.path.stat().st_size!=plan.sample_count*2:
                raise ExportError('validation_failed','Internal speech sample count differs.')
            expected=plan.source.size+plan.sample_count*2+64*1024**2
            if plan.route.mode==OutputMode.COMPATIBLE_MP4:
                expected+=int(plan.duration*2_000_000)+32*1024**2
            if shutil.disk_usage(destination.parent).free<expected+max(512*1024**2,expected//10):
                raise ExportError('disk_budget','Insufficient selected-destination space for safe Dub staging.')
            output=stack.enter_context(DubOutput(destination,control))
            environment=self.paths.child_environment({})
            def factory(command,**options):
                options.update(cwd=self.paths.temporary,env=environment)
                process=jobs(command,**options);active.append(process)
                return process
            def output_factory(command,**options):
                with output.descriptor() as descriptor:
                    options['stdin']=descriptor
                    return factory(command,**options)
            timeout=max(300,float(plan.duration)*10)
            source_process=ProbeProcess(factory,self.paths,control,timeout)
            result_process=ProbeProcess(output_factory,self.paths,control,timeout)
            progress('export',0,0)
            command=self.command(source,plan,composition.path)
            with output.descriptor() as descriptor,control.starting():
                process=factory(command,stdin=subprocess.DEVNULL,stdout=descriptor,stderr=subprocess.PIPE,shell=False)
            errors=[]
            def stderr():
                try:
                    while block:=process.stderr.read(4096):
                        diagnostics.extend(block);del diagnostics[:-16384]
                except OSError:errors.append(True)
            reader=Thread(target=stderr,daemon=True);reader.start()
            try:
                deadline=time.monotonic()+timeout
                while process.poll() is None:
                    control.check()
                    if time.monotonic()>deadline:raise ExportError('timeout','Dub mux exceeded its deadline.')
                    time.sleep(.05)
                reader.join(2)
                if reader.is_alive() or errors:
                    raise ExportError('lifecycle_failed','Dub mux diagnostic reader did not retire.',retained_resources=(reader,))
                if process.returncode:
                    raise ExportError('process_failed','Dub mux failed; no output is published.')
            finally:
                self._stop(process,control);reader.join(2)
                if reader.is_alive():
                    raise ExportError('lifecycle_failed','Dub mux reader remains owned.',retained_resources=(reader,))
                process.stderr.close()
                self.last_diagnostic=diagnostics.decode('utf-8',errors='replace')[-4096:]
            control.check();progress('validate',0,0)
            inspector=OutputInspector(self.runtime,self.paths)
            data=self.metadata(result_process,inspector.command('fd:')+['-show_streams','-show_format','-of','json','fd:'])
            self.validate_streams(source,plan,data)
            audio=next(row for row in data['streams'] if row['codec_type']=='audio')
            clock=inspector.scan(result_process,'fd:',audio['index'],ratio(audio['time_base'],positive=True),48000)
            tolerance=1024 if plan.route.audio_codec=='aac' else 0
            if not plan.sample_count<=clock.samples<=plan.sample_count+tolerance:
                raise ExportError('validation_failed','Decoded Dub audio does not preserve the complete sample span.')
            if clock.duration!=Fraction(clock.samples,48000):
                raise ExportError('validation_failed','Dub audio contains timestamp gaps/overlaps.')
            if source.video and plan.route.mode in (OutputMode.SAME_AS_SOURCE,OutputMode.COMPATIBLE_MP4):
                video=next(row for row in data['streams'] if row['codec_type']=='video')
                actual_clock=inspector.scan(result_process,'fd:',video['index'],ratio(video['time_base'],positive=True))
                if (actual_clock.first!=source.video.clock.first or actual_clock.end!=source.video.clock.end
                        or actual_clock.records!=source.video.clock.records
                        or actual_clock.interval_sha256!=source.video.clock.interval_sha256):
                    action=('video copy' if plan.route.mode==OutputMode.SAME_AS_SOURCE else 'compatible encoding')
                    raise ExportError('validation_failed',
                        f'Source picture timestamps/durations changed during {action}; no output was published. '
                        'Select Same as Source if eligible, or Dubbed Audio Only.')
                if abs(clock.first-plan.origin)>Fraction(1,48000):
                    raise ExportError('validation_failed','Dub audio and source video origins differ.')
                if plan.route.mode==OutputMode.SAME_AS_SOURCE:
                    expected_packets=self.packet_digest(source_process,source.identity.path,source.video.index,source.video.time_base)
                    actual_packets=self.packet_digest(result_process,'fd:',video['index'],ratio(video['time_base'],positive=True),owned=True)
                    if expected_packets!=actual_packets:
                        raise ExportError('validation_failed','Encoded video payload/PTS/DTS/config differs from source.')
                    expected_frames=self.picture_digest(source_process,source.identity.path,source.video.index,source.video.pixel_format)
                    actual_frames=self.picture_digest(result_process,'fd:',video['index'],source.video.pixel_format,owned=True)
                    if expected_frames!=actual_frames:
                        raise ExportError('validation_failed','Independently decoded source pictures differ.')
                else:
                    decoded_video=self.picture_digest(result_process,'fd:',video['index'],'yuv420p',owned=True)
                    if decoded_video[0]!=source.video.clock.records:
                        raise ExportError('validation_failed','Encoded picture decode is incomplete.')
            # Independently decode every audio sample. Lossless routes prove
            # byte equality; lossy routes prove the qualified count and finite
            # PCM decode, with codec priming/tail behavior separately gated.
            decoded=self.audio_digest(result_process,audio['index'],plan.sample_count+tolerance,composition,
                                      lossless=plan.route.audio_codec in ('pcm_s16le','flac'))
            if decoded['samples']!=clock.samples:
                raise ExportError('validation_failed','Independent audio decoder count disagrees with FFprobe.')
            if plan.route.audio_codec in ('pcm_s16le','flac') and decoded['sha256']!=composition.sha256:
                raise ExportError('validation_failed','Lossless Dub speech bytes differ from composition.')
            identity_=output.validated()
            control.check();progress('publish',0,0)
            published=output.publish()
            self.last_receipt=dict(status='published_pending_retirement',route=plan.route.copy_class or plan.route.container,
                video_stream_copy=plan.route.mode==OutputMode.SAME_AS_SOURCE,
                sample_count=clock.samples,audio_origin=str(clock.first),audio_end=str(clock.end),
                decoded=decoded,sha256=identity_.files[0].sha256,seconds=time.monotonic()-started,
                source_sha256=plan.source.sha256)
            result=ExportResult(published,identity_)
        except BaseException as error:
            try:self.retire(jobs,active,control)
            except BaseException as retirement:
                retained=stack.pop_all()
                control.retain((jobs,active,retained))
                raise ExportError('lifecycle_failed','Dub export owners remain retained.',
                                  retained_resources=(jobs,active,retained)) from retirement
            if getattr(error,'retained_resources',()):
                retained=stack.pop_all();control.retain((retained,error.retained_resources))
                raise ExportError('lifecycle_failed','Dub export lifecycle remains unresolved.',
                                  retained_resources=(retained,*error.retained_resources)) from error
            raise
        else:
            try:self.retire(jobs,active,control)
            except BaseException as error:
                retained=stack.pop_all();control.retain((jobs,active,retained))
                raise ExportError('lifecycle_failed','Dub publication retirement remains unresolved.',
                                  retained_resources=(jobs,active,retained)) from error
            self.last_receipt.update(job_commit_peak=jobs.peak,
                                     job_limits=jobs.applied,children=len(active))
        finally:
            try:stack.close()
            except BaseException:
                if self.last_receipt is not None:self.last_receipt['status']='published_cleanup_failed'
                raise
        self.last_receipt['status']='validated_published'
        return result

    @staticmethod
    def _stop(process,control):
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)

    @classmethod
    def retire(cls,jobs,active,control):
        for process in active:
            cls._stop(process,control)
        jobs.close()
        for process in active:
            for stream in (process.stdin,process.stdout,process.stderr):
                if stream is not None and not stream.closed:stream.close()
            process._handle.Close()

    def command(self,source,plan,pcm):
        route=plan.route
        command=[str(self.runtime.ffmpeg),'-nostdin','-hide_banner','-v','error','-xerror',
                 '-threads','2','-filter_threads','1','-protocol_whitelist','file']
        video_output=route.mode in (OutputMode.SAME_AS_SOURCE,OutputMode.COMPATIBLE_MP4)
        if video_output:
            command+=['-copyts','-noautorotate','-i',str(source.identity.path),'-itsoffset',format(float(plan.origin),'.17g')]
        command+=['-f','s16le','-ar','48000','-ac','1','-i',str(pcm)]
        if video_output:
            command+=['-map',f'0:{source.video.index}','-map','1:a:0']
            if route.mode==OutputMode.SAME_AS_SOURCE:command+=['-c:v','copy','-copytb','1']
            else:
                command+=['-c:v','libx264','-preset','medium','-crf','18','-pix_fmt','yuv420p',
                    '-threads:v','2','-refs','1','-bf','0','-maxrate','16M','-bufsize','32M',
                    '-fps_mode:v','passthrough','-enc_time_base:v','demux']
                for name,value in zip(('color_range','colorspace','color_trc','color_primaries'),source.video.color):
                    if value!='unknown':command+=['-'+name,value]
                # x264 can omit an explicitly limited-range source flag when
                # the other VUI color fields are unspecified. Preserve the
                # actual range signal without inventing a matrix/primaries.
                if source.video.color[0] in ('tv','pc'):
                    command+=['-bsf:v','h264_metadata=video_full_range_flag='+str(int(source.video.color[0]=='pc'))]
        else:command+=['-map','0:a:0','-vn']
        command+=['-map_metadata','-1','-map_chapters','-1','-sn','-dn','-c:a',route.audio_codec,'-ar','48000','-ac','1']
        if route.bitrate:command+=['-b:a',str(route.bitrate)]
        if route.container=='matroska' and route.audio_codec=='flac':
            command+=['-frame_size','4608'] # exact 96 ms blocks on Matroska's 1 ms clock
        command+=['-metadata:s:a:0','language=rus','-avoid_negative_ts','disabled']
        container={'matroska':'matroska','mp4':'mp4','mov':'mov','wav':'wav','mp3':'mp3'}[route.container]
        return command+['-f',container,'-fd','1','fd:']

    @staticmethod
    def metadata(process,command):
        payload=bytearray()
        with closing(process.lines(command,line_limit=1024*1024)) as records:
            for record in records:
                if len(payload)+len(record)>1024*1024:raise ExportError('validation_failed','Output metadata exceeds 1 MiB.')
                payload.extend(record)
        try:return json.loads(payload)
        except (ValueError,UnicodeError) as error:raise ExportError('validation_failed','Invalid output metadata.') from error

    @staticmethod
    def validate_streams(source,plan,payload):
        streams=payload.get('streams',[])
        video=[s for s in streams if s.get('codec_type')=='video']
        audio=[s for s in streams if s.get('codec_type')=='audio']
        copied=plan.route.mode==OutputMode.SAME_AS_SOURCE
        encoded=plan.route.mode==OutputMode.COMPATIBLE_MP4
        pictured=copied or encoded
        if len(streams)!=(2 if pictured else 1) or len(video)!=int(pictured) or len(audio)!=1:
            raise ExportError('validation_failed','Dub output stream inventory differs.')
        expected_codec='mp3' if plan.route.audio_codec=='libmp3lame' else plan.route.audio_codec
        if (audio[0].get('codec_name')!=expected_codec or audio[0].get('sample_rate')!='48000'
                or audio[0].get('channels')!=1):
            raise ExportError('validation_failed','Dub output audio encoding differs.')
        formats=set(payload.get('format',{}).get('format_name','').split(','))
        if ('matroska' if plan.route.container=='matroska' else 'mov' if pictured else plan.route.container) not in formats:
            raise ExportError('validation_failed','Output container differs from the selected route.')
        if copied:
            original=source.video;actual=video[0]
            for key,value in (('codec_name',original.codec),('profile',original.profile),('pix_fmt',original.pixel_format),
                              ('width',original.width),('height',original.height),('field_order',original.field_order)):
                if actual.get(key)!=value and not (key=='field_order' and value=='unknown' and actual.get(key)=='progressive'):
                    raise ExportError('validation_failed','Source picture properties changed: '+key)
            if actual.get('side_data_list') or ratio(actual.get('sample_aspect_ratio','1:1').replace(':','/'))!=original.sar:
                raise ExportError('validation_failed','Output picture side data/aspect changed.')
            colors=tuple(actual.get(k,'unknown') for k in ('color_range','color_space','color_transfer','color_primaries'))
            if colors!=original.color:raise ExportError('validation_failed','Picture color properties changed.')
        if encoded:
            original=source.video;actual=video[0]
            if (actual.get('codec_name')!='h264' or actual.get('pix_fmt')!='yuv420p'
                    or (actual.get('width'),actual.get('height'))!=(original.width,original.height)
                    or actual.get('side_data_list')
                    or ratio(actual.get('sample_aspect_ratio','1:1').replace(':','/'))!=original.sar):
                raise ExportError('validation_failed','Compatible MP4 picture geometry/codec/orientation differs.')
            colors=tuple(actual.get(k,'unknown') for k in ('color_range','color_space','color_transfer','color_primaries'))
            if colors!=original.color:raise ExportError('validation_failed','Compatible MP4 color signaling differs.')

    def packet_digest(self,process,path,index,time_base,owned=False):
        base=(OutputInspector(self.runtime,self.paths).command(path) if owned
              else FfprobeDubInspector(self.runtime,self.paths).command(path))
        command=base+['-select_streams',str(index),'-show_packets','-show_data_hash','sha256',
                      '-show_entries','packet=pts,dts,duration,size,flags,data_hash','-of','compact=p=1:nk=0',str(path)]
        result=hashlib.sha256();count=0
        with closing(process.lines(command)) as records:
            for raw in records:
                if not raw.strip():continue
                tokens=raw.decode('ascii').strip().split('|')
                fields=dict(t.split('=',1) for t in tokens[1:] if '=' in t)
                row=[str(integer(fields[k],minimum=-(2**63)+1)*time_base) for k in ('pts','dts','duration')]
                row += [fields['size'],fields['flags'],fields['data_hash']]
                if not fields['data_hash'].startswith('SHA256:'):raise ExportError('validation_failed','Missing encoded packet hash.')
                result.update(('|'.join(row)+'\n').encode());count+=1
                if count>100000000:raise ExportError('validation_failed','Packet ledger bound exceeded.')
        if not count:raise ExportError('validation_failed','No encoded picture packets.')
        return count,result.hexdigest()

    def picture_digest(self,process,path,index,pixel_format,owned=False):
        command=[str(self.runtime.ffmpeg),'-nostdin','-hide_banner','-v','error','-xerror','-threads','2']
        if owned:command+=['-protocol_whitelist','fd,file','-fd','0']
        else:command+=['-protocol_whitelist','file']
        command+=['-copyts','-i',str(path),'-map',f'0:{index}','-an','-sn','-dn','-c:v','rawvideo',
                  '-threads:v','1','-pix_fmt',pixel_format,'-fps_mode','passthrough','-enc_time_base:v','demux',
                  '-f','framehash','-hash','sha256','pipe:1']
        result=hashlib.sha256();count=0;time_base=None
        with closing(process.lines(command)) as records:
            for raw in records:
                if raw.startswith(b'#tb 0: '):time_base=ratio(raw[7:].decode('ascii').strip(),positive=True)
                if raw.startswith(b'#'):continue
                tokens=[v.strip() for v in raw.decode('ascii').split(',')]
                if len(tokens)!=6 or time_base is None or len(tokens[-1])!=64:
                    raise ExportError('validation_failed','Malformed independent picture digest.')
                tokens[1:4]=[str(integer(v,minimum=-(2**63)+1)*time_base) for v in tokens[1:4]]
                result.update((','.join(tokens)+'\n').encode());count+=1
                if count>100000000:raise ExportError('validation_failed','Picture digest bound exceeded.')
        if not count:raise ExportError('validation_failed','No independently decoded pictures.')
        return count,result.hexdigest()

    def audio_digest(self,process,index,maximum,composition,*,lossless):
        # Hash each decoded audio frame's bytes without buffering full PCM or
        # accepting a nonzero file as proof. framehash uses SHA256 by default.
        command=[str(self.runtime.ffmpeg),'-nostdin','-hide_banner','-v','error','-xerror','-threads','2',
                 '-protocol_whitelist','fd,file','-fd','0','-i','fd:','-map',f'0:{index}',
                 '-vn','-sn','-dn','-ac','1','-ar','48000','-c:a','pcm_s16le','-f','s16le','pipe:1']
        # ProbeProcess is a line protocol, so binary PCM gets its own bounded
        # owned pipe through the SAME factory/Job owner; no giant line buffer.
        import queue
        from threading import Event
        pending=queue.Queue(maxsize=8);stop=Event();child=None;errors=[];threads=[]
        sha=hashlib.sha256();count=0;reference=None
        sync=AudioSyncValidation(composition.protected_silence,composition.samples,lossless=lossless)
        try:
            reference=composition.path.open('rb')
            with process.control.starting():
                child=process.factory(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                      shell=False,cwd=process.paths.temporary,env=process.paths.child_environment({}))
            def read():
                try:
                    while not stop.is_set():
                        raw=child.stdout.read(32768)
                        while not stop.is_set():
                            try:pending.put(raw,timeout=.05);break
                            except queue.Full:pass
                        if not raw:break
                except OSError:errors.append(True)
            def error():
                try:
                    while raw:=child.stderr.read(4096):
                        if raw:errors.append(True)
                        if len(errors)>1:del errors[1:]
                except OSError:errors.append(True)
            threads=[Thread(target=read,daemon=True),Thread(target=error,daemon=True)]
            for thread in threads:thread.start()
            deadline=time.monotonic()+process.timeout
            while True:
                process.control.check()
                if time.monotonic()>deadline:raise ExportError('timeout','Independent audio decode timed out.')
                try:raw=pending.get(timeout=.05)
                except queue.Empty:
                    if errors:raise ExportError('validation_failed','Independent audio pipe failed.')
                    continue
                if not raw:break
                count+=len(raw)
                if count>maximum*2:raise ExportError('validation_failed','Decoded audio exceeds the qualified sample bound.')
                sha.update(raw)
                original=reference.read(len(raw))
                # Qualified AAC alone permits <=1024 decoder tail samples,
                # already bounded above. They correspond to zero padding.
                if len(original)<len(raw):original+=bytes(len(raw)-len(original))
                sync.consume(original,raw)
            child.wait(timeout=2)
            if child.returncode or errors or count%2:raise ExportError('validation_failed','Independent audio decode failed.')
            return dict(samples=count//2,sha256=sha.hexdigest(),sync=sync.receipt())
        finally:
            if reference is not None:reference.close()
            stop.set()
            if child is not None:
                self._stop(child,process.control)
                for thread in threads:thread.join(2)
                if any(t.is_alive() for t in threads):
                    raise ExportError('lifecycle_failed','Independent audio readers remain owned.',retained_resources=(child,*threads))
                child.stdout.close();child.stderr.close()
