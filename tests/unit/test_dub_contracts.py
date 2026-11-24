from dataclasses import replace
from fractions import Fraction as F
from pathlib import Path
from types import SimpleNamespace

import pytest

from mediaforge.features.dub.contracts import (
    AudioFormat, ClockFacts, DubError, OutputMode, PacketClock, Reason, Refusal, SourceIdentity,
)
from mediaforge.features.dub.planning import default_output_mode, output_plan, selected_track
from mediaforge.infrastructure.ffmpeg.dub_probe import FfprobeDubInspector, FrameClock, parse_metadata


def payload():
    return {'format': {'format_name':'mov,mp4,m4a,3gp,3g2,mj2',
                       'tags':{'major_brand':'isom'}}, 'streams': [
        dict(index=0, codec_type='video', codec_name='h264', profile='High',
             pix_fmt='yuv420p', width=96, height=64, time_base='1/12800',
             avg_frame_rate='25/1', field_order='progressive', sample_aspect_ratio='1:1'),
        dict(index=1, codec_type='audio', codec_name='aac', sample_rate='48000',
             channels=1, channel_layout='mono', time_base='1/48000',
             start_pts=4800, start_time='9999.0', duration_ts=48000,
             tags={'language':'eng'}, disposition={'default':1})]}


def source():
    identity=SourceIdentity(Path('D:/source.mp4'),1,2,100,0,'a'*64)
    descriptor=parse_metadata(payload(),identity)
    audio=replace(descriptor.audio_tracks[0],clock=ClockFacts(F(1,48000),F(1,10),F(11,10),47,'b'*64,48000))
    video=replace(descriptor.video,clock=ClockFacts(F(1,12800),F(0),F(1),25,'c'*64),
                  packet_clock=PacketClock(F(1,12800),25,F(-2,25),F(22,25),'d'*64))
    return replace(descriptor,audio_tracks=(audio,),video=video)


def test_three_modes_and_exact_audio_contracts():
    s=source()
    assert default_output_mode(s)==OutputMode.SAME_AS_SOURCE
    copy=output_plan(s,OutputMode.SAME_AS_SOURCE)
    assert copy.route.video_codec=='copy' and copy.route.suffix=='.mp4'
    assert copy.route.audio_codec=='aac' and copy.requires_output_qualification
    assert copy.route.audio_language=='rus'
    assert copy.sample_count==48000 and copy.duration==F(1)
    assert copy.excluded_audio_after==F(1,10) and copy.requires_span_acknowledgment
    for mode,src in ((OutputMode.VIDEO_AUDIO_ONLY,s),
                     (OutputMode.AUDIO,replace(s,kind='audio',video=None))):
        mp3=output_plan(src,mode)
        wav=output_plan(src,mode,audio_format=AudioFormat.WAV)
        assert (mp3.route.audio_codec,mp3.route.sample_rate,mp3.route.channels,mp3.route.bitrate)==('libmp3lame',48000,1,192000)
        assert wav.route.audio_codec=='pcm_s16le' and wav.route.video_codec is None


def test_unsupported_copy_does_not_block_audio_only():
    s=source();s=replace(s,video=replace(s.video,codec='hevc',pixel_format='yuv420p10le'))
    with pytest.raises(DubError) as error:output_plan(s,OutputMode.SAME_AS_SOURCE)
    assert error.value.code==Reason.UNQUALIFIED_COPY
    assert output_plan(s,OutputMode.VIDEO_AUDIO_ONLY).route.suffix=='.mp3'


def test_missing_packet_order_refuses_copy_independently():
    s=source();s=replace(s,video=replace(s.video,packet_clock=None))
    with pytest.raises(DubError) as error:output_plan(s,OutputMode.SAME_AS_SOURCE)
    assert error.value.code==Reason.UNQUALIFIED_COPY
    assert output_plan(s,OutputMode.VIDEO_AUDIO_ONLY).sample_count==48000


def test_multitrack_uses_absolute_indices_never_language_or_default():
    s=source();second=replace(s.audio_tracks[0],index=4,language='rus',default=False)
    s=replace(s,audio_tracks=(*s.audio_tracks,second))
    with pytest.raises(DubError) as error:selected_track(s)
    assert error.value.code==Reason.AMBIGUOUS_TRACK
    assert selected_track(s,4)==second
    assert output_plan(s,OutputMode.VIDEO_AUDIO_ONLY,audio_index=4).audio_index==4
    with pytest.raises(DubError):selected_track(s,0)
    with pytest.raises(DubError):selected_track(s,True)


def test_no_audio_and_unreliable_picture_span_refuse():
    with pytest.raises(DubError) as error:selected_track(replace(source(),audio_tracks=()))
    assert error.value.code==Reason.NO_AUDIO
    s=source();s=replace(s,video=replace(s.video,clock=None))
    with pytest.raises(DubError) as error:output_plan(s,OutputMode.VIDEO_AUDIO_ONLY)
    assert error.value.code==Reason.INVALID_TIMING


@pytest.mark.parametrize('sample_rate,stream,extent', [
    (48000, 'audio track', b'nb_samples=48'),
    (None, 'video', b'duration=48'),
])
@pytest.mark.parametrize('second,detail', [
    (47, 'decoded timestamps overlap'),
    (0, 'decoded timestamps repeat or go backwards'),
    (-1, 'decoded timestamps repeat or go backwards'),
    (48, None),
])
def test_decoded_clock_diagnostic_survives_output_planning(sample_rate, stream, extent, second, detail):
    class Probe:
        def lines(self, command):
            for start in (0, second):
                yield f'frame|pts={start}|best_effort_timestamp={start}|'.encode() + extent + b'\n'

    descriptor = source()
    inspector = FfprobeDubInspector(SimpleNamespace(ffprobe='ffprobe.exe'), None)
    args = (Probe(), descriptor.identity.path, 1 if sample_rate else 0, F(1, 48000), sample_rate)
    if detail is None:
        clock = inspector.scan(*args)
        assert clock.records == 2 and clock.end == F(96, 48000)
        return
    with pytest.raises(DubError) as inspected:
        inspector.scan(*args)
    refusal = Refusal(inspected.value.code, str(inspected.value))
    assert refusal.code == Reason.INVALID_TIMING
    assert f"this {stream} because its {detail}" in refusal.message
    if sample_rate:
        track = replace(descriptor.audio_tracks[0], clock=None, refusal=refusal)
        descriptor = replace(descriptor, audio_tracks=(track,))
    else:
        descriptor = replace(descriptor, video=replace(descriptor.video, clock=None, refusal=refusal))
    for mode in (OutputMode.SAME_AS_SOURCE, OutputMode.COMPATIBLE_MP4, OutputMode.VIDEO_AUDIO_ONLY):
        with pytest.raises(DubError) as planned:
            output_plan(descriptor, mode)
        assert planned.value.code == refusal.code and str(planned.value) == refusal.message


def test_integer_clock_authority_and_stream_metadata():
    s=parse_metadata(payload(),source().identity)
    t=s.audio_tracks[0]
    assert t.declared_start==F(1,10) and t.declared_duration==1
    assert t.language=='eng' and t.default and t.index==1
    assert s.video.time_base==F(1,12800)


def test_final_matroska_technical_metadata_is_explicit_and_never_clock_authority():
    p=payload();p['format']=dict(format_name='matroska,webm',tags={'ENCODER':'Lavf63.3.100'})
    p['streams'][0]['tags']={'ENCODER':'Lavc63 h264','DURATION':'00:00:02.000000000'}
    identity=replace(source().identity,path=Path('D:/source.mkv'))
    assert parse_metadata(p,identity).copy_refusal.code==Reason.EXTRA_STREAMS
    result=parse_metadata(p,identity,qualified_mux_metadata=True)
    assert result.copy_refusal is None and result.video.clock is None
    # The descriptive metadata is not used as a replacement for decoded clocks.
    p['streams'][0]['tags']['DURATION']='00:00:99.000000000'
    assert parse_metadata(p,identity,qualified_mux_metadata=True).video.clock is None
    for tags in ({'TITLE':'private title'},{'ENCODER':'a','encoder':'b'}, {'DURATION':'NaN'}):
        p['streams'][0]['tags']=tags
        assert parse_metadata(p,identity,qualified_mux_metadata=True).copy_refusal.code==Reason.EXTRA_STREAMS


@pytest.mark.parametrize('mutation', ['chapters','subtitle','timecode','rename','cover'])
def test_unqualified_extras_and_suffix_refuse_only_copy(mutation):
    p=payload();identity=source().identity
    if mutation=='chapters':p['chapters']=[{'id':0}]
    elif mutation=='subtitle':p['streams'].append({'index':3,'codec_type':'subtitle'})
    elif mutation=='timecode':p['streams'][0]['tags']={'timecode':'00:00:00:00'}
    elif mutation=='cover':p['streams'].append({'index':3,'codec_type':'video','disposition':{'attached_pic':1}})
    else:identity=replace(identity,path=identity.path.with_suffix('.mov'))
    s=parse_metadata(p,identity)
    assert s.copy_refusal is not None
    assert len(s.audio_tracks)==1


@pytest.mark.parametrize('value',['0/0','-1/25','NaN','Infinity','1/'+('9'*70),'1e999999999999999'])
def test_invalid_timebase_refuses(value):
    p=payload();p['streams'][1]['time_base']=value
    with pytest.raises((ValueError,ZeroDivisionError)):parse_metadata(p,source().identity)


@pytest.mark.parametrize('record',[
    b'frame|best_effort_timestamp=N/A|duration=1\n',
    b'frame|best_effort_timestamp=0|duration=0\n',
    b'frame|best_effort_timestamp=0|duration=1|duration=2\n',
    b'packet|pts=0|duration=1\n',
])
def test_clock_rejects_ambiguous_records(record):
    with pytest.raises((ValueError,KeyError)):FrameClock(F(1,25)).add(record)


def test_best_effort_timestamp_cannot_replace_actual_pts():
    for record in (b'frame|best_effort_timestamp=0|duration=1\n',
                   b'frame|pts=1|best_effort_timestamp=0|duration=1\n'):
        with pytest.raises(ValueError):FrameClock(F(1,25)).add(record)


def test_rational_vfr_negative_origin_and_backwards_refusal():
    clock=FrameClock(F(1,30000))
    for start,duration in ((-1001,1001),(0,2002),(2002,1001)):
        clock.add(f'frame|pts={start}|best_effort_timestamp={start}|duration={duration}\n'.encode())
    result=clock.finish()
    assert result.first==F(-1001,30000) and result.duration==F(4004,30000)
    assert result.records==3
    with pytest.raises(ValueError):clock.add(b'frame|pts=2002|best_effort_timestamp=2002|duration=1001\n')


def test_audio_effective_samples_and_half_up_rounding():
    clock=FrameClock(F(1,14112000),48000)
    clock.add(b'frame|pts=324870|best_effort_timestamp=324870|nb_samples=47\n')
    facts=clock.finish()
    assert facts.samples==47 and facts.duration==F(47,48000)
    s=source();s=replace(s,video=replace(s.video,clock=replace(s.video.clock,end=F(3,96000))))
    s=replace(s,audio_tracks=(replace(s.audio_tracks[0],clock=replace(s.audio_tracks[0].clock,first=F(0))),))
    assert output_plan(s,OutputMode.VIDEO_AUDIO_ONLY).sample_count==2
