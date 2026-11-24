"""Capture one active item and own one job under shared processing admission."""
from dataclasses import replace
import logging
import os
from pathlib import Path
from threading import RLock, Thread
import time

from mediaforge.application.contracts.export import ExportJob
from mediaforge.application.contracts.image_export import ImageExportRequest
from mediaforge.application.contracts.image_source import VideoSource
from mediaforge.application.contracts.source_timeline import SourceSignature
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.features.media.crop import CropRect
from mediaforge.features.media.export import (
    ExportControl, ExportError, ExportRequest, effective_format, output_aspect, ratio_text,
    require_audio_source_export, validate_video_encoder,
)
from mediaforge.features.media.reformat import GeometryError, dimensions, plan, sample_aspect, scaled_dimensions


def geometry(item):
    if item is None:
        raise ExportError("no_source", "Open a video first.")
    if not isinstance(item.metadata.source, VideoSource):
        raise ExportError('unsupported_source_kind', 'Image export is unavailable.')
    m, c, r = item.metadata, item.crop, item.resize
    crop = CropRect(c.x, c.y, c.width, c.height) if c.enabled else None
    if crop:
        crop.validate(m.width, m.height)
    w, h = (c.width, c.height) if crop else (m.width, m.height)
    reformat = None
    sar = sample_aspect(m.sample_aspect_ratio)
    if r.enabled:
        if r.error:
            raise GeometryError(r.error)
        target = scaled_dimensions(w, h, r.scale) if r.mode == "scale" else dimensions(r.width, r.height)
        sar = output_aspect(r.pixel_aspect, target.width, target.height, m.sample_aspect_ratio)
        reformat = plan(w, h, target, "stretch" if r.mode == "scale" else r.placement,
                        str(sample_aspect(m.sample_aspect_ratio) / sar))
        reformat.encoding_steps()
        w, h = target.width, target.height
    return w, h, sar, crop, reformat


def format_selection(item):
    if item is None:
        raise ExportError("no_source", "Open a video first.")
    s = item.output
    metadata = item.metadata
    if metadata.audio_only:
        require_audio_source_export(metadata.format_name, metadata.info_facts.audio_codec if metadata.info_facts else None,
                                    len(metadata.audio_streams))
        if s.container != 'audio' or s.kind != 'media':
            raise ExportError('unsupported_source_kind','Choose an audio output for this audio-only source.')
    if s.container == "audio":
        if not item.metadata.audio_streams:
            raise ExportError("no_audio", "This video has no audio stream to extract.")
        if not 0 <= s.audio_stream < len(item.metadata.audio_streams):
            raise ExportError("invalid_audio_stream", "Choose an available source audio stream.")
        if s.audio_format not in ("wav", "mp3"):
            raise ExportError("invalid_audio_format", "Choose WAV or MP3.")
        return "."+s.audio_format, "pcm_s16le" if s.audio_format == "wav" else "mp3"
    processing = item.trim.enabled or item.crop.enabled or item.resize.enabled
    if not processing and s.container == "source" and s.codec == "source":
        return Path(item.metadata.source_path).suffix.lower(), "copy"
    codec = item.metadata.video_codec if s.codec == "source" else s.codec
    result=effective_format(Path(item.metadata.source_path), s.container, codec, processing)
    quality=s.prores_quality if codec=='prores' else s.h264_quality
    validate_video_encoder(codec,quality,s.speed,s.h264_profile)
    return result


def summary(item):
    try:
        suffix, codec = format_selection(item)
        if codec in ("pcm_s16le", "mp3"):
            return {"valid":True,"audioOnly":True,"extension":suffix,"codec":codec,"actualCodec":codec,
                    "message":"Selected audio only; enabled Trim applies. Omit video, Crop/Reformat, subtitles, data and chapters. "
                              + ("PCM 16-bit; source sample rate/channels." if codec == "pcm_s16le" else "MP3 may convert sample rate to a supported rate; maximum two channels.")}
        w, h, sar, _, _ = geometry(item)
        from fractions import Fraction
        return {"valid": True, "width": w, "height": h, "sar": ratio_text(sar),
                "dar": ratio_text(Fraction(w, h) * sar), "extension": suffix, "codec": codec,
                "actualCodec": item.metadata.video_codec if codec == "copy" else codec,
                "qualityDescription": (f"CRF {item.output.h264_quality} · {item.output.speed.title()} ({dict(fast='veryfast',balanced='medium',quality='slow')[item.output.speed]}) · {item.output.h264_profile.title()} profile"
                    if codec == 'h264' else f"ProRes {['Proxy','422 LT','422 Standard','422 HQ','4444','4444 XQ'][item.output.prores_quality]}" if codec == 'prores' else ''),
                "message": ("Exact file copy: all source streams and metadata." if codec == "copy" else
                            "Encode first video + all audio (AAC); omit extra video, subtitles, data and chapters. "
                            + ("H.264: 8-bit 4:2:0; alpha omitted." if codec == "h264" else
                               "ProRes 4444/XQ: opaque YUV SDR only; 4:4:4 from 10-bit encoder input; alpha omitted. RGB/alpha sources are rejected. Does not restore source color depth or transparency."
                               if item.output.prores_quality >= 4 else "ProRes: 10-bit 4:2:2; alpha omitted."))}
    except ExportError as error:
        return {"valid": False, "message": str(error), "code": error.code}
    except (ValueError, RuntimeError, TypeError) as error:
        return {"valid": False, "message": str(error)}


def capture(item, *, replacement=None, protected_sources=(), allow_existing=False):
    suffix, codec = format_selection(item)
    audio_only = codec in ("pcm_s16le","mp3")
    if audio_only:
        w,h,sar,crop,reformat=item.metadata.width,item.metadata.height,sample_aspect(item.metadata.sample_aspect_ratio),None,None
    else:
        w, h, sar, crop, reformat = geometry(item)
    m, s = item.metadata, item.output
    source = Path(m.source_path)
    if not source.is_absolute() or not source.is_file():
        raise ExportError("source_unavailable", "Source is unavailable. Reopen the video.")
    folder = Path(s.folder)
    from mediaforge.application.contracts.export import output_filename
    name = output_filename(s.filename, suffix)
    if not folder.is_absolute() or not folder.is_dir():
        raise ExportError("output_unavailable", "Choose an existing absolute destination folder.")
    destination = folder / name
    sources = tuple(dict.fromkeys((source, *map(Path, protected_sources))))
    for protected in sources:
        if (os.path.normcase(str(protected.resolve())) == os.path.normcase(str(destination.resolve()))
                or (destination.exists() and protected.exists() and os.path.samefile(destination,protected))):
            raise ExportError("source_collision", "Loaded sources are never overwritten. Destination aliases a loaded source; choose a different filename.")
    if destination.exists() and not (allow_existing or replacement is not None):
        raise ExportError("destination_exists", "Destination exists. Choose a different filename or folder.")
    trim = (item.trim.in_ms, item.trim.out_ms) if item.trim.enabled else None
    if trim and (any(type(v) is not int for v in trim) or not 0 <= trim[0] < trim[1] <= item.duration_ms):
        raise ExportError("invalid_range", "Trim must satisfy 0 ≤ In < Out ≤ source duration.")
    if codec != "copy" and not audio_only:
        dimensions(w, h)
        if max(sar.numerator, sar.denominator) > 65535:
            raise ExportError("unsupported_sar", "Output SAR exceeds the supported exact signaling range (65535).")
    quality = s.prores_quality if codec == "prores" else s.h264_quality
    if type(quality) is not int or not 0 <= quality <= (5 if codec == "prores" else 51):
        raise ExportError("invalid_quality", "H.264 CRF must be 0..51; ProRes profile must be 0..5.")
    if s.speed not in ("fast","balanced","quality") or s.h264_profile not in ("auto","high","main"):
        raise ExportError("invalid_encoder_setting", "Choose a supported encoder speed and H.264 profile.")
    if s.audio_bitrate not in (128,192,256,320):
        raise ExportError("invalid_audio_bitrate", "MP3 bitrate must be 128, 192, 256 or 320 kb/s.")
    stat = source.stat()
    trim_pts_us = (None, None)
    pictures = (item.trim.in_picture, item.trim.out_picture)
    if trim and any(picture is not None for picture in pictures):
        signature = SourceSignature.capture(source)
        if any(picture is not None and picture.signature != signature for picture in pictures):
            raise ExportError('source_changed', 'Source changed since the Trim picture was selected. Set the Trim boundary again.')
        trim_pts_us = tuple(picture.pts_us if picture is not None else None for picture in pictures)
    return ExportRequest(source, destination, m.width, m.height, sample_aspect(m.sample_aspect_ratio),
                         (stat.st_size, stat.st_mtime_ns), codec, quality, w, h, sar,
                         trim, crop, reformat, codec == "copy", s.speed, s.h264_profile,
                         s.audio_stream, s.audio_bitrate, replacement, sources, trim_pts_us)


class ExportService:
    def __init__(self, executor, logger=None, *, gate=None):
        self._executor = executor
        self._processing_gate = gate if gate is not None else MediaProcessingGate()
        self._logger = logger or logging.getLogger("mediaforge")
        self._lock = RLock()
        self._thread = None
        self._control = None
        self._job = ExportJob()
        self._completed_result = None
        self._result_preparations=set()
        self._result_failures=[]
        self._generation = 0
        self._closed = False
        self._started_at = 0.

    @property
    def busy(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def snapshot(self):
        with self._lock:
            return replace(self._job, elapsed=time.monotonic()-self._started_at) if self.busy else self._job

    def prepare(self, item, protected_sources=(),snapshot=None, *, allow_replacement=False):
        from mediaforge.application.services.image_export import uses_image_export,capture as capture_images
        if uses_image_export(item):return capture_images(item,protected_sources,snapshot)
        request=capture(item,protected_sources=protected_sources,allow_existing=allow_replacement)
        if allow_replacement and request.destination.exists():
            info=request.destination.lstat()
            if not request.destination.is_file() or getattr(info,'st_file_attributes',0)&0x400:
                raise ExportError('unsafe_destination','Replacement requires a regular destination without reparse points.')
            request=replace(request,replacement=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns))
        return request

    def completed_result(self):
        """Only an unambiguous completed job may offer explicit result import."""
        with self._lock:
            if (self._closed or self.busy or self._processing_gate.busy
                    or self._job.state!='Completed' or not self._job.result
                    or self._completed_result is None or self._completed_result.identity is None
                    or self._completed_result.job_id!=self._job.job_id):return None
            return self._completed_result

    def prepare_result(self,result,cancel=None):
        from mediaforge.application.services.exported_result import ResultControl,ResultLease
        def current():
            with self._lock:return not self._closed and self._job.state=='Completed' and self._completed_result is result and self._job.job_id==result.job_id
        with self._lock:
            if not current() or result.identity is None:raise ExportError('result_stale','The completed export is no longer available.')
            owner=self._processing_gate.acquire()
            if owner is None:raise ExportError('media_processing_busy','Media processing is busy; the current Viewer is preserved.')
            control=ResultControl(current,cancel);lease=ResultLease(self,owner,control);self._result_preparations.add(lease)
        try:
            lease.native=self._executor.prepare_result(result,control)
            lease.check();return lease
        except BaseException as error:
            if getattr(error,'retained_resources',()):control.retain_resources(error.retained_resources)
            lease.close();raise
        finally:lease.ready.set()

    def cancel_result_preparation(self):
        with self._lock:
            for lease in tuple(self._result_preparations):lease.control.cancel()

    @staticmethod
    def _result_description(request,path,identity=None,job_id=0):
        from mediaforge.application.contracts.exported_result import ExportedResult
        from mediaforge.application.contracts.image_export import ImageExportRequest
        if isinstance(request,ImageExportRequest):
            if request.container not in ('png','jpeg','tiff','webp','mp4','mov'):return None
            kind='sequence' if request.mode=='sequence' else 'video' if request.mode=='media' else 'still'
            return ExportedResult(str(path),kind,request.width,request.height,request.container,request.numbering,request.rate,job_id,identity,
                                  viewer_supported=True)
        if Path(path).suffix.lower() in ('.mp4','.mov','.mkv') or getattr(request,'exact_copy',False):
            return ExportedResult(str(path),'video',request.width,request.height,Path(path).suffix[1:].lower(),job_id=job_id,identity=identity)
        if Path(path).suffix.lower() in ('.wav','.mp3'):
            return ExportedResult(str(path),'audio',0,0,Path(path).suffix[1:].lower(),job_id=job_id,identity=identity)
        return None

    def start_request(self, request):
        return self._start(lambda: request)

    def start(self, item):
        from mediaforge.application.services.image_export import uses_image_export,capture as capture_images
        if uses_image_export(item):return self._start(lambda:capture_images(item))
        return self._start(lambda: capture(item))

    def _start(self, build_request):
        with self._lock:
            if self.busy or self._closed:
                return False
            self._generation += 1
            owner = self._processing_gate.acquire()
            if owner is None:
                self._fail("media_processing_busy", "Media processing is busy or unavailable after a child cleanup failure.")
                return False
            handed = False
            try:
                request = build_request()
                self._control = ExportControl(self._stage, self._progress)
                self._started_at = time.monotonic()
                if isinstance(request, ImageExportRequest):
                    self._job = ExportJob(self._generation, "Running", "admission", "Checking image export resources…")
                else:
                    self._job = ExportJob(self._generation, "Running", "processing", "Export running… Progress is indeterminate.")
                self._completed_result = None
                self._thread = Thread(target=self._run, args=(request, self._control, owner), name="mediaforge-export")
                self._thread.start()
                handed = True
                return True
            except Exception as error:
                self._fail(getattr(error, "code", "invalid_settings"), str(error))
                return False
            finally:
                if not handed:
                    self._processing_gate.release(owner)

    def _fail(self, code, message):
        code = getattr(code, "value", code)
        self._job = ExportJob(self._generation, "Cancelled" if code == "cancelled" else "Failed", "done", message, error=code)

    def _stage(self, stage, message):
        with self._lock:
            if not self._closed and self._job.state == "Running":
                self._job = replace(self._job, stage=stage, message=message, progress=-1.)

    def _progress(self, fraction, speed, fps):
        with self._lock:
            if not self._closed and self._job.state == "Running":
                self._job = replace(self._job, progress=fraction, speed=speed, fps=fps)

    def _run(self, request, control, owner):
        try:
            try:
                result = self._executor.execute(request, control)
                control.check()
                with self._lock:
                    self._completed_result = self._result_description(request,result.output,getattr(result,'identity',None),self._job.job_id)
                    self._job = replace(self._job, state="Completed",stage="done",message="Export completed and validated.",
                                        result=str(result.output),progress=1.,elapsed=time.monotonic()-self._started_at)
            except Exception as error:
                self._logger.exception("Export failed")
                retained = getattr(error, 'retained_resources', ())
                if retained:
                    control.retain_resources(retained)
                code = getattr(error, "code", "process_failed")
                message = str(error)
                published_destination = control.published_destination
                if control.cleanup_failed and published_destination is not None:
                    code = "lifecycle_failed"
                    if getattr(error, "reported_destination", None) != published_destination:
                        message = (f"{message}\nOutput was published to {published_destination} before cleanup failed. "
                                   "Further processing is blocked until the retained resources can be safely retired.")
                if code != "lifecycle_failed":
                    try:
                        control.check()
                    except ExportError as cancelled:
                        code, message = cancelled.code, str(cancelled)
                with self._lock:
                    self._fail(code, message)
        finally:
            if control.cleanup_failed:
                self._processing_gate.poison(owner, "export_resource_cleanup_failed")
            else:
                self._processing_gate.release(owner)

    def cancel(self):
        with self._lock:
            if not self.busy or self._job.state != "Running" or not self._control.cancel():
                return False
            self._job = replace(self._job, state="Cancelling", message="Cancelling Export…")
            return True

    def shutdown(self):
        with self._lock:
            self._closed = True
            self.cancel_result_preparation()
            self.cancel()
            thread = self._thread
        if thread:
            thread.join(6.0)
            if thread.is_alive():
                raise RuntimeError("Export worker did not stop within shutdown deadline")
        if (self._control and self._control.cleanup_failed) or self.snapshot().error == "lifecycle_failed":
            raise RuntimeError("Export resource termination/cleanup failed; transaction/handles/staging and gate retained")
        for lease in tuple(self._result_preparations):
            if not lease.ready.wait(20):
                self._processing_gate.poison(lease.owner,'export_result_worker_unresolved')
                self._result_failures.append(lease)
                raise RuntimeError('Export result preparation worker did not stop; ownership retained.')
            lease.close()
        self._completed_result=None
        if self._result_failures:raise RuntimeError('Export result preparation resources remain unresolved.')
