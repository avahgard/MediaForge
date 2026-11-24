"""Protected, streaming Dub inspection using the sole injected FFmpeg runtime.

C1 establishes decoded source clocks and copy-route candidates, never mux/output
qualification. FFprobe decodes the complete selected streams, retaining only
aggregate rational clocks and an interval digest (no giant frame JSON/index).
"""

from contextlib import closing
from collections import OrderedDict
from dataclasses import replace
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

from mediaforge.features.dub.contracts import (
    AudioTrack, ClockFacts, DubError, DubSourceDescriptor, Reason, Refusal,
    SourceKind, VideoFacts, PacketClock,
)
from mediaforge.infrastructure.filesystem.dub_source import ProtectedDubSource
from .dub_process import ProbeProcess
from .preview_job import PreviewJobs
from .runtime import FfmpegManifest


def ratio(value, *, positive=False):
    if not isinstance(value, (str, int)) or isinstance(value, bool) or len(str(value)) > 64:
        raise ValueError('Invalid rational field.')
    if not re.fullmatch(r'-?\d{1,19}(?:/\d{1,19}|\.\d{1,19})?', str(value)):
        raise ValueError('Rational field must use bounded decimal/integer-ratio notation.')
    result = Fraction(value)
    if max(abs(result.numerator), result.denominator) > 2**63 - 1 or (positive and result <= 0):
        raise ValueError('Rational field exceeds bounds.')
    return result


def integer(value, minimum=0, maximum=2**63-1):
    if isinstance(value, bool) or len(str(value)) > 32 or str(int(value)) != str(value):
        raise ValueError('Invalid integer field.')
    result = int(value)
    if not minimum <= result <= maximum:
        raise ValueError('Integer field exceeds bounds.')
    return result


def optional_clock(stream, tick, decimal, time_base):
    # Integer ticks are authoritative. Decimal display values never override them.
    if stream.get(tick) not in (None, 'N/A'):
        return integer(stream[tick], minimum=-(2**63)+1) * time_base
    if stream.get(decimal) not in (None, 'N/A'):
        return ratio(stream[decimal])
    return None


class _DecodedTimingError(ValueError):
    """A specific decoded timing failure safe to explain to the user."""


class FrameClock:
    """Strict decoded presentation order; audio priming uses actual decoded samples."""
    def __init__(self, time_base, sample_rate=None):
        self.time_base, self.rate = time_base, sample_rate
        self.first = self.end = None
        self.previous = None
        self.count = self.samples = 0
        self.digest = hashlib.sha256()

    def add(self, raw):
        fields = {}
        tokens = raw.decode('ascii', errors='strict').rstrip('\r\n').split('|')
        if tokens[0] != 'frame':
            raise ValueError('Unexpected clock record.')
        for token in tokens[1:]:
            if not token:
                continue
            key, value = token.split('=', 1)
            if key in fields:
                raise ValueError('Duplicate clock field.')
            fields[key] = value
        start = integer(fields['best_effort_timestamp'], minimum=-(2**63)+1) * self.time_base
        if 'pts' not in fields or integer(fields['pts'], minimum=-(2**63)+1) * self.time_base != start:
            raise _DecodedTimingError('its decoded presentation timestamps are missing or inconsistent')
        if self.rate:
            samples = integer(fields['nb_samples'], 1, 1024*1024)
            duration = Fraction(samples, self.rate)
            self.samples += samples
        else:
            duration = integer(fields['duration'], 1) * self.time_base
        if self.previous is not None:
            if start <= self.previous:
                raise _DecodedTimingError('its decoded timestamps repeat or go backwards')
            if start < self.end:
                raise _DecodedTimingError('its decoded timestamps overlap')
        if self.first is None:
            self.first = start
        self.end = start + duration
        self.previous = start
        self.count += 1
        if self.count > 100_000_000:
            raise ValueError('Clock record count exceeds the bounded inspection envelope.')
        self.digest.update(f'{start-self.first}|{duration}\n'.encode('ascii'))

    def finish(self):
        if not self.count:
            if self.rate:
                raise DubError(Reason.EMPTY_AUDIO, "Dub can't use this audio track because it has no decoded audio samples.")
            raise DubError(Reason.INVALID_TIMING, "Dub can't use this video because it has no decoded pictures.")
        return ClockFacts(self.time_base, self.first, self.end, self.count,
                          self.digest.hexdigest(), self.samples if self.rate else None)


class FfprobeDubInspector:
    def __init__(self, runtime, paths, *, process_factory=None, timeout=300, qualified_mux_metadata=False):
        self.runtime, self.paths = runtime, paths
        self.factory, self.timeout = process_factory, timeout
        self.last_job_peak = 0
        self.qualified_mux_metadata=qualified_mux_metadata
        self._qualified_sources = OrderedDict()

    def command(self, source):
        return [str(self.runtime.ffprobe), '-v', 'error', '-threads', '2',
                '-protocol_whitelist', 'file', '-format_whitelist',
                'mov,matroska,webm,avi,wav,mp3,flac,ogg,aac',
                '-enable_drefs', '0', '-use_absolute_path', '0',
                '-probesize', '8388608', '-analyzeduration', '10000000']

    def inspect(self, source: Path, control):
        # Session-only immutable facts. A hit still holds deny-write/delete
        # source/ancestor handles and hashes every byte before AND after reuse.
        # Start independently reacquires/authenticates the source as before.
        key = (str(source), id(self.runtime), self.qualified_mux_metadata)
        cached = self._qualified_sources.get(key)
        if cached is not None:
            with ProtectedDubSource(source, control) as protected:
                if protected.identity == cached.identity:
                    self._qualified_sources.move_to_end(key)
                    return cached
            del self._qualified_sources[key]
        descriptor = self._inspect(source, control)
        self._qualified_sources[key] = descriptor
        self._qualified_sources.move_to_end(key)
        while len(self._qualified_sources) > 8:
            self._qualified_sources.popitem(last=False)
        return descriptor

    def _inspect(self, source: Path, control):
        if self.runtime is None:
            raise DubError(Reason.RUNTIME_MISSING, 'Authenticated managed FFprobe is required.')
        self.paths.prepare()
        jobs = None
        try:
            # Same suspended-before-assignment Windows factory as Media's FFmpeg
            # preview children; no ML worker or second runtime is introduced.
            jobs = PreviewJobs(512 * 1024**2) if self.factory is None else None
            process = ProbeProcess(self.factory or jobs, self.paths, control, self.timeout)
            with ProtectedDubSource(source, control) as protected:
                command = self.command(protected.path) + ['-show_streams', '-show_format',
                    '-show_chapters', '-of', 'json', str(protected.path)]
                data = bytearray()
                with closing(process.lines(command, line_limit=1024*1024)) as records:
                    for record in records:
                        if len(data) + len(record) > 1024*1024:
                            raise DubError(Reason.PROBE_FAILED, 'Metadata exceeds 1 MiB.')
                        data.extend(record)
                def reject(value):
                    raise ValueError('Nonfinite metadata.')
                payload = json.loads(data, parse_constant=reject)
                descriptor = parse_metadata(payload, protected.identity, qualified_mux_metadata=self.qualified_mux_metadata)
                manifest = FfmpegManifest.load()
                descriptor = replace(descriptor, runtime_id=':'.join((manifest.runtime_id,
                    manifest.version, manifest.ffmpeg_sha256, manifest.ffprobe_sha256)))
                tracks = []
                for track in descriptor.audio_tracks:
                    if track.refusal:
                        tracks.append(track)
                        continue
                    try:
                        clock = self.scan(process, protected.path, track.index, track.time_base, track.sample_rate)
                        tracks.append(replace(track, clock=clock))
                    except DubError as error:
                        if error.code not in (Reason.INVALID_TIMING, Reason.EMPTY_AUDIO, Reason.PROBE_FAILED):
                            raise
                        tracks.append(replace(track, refusal=Refusal(error.code, str(error))))
                video = descriptor.video
                if video:
                    try:
                        video = replace(video, clock=self.scan(process, protected.path, video.index, video.time_base))
                    except DubError as error:
                        if error.code not in (Reason.INVALID_TIMING, Reason.PROBE_FAILED):
                            raise
                        video = replace(video, refusal=Refusal(error.code, str(error)))
                    if video.clock is not None:
                        try:
                            video = replace(video, packet_clock=self.packets(process, protected.path, video.index, video.time_base))
                        except DubError as error:
                            if error.code not in (Reason.INVALID_TIMING, Reason.PROBE_FAILED):
                                raise
                            # Packet-copy qualification is independent from the
                            # presented picture span used for audio-only clipping.
                            descriptor = replace(descriptor, copy_refusal=descriptor.copy_refusal or Refusal(error.code, str(error)))
                return replace(descriptor, audio_tracks=tuple(tracks), video=video)
        except (ValueError, TypeError, KeyError, ZeroDivisionError, UnicodeError) as error:
            raise DubError(Reason.INVALID_SOURCE, 'Malformed or unsupported source metadata.') from error
        finally:
            if jobs:
                try:
                    jobs.close()
                    self.last_job_peak = jobs.peak
                except (OSError, RuntimeError) as error:
                    control.retain(jobs)
                    raise DubError(Reason.LIFECYCLE_FAILED, 'Probe Job ownership is unresolved.',
                                   retained_resources=(jobs,)) from error

    def scan(self, process, path, index, time_base, rate=None):
        clock = FrameClock(time_base, rate)
        stream = 'audio track' if rate else 'video'
        command = self.command(path) + ['-select_streams', str(index), '-show_frames',
            '-show_entries', 'frame=pts,best_effort_timestamp,duration,nb_samples:frame_side_data=',
            '-of', 'compact=p=1:nk=0', str(path)]
        try:
            with closing(process.lines(command)) as records:
                for record in records:
                    if record.strip():
                        clock.add(record)
            return clock.finish()
        except _DecodedTimingError as error:
            raise DubError(Reason.INVALID_TIMING,
                           f"Dub can't use this {stream} because {error}. The source can still play in Media.") from error
        except (ValueError, KeyError, UnicodeError, ZeroDivisionError) as error:
            raise DubError(Reason.INVALID_TIMING,
                           f"Dub couldn't verify the decoded timestamps for this {stream}.") from error

    def packets(self, process, path, index, time_base):
        first = last = None
        count = 0
        digest = hashlib.sha256()
        command = self.command(path) + ['-select_streams', str(index), '-show_packets',
            '-show_entries', 'packet=pts,dts,duration,size,flags:packet_side_data=',
            '-of', 'compact=p=1:nk=0', str(path)]
        try:
            with closing(process.lines(command)) as records:
                for raw in records:
                    if not raw.strip():
                        continue
                    tokens = raw.decode('ascii').rstrip('\r\n').split('|')
                    if tokens[0] != 'packet':
                        raise ValueError('Unexpected packet record.')
                    fields = dict(token.split('=',1) for token in tokens[1:] if token)
                    pts = integer(fields['pts'], minimum=-(2**63)+1)
                    dts = integer(fields['dts'], minimum=-(2**63)+1) * time_base
                    duration = integer(fields['duration'],1)
                    size = integer(fields['size'],1)
                    if last is not None and dts <= last:
                        raise ValueError('Encoded decoding order is ambiguous.')
                    if first is None:
                        first = dts
                    last = dts
                    count += 1
                    if count > 100_000_000:
                        raise ValueError('Packet count exceeds bound.')
                    digest.update(f'{pts*time_base}|{dts}|{duration*time_base}|{size}|{fields["flags"]}\n'.encode('ascii'))
            if not count:
                raise ValueError('No encoded video packets.')
            return PacketClock(time_base,count,first,last,digest.hexdigest())
        except (ValueError, KeyError, UnicodeError) as error:
            raise DubError(Reason.INVALID_TIMING, 'Encoded video packet clock is unqualified; audio-only may remain eligible.') from error


def parse_metadata(payload, identity, *, qualified_mux_metadata=False):
    streams, container = payload['streams'], payload['format']
    if not isinstance(streams, list) or not 1 <= len(streams) <= 32 or not isinstance(container, dict):
        raise ValueError('Invalid stream inventory.')
    indices = [integer(s['index'], 0, 1024) for s in streams]
    if len(set(indices)) != len(indices):
        raise ValueError('Duplicate absolute stream indices.')
    formats = set(container['format_name'].split(','))
    extension = identity.path.suffix
    if 'mov' in formats:
        brand = container.get('tags', {}).get('major_brand', '').strip()
        kind = 'mov' if brand == 'qt' else 'mp4'
    elif 'matroska' in formats:
        kind = 'matroska'
    else:
        kind = container['format_name']
    video = [s for s in streams if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
    if len(video) > 1:
        raise DubError(Reason.UNSUPPORTED_SOURCE, 'Multiple main video streams are unsupported.')
    audio = [s for s in streams if s.get('codec_type') == 'audio']
    if len(audio) > 8:
        raise DubError(Reason.UNSUPPORTED_SOURCE, 'More than eight audio candidates are unsupported.')
    copy_refusal = None
    allowed_suffixes = {'mp4': {'.mp4'}, 'mov': {'.mov'}, 'matroska': {'.mkv'}}
    if extension.lower() not in allowed_suffixes.get(kind, set()):
        copy_refusal = Refusal(Reason.UNQUALIFIED_COPY, 'Source suffix/container combination is unqualified for preservation.')
    # Only routine muxer/identity tags are allowed for initial video candidates.
    routine_container = {'major_brand','minor_version','compatible_brands','encoder'}
    routine_stream = {'language','handler_name','vendor_id','encoder'}
    if qualified_mux_metadata and kind=='matroska':
        # Final Dub explicitly regenerates these technical muxer tags. They
        # have NO clock authority: decoded frame/sample/packet clocks remain
        # independently required. Arbitrary title, attachment and chapter tags
        # still refuse, as do duplicate case aliases and malformed values.
        def technical(tags,allowed):
            lowered=[k.lower() for k in tags]
            if len(set(lowered))!=len(lowered):return False
            for key,value in tags.items():
                if key.lower() not in allowed or not isinstance(value,str) or len(value)>256:return False
                if key.lower()=='duration' and not re.fullmatch(r'\d{2}:\d{2}:\d{2}\.\d{9}',value):return False
            return True
        if technical(container.get('tags',{}),routine_container|{'duration'}) and all(
                technical(s.get('tags',{}),routine_stream|{'duration'}) for s in video):
            routine_container|=set(container.get('tags',{}))
            routine_stream|={k for s in video for k in s.get('tags',{})}
    extras = (len(streams) != len(audio) + len(video) or payload.get('chapters')
              or set(container.get('tags', {})) - routine_container
              or any(set(s.get('tags', {})) - routine_stream for s in video)
              or any(s.get('codec_type') not in ('video','audio') for s in streams))
    if extras:
        copy_refusal = Refusal(Reason.EXTRA_STREAMS, 'Ancillary streams/chapters/metadata require explicit preservation qualification.')
    tracks = []
    for sound in audio:
        rate = integer(sound.get('sample_rate', '0'), 0, 768000)
        channels = integer(sound.get('channels', 0), 0, 64)
        layout = sound.get('channel_layout') or {1:'mono', 2:'stereo'}.get(channels, 'unknown')
        codec = sound.get('codec_name', 'unknown')
        clock = ratio(sound['time_base'], positive=True)
        tags = sound.get('tags', {})
        refusal = None
        # Surround downmix qualification belongs to later audio pipeline work.
        if codec not in {'aac','mp3','pcm_s16le','pcm_s24le','pcm_s32le','pcm_f32le','flac','vorbis','opus','alac'}:
            refusal = Refusal(Reason.UNSUPPORTED_SOURCE, f'Dub does not support the {codec} codec on this audio track.')
        elif not 8000 <= rate <= 192000 or (channels, layout) not in ((1,'mono'), (2,'stereo')):
            refusal = Refusal(Reason.UNSUPPORTED_SOURCE, 'Dub requires mono or stereo audio at 8–192 kHz.')
        tracks.append(AudioTrack(integer(sound['index']), codec, rate, channels, layout,
            tags.get('language'), tags.get('title'), bool(sound.get('disposition', {}).get('default')),
            clock, optional_clock(sound, 'start_pts', 'start_time', clock),
            optional_clock(sound, 'duration_ts', 'duration', clock), refusal=refusal))
    facts = None
    if video:
        v = video[0]
        sar = v.get('sample_aspect_ratio', '1:1')
        facts = VideoFacts(integer(v['index']), v.get('codec_name', 'unknown'), v.get('profile', 'unknown'),
            v.get('pix_fmt', 'unknown'), integer(v['width'], 1, 32768), integer(v['height'], 1, 32768),
            ratio(sar.replace(':','/'), positive=True) if sar != 'N/A' else Fraction(1),
            ratio(v['avg_frame_rate'], positive=True) if v.get('avg_frame_rate') not in (None,'0/0') else None,
            ratio(v['time_base'], positive=True), v.get('field_order','unknown'),
            tuple(v.get(k, 'unknown') for k in ('color_range','color_space','color_transfer','color_primaries')),
            bool(v.get('side_data_list')))
    return DubSourceDescriptor(identity, SourceKind.VIDEO if facts else SourceKind.AUDIO,
        kind, extension, tuple(tracks), facts, copy_refusal, 'gyan-release-essentials-9.0.2')
