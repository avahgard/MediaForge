"""Independent audio/copy route decisions over inspected facts, no I/O or Qt."""

from fractions import Fraction

from .contracts import (
    AudioFormat, AudioTrack, DubError, DubSourceDescriptor, OutputMode,
    OutputPlan, OutputRoute, Reason, SourceKind,
)


def default_output_mode(source: DubSourceDescriptor) -> OutputMode:
    return OutputMode.SAME_AS_SOURCE if source.kind == SourceKind.VIDEO else OutputMode.AUDIO


def selected_track(source: DubSourceDescriptor, index: int | None = None) -> AudioTrack:
    if not source.audio_tracks:
        raise DubError(Reason.NO_AUDIO, 'Source has no audio stream.')
    eligible = [t for t in source.audio_tracks if t.clock and not t.refusal]
    if index is None:
        # Multiple source tracks require an explicit selection even if only one
        # currently qualifies. Language/default tags never imply user consent.
        if len(source.audio_tracks) > 1:
            raise DubError(Reason.AMBIGUOUS_TRACK, 'Select an eligible source audio track.')
        track = source.audio_tracks[0]
    else:
        if type(index) is not int:
            raise DubError(Reason.INVALID_TRACK, 'Select an absolute integer stream index.')
        track = next((t for t in source.audio_tracks if t.index == index), None)
        if track is None:
            raise DubError(Reason.INVALID_TRACK, 'Selected stream is not an audio track.')
    if track not in eligible:
        refusal = track.refusal
        raise DubError(refusal.code if refusal else Reason.INVALID_TIMING,
                       refusal.message if refusal else 'Selected track has no complete clock.')
    return track


def output_plan(source: DubSourceDescriptor, mode: OutputMode, *,
                audio_index: int | None = None, audio_format: AudioFormat = AudioFormat.MP3) -> OutputPlan:
    if not isinstance(mode, OutputMode) or not isinstance(audio_format, AudioFormat):
        raise DubError(Reason.UNSUPPORTED_SOURCE, 'Choose a typed Dub output mode/format.')
    track = selected_track(source, audio_index)
    before = after = Fraction(0)
    if source.kind == SourceKind.VIDEO:
        video = source.video
        if video and video.refusal:
            raise DubError(video.refusal.code, video.refusal.message)
        if video is None or video.clock is None:
            raise DubError(Reason.INVALID_TIMING, 'Dub needs verified video timestamps to align audio to the picture.')
        origin, end = video.clock.first, video.clock.end
        before = max(Fraction(0), origin - track.clock.first)
        after = max(Fraction(0), track.clock.end - end)
        if min(end, track.clock.end) <= max(origin, track.clock.first):
            raise DubError(Reason.EMPTY_AUDIO, 'Selected audio does not intersect the picture span.')
        if mode not in (OutputMode.SAME_AS_SOURCE, OutputMode.VIDEO_AUDIO_ONLY, OutputMode.COMPATIBLE_MP4):
            raise DubError(Reason.UNSUPPORTED_SOURCE, 'Use a video source output mode.')
    else:
        if mode != OutputMode.AUDIO:
            raise DubError(Reason.UNSUPPORTED_SOURCE, 'Standalone audio has no video output mode.')
        origin, end = track.clock.first, track.clock.end
    if mode == OutputMode.SAME_AS_SOURCE:
        if source.copy_refusal:
            raise DubError(source.copy_refusal.code, source.copy_refusal.message)
        video = source.video
        if video.packet_clock is None or video.packet_clock.packets != video.clock.records:
            raise DubError(Reason.UNQUALIFIED_COPY, 'Encoded packet decoding order has no matching qualified clock.')
        # These are deliberately narrow *planning* classes, not C4 mux qualification.
        key = (source.container, video.codec, video.profile, video.pixel_format)
        choices = {
            ('mp4', 'h264', 'High', 'yuv420p'): ('aac', 'mp4-h264-high-420-sdr'),
            ('mov', 'prores', 'Standard', 'yuv422p10le'): ('pcm_s16le', 'mov-prores-standard-422-10-sdr'),
            ('matroska', 'h264', 'High', 'yuv420p'): ('flac', 'mkv-h264-high-420-sdr'),
        }
        for container in ('mp4','matroska'):
            for profile in ('Main','Baseline','Constrained Baseline'):
                choices[(container,'h264',profile,'yuv420p')]=(
                    'aac' if container=='mp4' else 'flac', f'{container}-h264-{profile.lower().replace(" ","-")}-420-sdr')
        _picture_policy(video)
        if key not in choices:
            raise DubError(Reason.UNQUALIFIED_COPY,
                f'Video copy is not qualified for {source.container}/{video.codec}/{video.profile}/{video.pixel_format}. '
                'Select Compatible MP4 for qualified SDR pictures, or Dubbed Audio Only.')
        codec, copy_class = choices[key]
        route = OutputRoute(mode, source.container, source.extension, codec,
                            bitrate=192000 if codec == 'aac' else None,
                            video_codec='copy', copy_class=copy_class)
    elif mode == OutputMode.COMPATIBLE_MP4:
        video=source.video
        if source.copy_refusal and source.copy_refusal.code==Reason.EXTRA_STREAMS:
            raise DubError(Reason.EXTRA_STREAMS,source.copy_refusal.message)
        _picture_policy(video)
        if video.codec not in ('h264','hevc','vp9','av1','mjpeg','prores') or video.pixel_format not in ('yuv420p','yuv422p10le'):
            raise DubError(Reason.UNSUPPORTED_SOURCE,'Compatible MP4 requires a qualified opaque SDR YUV source; choose Dubbed Audio Only.')
        route=OutputRoute(mode,'mp4','.mp4','aac',bitrate=192000,video_codec='libx264')
    else:
        route = OutputRoute(mode, audio_format.value, '.' + audio_format.value,
                            'libmp3lame' if audio_format == AudioFormat.MP3 else 'pcm_s16le',
                            bitrate=192000 if audio_format == AudioFormat.MP3 else None)
    duration = end - origin
    if duration <= 0:
        raise DubError(Reason.INVALID_TIMING, 'Presented duration must be positive.')
    n = duration * route.sample_rate
    samples = (2 * n.numerator + n.denominator) // (2 * n.denominator)
    if route.container == 'wav' and samples * 2 + 4096 >= 2**32:
        raise DubError(Reason.UNSUPPORTED_SOURCE, 'PCM16 output exceeds the qualified RIFF size envelope.')
    return OutputPlan(route, source.identity, track.index, origin, duration, samples, before, after)


def _picture_policy(video):
    """Conservative shared visual envelope; copy/encode qualify separately."""
    failures=[]
    if video.has_side_data:failures.append('picture side data/orientation preservation is unqualified')
    if video.field_order not in ('progressive','unknown'):failures.append('interlaced picture processing is unqualified')
    if video.sar!=1:failures.append(f'pixel aspect {video.sar} is unqualified')
    if video.width>3840 or video.height>2160:failures.append(f'raster {video.width}x{video.height} exceeds the Dub video envelope')
    allowed=({'unknown','tv','pc'},{'unknown','bt709'},{'unknown','bt709'},{'unknown','bt709'})
    for name,value,values in zip(('range','matrix','transfer','primaries'),video.color,allowed):
        if value not in values:failures.append(f'color {name}={value} is unqualified')
    if failures:raise DubError(Reason.UNQUALIFIED_COPY,'; '.join(failures)+'. Choose Dubbed Audio Only.')
