"""One shared-child, destination-staged combined FFmpeg export."""
from decimal import Decimal
from fractions import Fraction
from math import lcm
import json
import logging
from pathlib import Path
import re
import subprocess
import time

from mediaforge.features.media.export import ExportError, ExportResult, ratio_text, require_audio_source_export
from mediaforge.features.media.reformat import sample_aspect
from mediaforge.infrastructure.ffmpeg.child_process import ChildProcesses
from mediaforge.infrastructure.ffmpeg.resize import FfmpegResizeExecutor, reformat_filters
from mediaforge.infrastructure.ffmpeg.video_duration import first_video_duration
from mediaforge.infrastructure.filesystem.export_output import ExportOutput


# Preset, rate-control lookahead, and reference frames in the managed x264.
_H264_PRESETS = {"fast": ("veryfast", 10, 1), "balanced": ("medium", 40, 3),
                 "quality": ("slow", 50, 5)}
_VIDEO_THREADS = 4


def _aligned_pixels(width, height):
    return ((width + 63) // 64) * 64 * ((height + 63) // 64) * 64


def _software_job_requirement(request):
    source = _aligned_pixels(request.source_width, request.source_height)
    output = _aligned_pixels(request.width, request.height)
    largest = _aligned_pixels(max(request.source_width, request.width),
                              max(request.source_height, request.height))
    raster_bytes = 4 * 64 * largest
    preset = _H264_PRESETS.get(request.speed) if request.codec == "h264" and not request.exact_copy else None
    if preset is not None:
        _, lookahead, references = preset
        # Charge queued input pictures including their low-res analysis planes,
        # reconstructed references including subpixel planes, and the separate
        # input-sized decoder/filter working set. Include frame and sync threads.
        queued = lookahead + 3 * _VIDEO_THREADS + 3
        reconstructed = references + _VIDEO_THREADS + 2
        raster_bytes = max(raster_bytes, 64 * source + (4 * queued + 6 * reconstructed) * output)
    return max(512 * 1024**2, 96 * 1024**2 + raster_bytes)


_MESSAGES = {
    "start_failed": "Cannot start the managed runtime. Check installation and permissions.",
    "process_failed": "FFmpeg failed. Check the application log for its diagnostic; verify source and destination space.",
    "timeout": "Export exceeded its processing deadline. Cancelled child cleanup was requested.",
    "lifecycle_failed": "Cannot prove process termination. Restart the application before processing again; staging is retained.",
    "validation_failed": "Output failed validation and was not published. Check the log.",
}


def _stream_start(stream):
    """Use integer stream ticks; FFprobe's decimal start_time is only a fallback."""
    try:
        if type(stream.get("start_pts")) is int:
            return stream["start_pts"] * Fraction(stream["time_base"])
        return Fraction(stream.get("start_time", "0"))
    except (ValueError, TypeError, KeyError, ZeroDivisionError) as error:
        raise ExportError("invalid_timing", "Source stream start time is unavailable or invalid.") from error


def _trim_source_bounds(request, stream):
    origin = _stream_start(stream)
    bounds = []
    for milliseconds, picture_us in zip(request.trim, request.trim_pts_us, strict=True):
        if picture_us is None:
            bounds.append(origin + Fraction(milliseconds, 1000))
            continue
        clock = Fraction(stream['time_base'])
        timestamp = Fraction(picture_us, 1_000_000)
        # The source index packs presentation timestamps in microseconds.
        # Recover the uniquely representable original stream tick.
        tick = round(timestamp / clock) * clock
        if clock <= Fraction(2, 1_000_000) or abs(timestamp - tick) > Fraction(1, 1_000_000):
            raise ExportError('invalid_timing', 'The selected Trim picture cannot be resolved on the source stream clock.')
        bounds.append(tick)
    if bounds[0] >= bounds[1]:
        raise ExportError('invalid_range', 'Select a nonempty Trim range on the source clock.')
    return tuple(bounds)


def _video_clock(request, video, media_start=None):
    try:
        source_tb = Fraction(video["time_base"])
        if source_tb <= 0:
            raise ValueError("Nonpositive source time base")
        origin = _stream_start(video)
        start, end = (_trim_source_bounds(request, video) if request.trim else
                      (origin if media_start is None else Fraction(media_start), None))
        # Represent source ticks and manual millisecond edits exactly.
        scale = lcm(source_tb.denominator, 1000, origin.denominator, start.denominator,
                    end.denominator if end is not None else 1)
        if scale > 2_147_483_647:
            raise ValueError("Exact clock exceeds the encoder/muxer integer range")
        return start, end, scale
    except (ValueError, TypeError, KeyError, ZeroDivisionError) as error:
        raise ExportError("invalid_timing", "Cannot represent the exact source/edit time base with this encoder and container.") from error


def _audio_span(request, video, streams):
    """Selected audio's own duration/intersection, never a video/container estimate."""
    try:
        selected = [s for s in streams if s.get('codec_type') == 'audio'][request.audio_stream]
        ticks = selected.get('duration_ts')
        clock = Fraction(selected.get('time_base', '0'))
        decimal = selected.get('duration')
        duration = ticks * clock if type(ticks) is int and clock > 0 else Fraction(decimal)
        if duration <= 0:
            raise ValueError('Nonpositive audio duration')
        if type(ticks) is int and clock > 0 and decimal not in (None, 'N/A'):
            if abs(duration - Fraction(decimal)) > max(clock, Fraction(1, 1_000_000)):
                raise ValueError('Conflicting selected audio duration metadata')
        if not request.trim:
            return duration
        if not ((type(selected.get('start_pts')) is int and clock > 0)
                or selected.get('start_time') not in (None, 'N/A')):
            raise ValueError('Selected audio start time is unavailable for Trim')
        audio_start = _stream_start(selected)
        start, end = _trim_source_bounds(request, video)
        intersection = min(end, audio_start + duration) - max(start, audio_start)
        if intersection <= 0:
            raise ExportError('empty_audio_range', 'Trim does not intersect the selected audio. Change the range or audio stream.')
        return intersection
    except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError) as error:
        raise ExportError('audio_timing_unavailable', 'Selected audio duration/timeline is missing or uncertain. '
                          'Use a source with reliable selected-stream timing; video/container duration is not substituted.') from error


class FfmpegExportExecutor:
    def __init__(self, runtime, *, process_factory=subprocess.Popen, logger=None,
                 timeout_seconds=3600, probe_timeout_seconds=15, image_service=None):
        self._runtime = runtime
        self._image_service = image_service
        self._logger = logger or logging.getLogger("mediaforge")
        self._timeout = timeout_seconds
        self._probe_timeout = probe_timeout_seconds
        self._processes = ChildProcesses(process_factory,
                                        lambda code: ExportError(code, _MESSAGES.get(code, "")), self._logger)

    def _probe_command(self, path):
        return [str(self._runtime.ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]

    def prepare_result(self,result,control):
        from mediaforge.infrastructure.filesystem.export_identity import LockedResult
        self._processes.check_reaped()
        budget=self._image_service.budget if self._image_service else None
        held=LockedResult(result,control,budget)
        try:
            held.prepare()
            if result.kind=='video':
                if self._runtime is None:raise ExportError('runtime_unavailable','Authenticated runtime is unavailable.')
                if self._image_service:self._image_service.admit_cpu_processing()
                text=self._processes.run([str(self._runtime.ffmpeg),'-nostdin','-hide_banner','-v','error','-xerror',
                    '-err_detect','explode','-threads','1','-i',result.path,'-map','0:v:0','-an','-sn','-dn',
                    '-frames:v','1','-threads:v','1','-progress','pipe:1','-nostats','-f','null','-'],
                    control,min(15,self._probe_timeout),validating=True)
                if not any(line.strip()=='frame=1' for line in text.splitlines()):
                    raise ExportError('result_not_ready','The completed video has no decodable first picture.')
            elif result.kind=='audio':
                if self._runtime is None:raise ExportError('runtime_unavailable','Authenticated runtime is unavailable.')
                self._probe(Path(result.path),control,allow_audio=True)
                text=self._processes.run([str(self._runtime.ffmpeg),'-nostdin','-hide_banner','-v','error','-xerror',
                    '-err_detect','explode','-threads','1','-i',result.path,'-map','0:a:0','-vn','-sn','-dn',
                    '-t','0.1','-progress','pipe:1','-nostats','-f','null','-'],control,min(15,self._probe_timeout),validating=True)
                if not any(line.startswith('out_time_us=') and line[12:].isdigit() and int(line[12:])>0 for line in text.splitlines()):
                    raise ExportError('result_not_ready','The completed audio has no decodable samples.')
            control.check();return held
        except BaseException:
            try:self._processes.check_reaped()
            except BaseException:
                control.retain_resources((held,self._processes));raise
            held.close();raise

    def _probe(self, path, control, output=None, processes=None, *, allow_audio=False):
        text = (processes or self._processes).run(self._probe_command(path), control, self._probe_timeout, output=output)
        try:
            if allow_audio:
                def reject(value):raise ValueError('Non-finite AV metadata: '+value)
                payload=json.loads(text,parse_constant=reject)
                if not isinstance(payload,dict) or not isinstance(payload.get('streams'),list):raise ValueError('Missing streams')
                streams=payload['streams']
                if any(not isinstance(s,dict) for s in streams):raise ValueError('Malformed stream')
                if not any(s.get('codec_type')=='video' for s in streams):
                    audio=[s for s in streams if s.get('codec_type')=='audio']
                    require_audio_source_export(payload.get('format', {}).get('format_name'),
                                                audio[0].get('codec_name') if audio else None, len(audio))
                    if payload['format']['format_name']=='wav' and audio[0].get('start_time') in (None,'N/A'):
                        # Qualified RIFF PCM has a zero sample origin, not an
                        # absent video/container timing estimate.
                        audio[0]['start_pts']=0;audio[0]['start_time']='0'
                    # Only a timing reference for the shared audio encoder. No
                    # raster/frame index is presented to the user.
                    reference=dict(audio[0],width=0,height=0,sample_aspect_ratio='1:1')
                    return payload,reference,1
            payload, video, audio_count = FfmpegResizeExecutor._payload(text)
            return payload, video, audio_count
        except (ValueError, TypeError, KeyError) as error:
            raise ExportError("validation_failed", "FFprobe returned invalid media metadata.") from error

    @staticmethod
    def _signature(request):
        stat = request.source.stat()
        if (stat.st_size, stat.st_mtime_ns) != request.source_signature:
            raise ExportError("source_changed", "Source changed after Export was requested. Reopen it.")

    def command(self, request, staged, video, audio_count, media_start=None):
        start, end, scale = _video_clock(request, video, media_start)
        prores_format = 'yuv444p10le' if request.quality >= 4 else 'yuv422p10le'
        filters = [f"settb=expr=1/{scale}"]
        audio = []
        if request.trim:
            filters.append(f"trim=start_pts={int(start * scale)}:end_pts={int(end * scale)}")
            # Audio is sampled on its own clock; retain the same rational origin.
            audio.append(f"atrim=start={Decimal(start.numerator)/Decimal(start.denominator)}:"
                         f"end={Decimal(end.numerator)/Decimal(end.denominator)}")
        filters.append(f"setpts=PTS-({int(start * scale)})")
        audio.append(f"asetpts=PTS-({start})/TB")
        if request.crop:
            c = request.crop
            filters.append(f"crop={c.width}:{c.height}:{c.x}:{c.y}:exact=1")
        if request.reformat:
            g = request.reformat
            # Identity spatial transform, including SAR-only changes, needs no resampling.
            if not (g.placement == "stretch" and (g.input_width, g.input_height) == (g.output.width, g.output.height)):
                filters.append(reformat_filters(g, prores_format, "yuv444p10le") if request.codec == "prores"
                               else reformat_filters(g))
        filters.append(f"setsar={request.sar.numerator}/{request.sar.denominator}:max=65535")
        args = [str(self._runtime.ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-n",
                "-copyts", "-noautorotate", "-i", str(request.source), "-map", "0:v:0", "-map", "0:a?",
                "-vf", ",".join(filters)]
        if request.codec == "h264":
            args += ["-c:v", "libx264", "-preset", _H264_PRESETS[request.speed][0],
                     "-crf", str(request.quality), "-pix_fmt", "yuv420p"]
            if request.h264_profile != "auto":
                args += ["-profile:v", request.h264_profile]
        else:
            args += ["-c:v", "prores_ks", "-profile:v", str(request.quality), "-pix_fmt", prores_format]
            if request.quality >= 4:
                args += ['-alpha_bits', '0']  # TASK-010 deliberately exports opaque video only.
        if audio_count:
            args += ["-af", ",".join(audio), "-c:a", "aac", "-b:a", "192k"]
        for tag in ("color_range", "color_space", "color_transfer", "color_primaries"):
            value = video.get(tag)
            if value and value not in ("unknown", "unspecified"):
                args += ["-" + ("colorspace" if tag == "color_space" else "color_trc" if tag == "color_transfer" else tag), value]
        args += ["-enc_time_base:v:0", "filter", "-fps_mode:v:0", "passthrough"]
        if request.destination.suffix.lower() in (".mp4", ".mov"):
            # Processed output maps only video/audio. MOV/MP4 otherwise auto-
            # synthesizes an unmapped tmcd data track from inherited tags, which
            # correctly trips strict stream validation on ordinary ProRes media.
            args += ["-video_track_timescale", str(scale), "-write_tmcd", "0",
                     "-metadata", "timecode=", "-metadata:s:v:0", "timecode="]
        return args + ["-map_chapters", "-1", str(staged)]

    def audio_command(self, request, staged, video, streams):
        audio = [s for s in streams if s.get("codec_type") == "audio"]
        if not 0 <= request.audio_stream < len(audio):
            raise ExportError("no_audio", "Selected audio stream is unavailable. Reopen and choose an available stream.")
        selected = audio[request.audio_stream]
        if request.codec == "mp3" and int(selected.get('channels',0)) not in (1,2):
            raise ExportError("unsupported_audio", "MP3 supports mono/stereo here. Choose WAV to retain these channels.")
        origin = _stream_start(video)
        start, end = (_trim_source_bounds(request, video) if request.trim else
                      (min(origin, _stream_start(selected)), None))
        filters = []
        if request.trim:
            filters += [f"atrim=start={Decimal(start.numerator)/Decimal(start.denominator)}:end={Decimal(end.numerator)/Decimal(end.denominator)}"]
        filters += [f"asetpts=PTS-({start})/TB"]
        args = [str(self._runtime.ffmpeg),'-nostdin','-hide_banner','-v','error','-n','-copyts','-i',str(request.source),
                '-map',f'0:a:{request.audio_stream}','-vn','-af',','.join(filters),'-c:a',
                'libmp3lame' if request.codec == 'mp3' else 'pcm_s16le']
        if request.codec == 'mp3': args += ['-b:a',f'{request.audio_bitrate}k']
        return args + ['-map_metadata','-1','-map_chapters','-1',str(staged)]

    @staticmethod
    def _progress(control, duration):
        started=time.monotonic()
        latest=Fraction(0)
        def update(text):
            nonlocal latest
            if isinstance(text,bytes): text=text.decode('utf-8','replace')
            for line in text.splitlines():
                if not line.startswith('mediaforge_pts='): continue
                try:
                    pts,tb,count=line.split('=',1)[1].split('|')
                    timestamp=int(pts)*Fraction(tb)
                    if abs(timestamp)>86_400: continue
                    latest=max(latest,timestamp)
                    elapsed=max(.001,time.monotonic()-started)
                    fraction=max(0.,min(.99,float(latest/Fraction(str(duration))))) if duration>0 else -1.
                    control.progress(fraction,f'{latest/elapsed:.2f}x',f'{(int(count)+1)/elapsed:.1f}')
                except (ValueError,ZeroDivisionError): continue
        return update

    def execute(self, request, control):
        from mediaforge.application.contracts.image_export import ImageExportRequest
        if isinstance(request,ImageExportRequest):
            from mediaforge.infrastructure.ffmpeg.image_export import execute
            return execute(self,request,control)
        if not self._image_service:
            return self._execute_av(request, control, self._processes)

        from mediaforge.infrastructure.ffmpeg.image_export import host_job_capacity
        from mediaforge.infrastructure.ffmpeg.preview_job import PreviewJobs
        requirement = _software_job_requirement(request)

        def admit():
            sample = self._image_service.admit_cpu_processing()
            if host_job_capacity(self._image_service, sample, 0) < requirement:
                raise ExportError('insufficient_memory', 'Insufficient host RAM/commit for this software export.')
            return requirement

        control.check()
        admit()  # Also covers Exact Copy, which uses a parent-side file loop.
        jobs = PreviewJobs(requirement, admission=admit)
        processes = ChildProcesses(jobs, lambda code: ExportError(code, _MESSAGES.get(code, '')), self._logger)
        from mediaforge.infrastructure.filesystem.image_output import ReadLocks
        sources = ReadLocks()
        try:
            sources.parents(request.source.parent)
            sources.add(request.source)
            return self._execute_av(request, control, processes)
        finally:
            try:
                processes.check_reaped()
                jobs.close()
                sources.close()
                self._logger.info('AV export software worker limit=%d peak=%d children=%d published=%s',
                                  requirement, jobs.peak, len(jobs.processes), control.published)
            except BaseException:
                control.retain_resources((jobs, processes, sources))
                raise

    def _execute_av(self, request, control, processes):
        control.check()
        if not request.exact_copy and request.codec not in ('pcm_s16le','mp3'):
            from mediaforge.features.media.export import validate_video_encoder
            validate_video_encoder(request.codec,request.quality,request.speed,request.h264_profile)
        control.report("preflight", "Inspecting source and encoder capabilities… Progress is indeterminate.")
        processes.check_reaped()
        if self._runtime is None:
            raise ExportError("runtime_unavailable", "Managed FFmpeg/FFprobe is unavailable. Verify installation.")
        self._signature(request)
        audio_only = request.codec in ('pcm_s16le','mp3')
        if processes is self._processes:
            payload, video, audio_count = self._probe(request.source, control, allow_audio=True) if audio_only else self._probe(request.source, control)
        else:
            payload, video, audio_count = self._probe(request.source, control, processes=processes, allow_audio=audio_only)
        if (video["width"], video["height"]) != (request.source_width, request.source_height) or sample_aspect(video.get("sample_aspect_ratio")) != request.source_sar:
            raise ExportError("source_changed", "Source geometry changed. Reopen the media.")
        audio_expected = _audio_span(request, video, payload['streams']) if audio_only else None
        if not request.exact_copy and not audio_only:
            try:
                FfmpegResizeExecutor._orientation(video)
            except RuntimeError as error:
                raise ExportError("unsupported_orientation", "Encoding requires an unrotated/untransformed source.") from error
            if video.get("field_order", "progressive") not in ("progressive", "unknown"):
                raise ExportError("unsupported_scan", "Interlaced encoding is not supported by this export preset.")
            if video.get("color_transfer") in ("smpte2084", "arib-std-b67"):
                raise ExportError("unsupported_hdr", "HDR encoding is not supported by these SDR presets. Use exact Same-as-Source copy.")
            aliases = set(payload.get("format", {}).get("format_name", "").split(","))
            if not aliases.intersection({"mp4", "mov", "matroska"}):
                raise ExportError("unsupported_container", "Encoding accepts MP4/MOV/MKV input only.")
            if request.codec == 'prores' and request.quality >= 4:
                pixels = video.get('pix_fmt', '')
                if not pixels.startswith('yuv') or pixels.startswith('yuva') or video.get('color_space') == 'gbr':
                    raise ExportError('unsupported_color', 'ProRes 4444/XQ currently requires opaque YUV SDR video. '
                                      'RGB/alpha input and alpha preservation are unsupported; choose an opaque YUV source.')
        if not request.exact_copy:
            encoder = {"h264":"libx264","prores":"prores_ks","pcm_s16le":"pcm_s16le","mp3":"libmp3lame"}[request.codec]
            inventory = processes.run([str(self._runtime.ffmpeg), "-hide_banner", "-encoders"], control, self._probe_timeout)
            if not any(re.fullmatch(r"\s*[VA][A-Z.]{5}\s+" + encoder + r"(?:\s+.*)?", line) for line in inventory.splitlines()):
                raise ExportError("encoder_unavailable", f"Managed runtime does not provide {encoder}. No alternative was substituted.")
        with ExportOutput(request.source, request.destination, self._logger, replacement=request.replacement,
                          protected_sources=request.protected_sources) as output:
            if request.exact_copy:
                control.report("copy", "Copying the original file… Progress is indeterminate.")
                # Bounded chunks permit cancellation of a large exact copy without a child.
                with request.source.open("rb") as src, output.staging.descriptor() as dst:
                    while True:
                        control.check()
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
            else:
                control.report("encode", "Encoding enabled operations… Progress is indeterminate.")
                starts = [_stream_start(video)] + [
                    _stream_start(s) for s in payload["streams"] if s.get("codec_type") == "audio"]
                command = (self.audio_command(request, output.staged, video, payload['streams']) if audio_only else
                           self.command(request, output.staged, video, audio_count, min(starts)))
                if processes is not self._processes:
                    command[1:1] = ['-filter_threads', '2']
                    command[command.index('-i'):command.index('-i')] = ['-threads', str(_VIDEO_THREADS)]
                    command[-1:-1] = ['-threads:v', str(_VIDEO_THREADS)] if not audio_only else ['-threads:a', '1']
                spec='a:0' if audio_only else 'v:0'
                command[-1:-1]=['-stats_enc_post:'+spec,'pipe:1','-stats_enc_post_fmt:'+spec,'mediaforge_pts={pts}|{tb}|{n}']
                duration = float(audio_expected) if audio_only else ((request.trim[1]-request.trim[0])/1000 if request.trim else first_video_duration(video))
                # FFmpeg uses the seekable owned object via fd 0. -nostdin
                # keeps it from interpreting that descriptor as keyboard input,
                # and the existing progress channel stays on stdout.
                suffix = request.destination.suffix.lower()[1:]
                command[-1:] = ['-f', 'matroska' if suffix == 'mkv' else suffix, '-fd', '0', 'fd:']
                with output.staging.descriptor() as partial:
                    processes.run(command, control, self._timeout, output=output,
                                  on_output=self._progress(control,duration), stdin=partial)
            output.staging.finish_writing()
            control.check()
            control.report("validation", "Validating staged output before publication… Progress is indeterminate.")
            from mediaforge.infrastructure.filesystem.image_output import ReadLocks
            from mediaforge.infrastructure.filesystem.export_identity import capture_output
            verified=ReadLocks()
            output._result_validation_locks=(verified,)
            with verified:
                output.staging.check_partial(verified.add(output.staged))
                if request.exact_copy and output.staged.stat().st_size != request.source_signature[0]:
                    raise ExportError("source_changed", "Source size changed while copying.")
                if audio_only:
                    text=processes.run(self._probe_command(output.staged),control,self._probe_timeout,output=output)
                    self._validate_audio(request,payload,json.loads(text))
                else:
                    staged_payload, staged_video, staged_audio = (
                        self._probe(output.staged, control, output) if processes is self._processes
                        else self._probe(output.staged, control, output, processes=processes))
                    output_clock = None if request.exact_copy else Fraction(1, _video_clock(request, video, min(starts))[2])
                    self._validate(request, video, audio_count, staged_payload, staged_video, staged_audio, output_clock)
                self._signature(request)
                identity=capture_output([output.staged],[request.destination.name],request.destination,control,
                    self._image_service.budget if self._image_service else None)
                # DELETE ownership cannot be acquired under the validation share
                # mode. Treat that gap as untrusted; revalidate the frozen receipt
                # THROUGH the newly acquired, write/delete-denying file handle.
                # The same handle, not a staging path, then performs the rename.
                verified.close()
                output.bind_validated(identity,control,self._image_service.budget if self._image_service else None)
                control.check();control.report('publication','Publishing validated output…')
                target=output.publish(control)
                return ExportResult(target,identity)

    @staticmethod
    def _validate_audio(request, source, payload):
        try:
            streams=payload['streams']
            selected=[s for s in source['streams'] if s.get('codec_type')=='audio'][request.audio_stream]
            if len(streams)!=1 or streams[0].get('codec_type')!='audio' or streams[0].get('codec_name')!=request.codec:
                raise ValueError('Output must contain exactly the selected audio and no video')
            actual=streams[0]
            if type(actual.get('channels')) is not int or actual['channels'] <= 0 or actual['channels'] != selected.get('channels'):
                raise ValueError('Audio channels changed')
            rate, source_rate = int(actual['sample_rate']), int(selected['sample_rate'])
            if rate <= 0 or source_rate <= 0:
                raise ValueError('Invalid audio sample rate')
            if request.codec=='pcm_s16le' and rate != source_rate:
                raise ValueError('WAV sample rate changed')
            video = next((s for s in source['streams'] if s.get('codec_type') == 'video'),None)
            if video is None:video=[s for s in source['streams'] if s.get('codec_type')=='audio'][request.audio_stream]
            expected = _audio_span(request, video, source['streams'])
            duration = Fraction(payload['format']['duration'])
            if duration <= 0 or abs(duration - expected) > Fraction(1, 4):
                raise ValueError('Audio duration differs from requested interval')
            if request.destination.suffix.lower().lstrip('.') not in payload['format']['format_name'].split(','):
                raise ValueError('Wrong audio container')
            # A successfully published output must satisfy the same explicit
            # import profile as a fresh file; metadata recognition alone in
            # a different output-only path is insufficient.
            from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe
            from mediaforge.features.media.metadata import MediaProbeError
            try:reopened=FfprobeMediaMetadataProbe._parse(payload,request.destination)
            except MediaProbeError as error:raise ValueError('Output does not satisfy native audio import qualification') from error
            if not reopened.audio_only:raise ValueError('Output is not the qualified audio-only class')
        except (ValueError,KeyError,IndexError,TypeError) as error:
            raise ExportError('validation_failed',f'Audio output was not published: {error}.') from error

    @staticmethod
    def _validate(request, source_video, audio_count, payload, video, actual_audio, output_clock=None):
        try:
            if (video["width"], video["height"]) != (request.width, request.height):
                raise ValueError("Coded dimensions do not match the captured plan")
            if sample_aspect(video.get("sample_aspect_ratio")) != request.sar or actual_audio != audio_count:
                raise ValueError("SAR or audio stream count changed")
            if video["codec_name"] != (source_video["codec_name"] if request.exact_copy else request.codec):
                raise ValueError("Unexpected output codec")
            aliases = set(payload.get("format", {}).get("format_name", "").split(","))
            expected = {".mp4": "mp4", ".mov": "mov", ".mkv": "matroska"}.get(request.destination.suffix.lower())
            if expected and expected not in aliases:
                raise ValueError("Unexpected container")
            if not request.exact_copy:
                if request.codec == 'prores' and request.quality >= 4:
                    expected_profile, expected_tag = ('4444', 'ap4h') if request.quality == 4 else ('XQ', 'ap4x')
                    if (video.get('profile') != expected_profile or video.get('codec_tag_string') != expected_tag
                            or video.get('pix_fmt') != 'yuv444p12le' or video.get('bits_per_raw_sample') != '12'):
                        raise ValueError('ProRes 4444/XQ profile or opaque 4:4:4 depth signaling differs from the selected preset')
                # Refuse a muxer clock that cannot represent the filter's exact ticks.
                mux_clock = Fraction(video["time_base"])
                if mux_clock <= 0 or (output_clock / mux_clock).denominator != 1:
                    raise ValueError("Container time base cannot preserve the exact filtered video clock")
                if any(s.get("codec_name") != "aac" for s in payload["streams"] if s.get("codec_type") == "audio"):
                    raise ValueError("Unexpected audio encoder")
                if len(payload["streams"]) != 1 + actual_audio:
                    raise ValueError("Unexpected extra streams")
            duration = first_video_duration(video)
            if request.trim:
                start, end = _trim_source_bounds(request, source_video)
                expected_duration = float(end - start)
            else:
                expected_duration = first_video_duration(source_video)
            if duration <= 0 or abs(duration - expected_duration) > 0.250:
                raise ValueError("Output duration outside tolerance")
            if video.get("display_aspect_ratio") not in (None, "N/A", ratio_text(request.dar)):
                raise ValueError("Unexpected display aspect ratio")
        except (ValueError, TypeError, KeyError, RuntimeError) as error:
            raise ExportError("validation_failed", f"Output was not published: {error}.") from error
