"""Small native source qualification; no AI, Dub render or mux publication."""

from dataclasses import replace
from fractions import Fraction as F
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from mediaforge.features.dub.contracts import AudioFormat, DubControl, DubError, OutputMode, Reason
from mediaforge.features.dub.planning import output_plan, selected_track
from mediaforge.infrastructure.ffmpeg.dub_probe import FfprobeDubInspector
from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest, FfmpegRuntimeResolver
from mediaforge.infrastructure.filesystem.dub_source import ProtectedDubSource, destination_plan
from mediaforge.infrastructure.filesystem.profile import ProfilePaths


pytestmark=[pytest.mark.native,pytest.mark.integration]


@pytest.fixture(scope='module')
def corpus(tmp_path_factory):
    # Explicit test-only reference to already provisioned bytes, authenticated
    # against both project pins. Never a production AppData lookup/fallback/import.
    root=os.environ.get('MEDIAFORGE_TEST_RUNTIME_ROOT')
    assert root, 'Set explicit MEDIAFORGE_TEST_RUNTIME_ROOT to an authenticated existing installation.'
    runtime=FfmpegRuntimeResolver(FfmpegManifest.load()).verify_directory(Path(root))
    directory=tmp_path_factory.mktemp('dub-native-Источники')
    paths=ProfilePaths.for_test(directory/'portable/Data/v2');paths.prepare()
    sources={}
    recipes={
        'mp4':('.mp4',['-f','lavfi','-i','testsrc2=size=96x64:rate=25:duration=2',
                        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2',
                        '-c:v','libx264','-pix_fmt','yuv420p','-profile:v','high','-c:a','aac']),
        'fractional':('.mp4',['-f','lavfi','-i','testsrc2=size=96x64:rate=24000/1001:duration=2',
                        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2',
                        '-c:v','libx264','-pix_fmt','yuv420p','-profile:v','high','-c:a','aac']),
        'prores':('.mov',['-f','lavfi','-i','testsrc2=size=96x64:rate=25:duration=2',
                         '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2',
                         '-c:v','prores_ks','-profile:v','2','-pix_fmt','yuv422p10le','-c:a','pcm_s16le']),
        'mkv':('.mkv',['-f','lavfi','-i','testsrc2=size=96x64:rate=25:duration=2',
                       '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2',
                       '-c:v','libx264','-pix_fmt','yuv420p','-profile:v','high','-c:a','flac']),
        'unsupported-copy':('.mkv',['-f','lavfi','-i','testsrc2=size=96x64:rate=25:duration=2',
                       '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2',
                       '-c:v','mpeg4','-c:a','flac']),
        'independent-audio':('.avi',['-f','lavfi','-i','testsrc2=size=96x64:rate=25:duration=2',
                       '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2',
                       '-c:v','mpeg4','-c:a','pcm_s16le']),
        'aligned-mkv':('.mkv',['-f','lavfi','-i','testsrc2=size=96x64:rate=25:duration=2',
                       '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2',
                       '-c:v','libx264','-pix_fmt','yuv420p','-profile:v','high',
                       '-c:a','flac','-frame_size','4608']),
        'multi':('.mp4',['-f','lavfi','-i','testsrc2=size=96x64:rate=25:duration=2',
                         '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2',
                         '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=2',
                         '-map','0:v','-map','1:a','-map','2:a','-metadata:s:a:0','language=eng',
                         '-metadata:s:a:1','language=rus','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac']),
        'silent':('.mp4',['-f','lavfi','-i','testsrc2=size=96x64:rate=25:duration=1',
                          '-c:v','libx264','-pix_fmt','yuv420p']),
        'wav':('.wav',['-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2','-c:a','pcm_s16le']),
        'mp3':('.mp3',['-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2','-c:a','libmp3lame']),
        'flac':('.flac',['-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2','-c:a','flac']),
        'm4a':('.m4a',['-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2','-c:a','aac']),
    }
    commands=[]
    for key,(suffix,recipe) in recipes.items():
        path=directory/(key+' с пробелами'+suffix)
        command=[str(runtime.ffmpeg),'-nostdin','-v','error',*recipe,'-threads','1',str(path)]
        result=subprocess.run(command,capture_output=True,timeout=30,cwd=paths.temporary,env=paths.child_environment())
        assert result.returncode==0,result.stderr.decode(errors='replace')
        sources[key]=path;commands.append(command)
    (directory/'recipes.json').write_text(json.dumps(commands,ensure_ascii=False,indent=2),encoding='utf-8')
    return directory,runtime,paths,sources


def inspect(corpus,key):
    directory,runtime,paths,sources=corpus
    inspector=FfprobeDubInspector(runtime,paths)
    source=inspector.inspect(sources[key],DubControl())
    facts={'kind':source.kind,'container':source.container,
           'sha256':source.identity.sha256,'job_peak':inspector.last_job_peak,
           'video':repr(source.video),'audio':[repr(t) for t in source.audio_tracks]}
    (directory/(key+'-inspection.json')).write_text(json.dumps(facts,ensure_ascii=False,indent=2),encoding='utf-8')
    return source


@pytest.mark.parametrize('key',['mp4','prores'])
def test_native_candidate_picture_and_selected_audio_clocks(corpus,key):
    source=inspect(corpus,key)
    plan=output_plan(source,OutputMode.SAME_AS_SOURCE)
    assert source.video.clock.records==50 and source.video.clock.duration==F(2)
    assert plan.route.video_codec=='copy' and plan.route.suffix==source.identity.path.suffix
    assert plan.requires_output_qualification
    assert output_plan(source,OutputMode.VIDEO_AUDIO_ONLY).sample_count==96000


def test_native_fractional_cadence_and_bframe_decoding_order(corpus):
    source=inspect(corpus,'fractional')
    assert source.video.frame_rate==F(24000,1001)
    assert source.video.clock.records==48 and source.video.clock.duration==F(1001,500)
    assert source.video.packet_clock.packets==48
    assert source.video.packet_clock.first_dts < source.video.clock.first
    plan=output_plan(source,OutputMode.SAME_AS_SOURCE)
    assert plan.duration==F(1001,500) and plan.sample_count==96096


@pytest.mark.parametrize('key',['wav','mp3','flac'])
def test_native_audio_exact_decoded_sample_span(corpus,key):
    source=inspect(corpus,key)
    track=selected_track(source)
    assert track.clock.samples==96000 and track.clock.duration==F(2)
    for fmt in (AudioFormat.MP3,AudioFormat.WAV):
        plan=output_plan(source,OutputMode.AUDIO,audio_format=fmt)
        assert plan.sample_count==96000 and plan.route.video_codec is None


def test_native_aac_padding_is_observed_not_guessed(corpus):
    source=inspect(corpus,'m4a');track=source.audio_tracks[0]
    # C1 retains decoded and declared clocks separately. AAC padding is a future
    # qualification requirement and cannot silently become an audible span.
    assert track.declared_duration==2
    assert track.clock.samples==96000 and track.clock.duration==track.declared_duration
    assert output_plan(source,OutputMode.AUDIO).sample_count==96000


def test_native_mkv_rounded_sample_clock_refuses_without_guessing(corpus):
    source=inspect(corpus,'mkv')
    assert source.video.clock.duration==2
    assert source.audio_tracks[0].refusal.code==Reason.INVALID_TIMING
    with pytest.raises(DubError) as error:output_plan(source,OutputMode.VIDEO_AUDIO_ONLY)
    assert error.value.code==Reason.INVALID_TIMING


def test_native_mkv_exact_block_clock_is_independent_of_metadata_copy_refusal(corpus):
    source=inspect(corpus,'aligned-mkv')
    assert source.video.clock.records==50 and source.video.clock.duration==2
    assert selected_track(source).clock.samples==96000
    assert output_plan(source,OutputMode.VIDEO_AUDIO_ONLY).sample_count==96000
    # FFmpeg writes uppercase ENCODER/DURATION tags. Preserve rather than
    # silently discard them: this class needs C4 explicit metadata qualification.
    with pytest.raises(DubError) as error:output_plan(source,OutputMode.SAME_AS_SOURCE)
    assert error.value.code==Reason.EXTRA_STREAMS


def test_native_video_refusal_keeps_independent_audio_route(corpus):
    source=inspect(corpus,'independent-audio')
    with pytest.raises(DubError) as error:output_plan(source,OutputMode.SAME_AS_SOURCE)
    assert error.value.code==Reason.EXTRA_STREAMS
    assert output_plan(source,OutputMode.VIDEO_AUDIO_ONLY).sample_count==96000


def test_native_multitrack_no_implicit_selection(corpus):
    source=inspect(corpus,'multi')
    assert [t.index for t in source.audio_tracks]==[1,2]
    assert [t.language for t in source.audio_tracks]==['eng','rus']
    with pytest.raises(DubError) as error:selected_track(source)
    assert error.value.code==Reason.AMBIGUOUS_TRACK
    assert output_plan(source,OutputMode.VIDEO_AUDIO_ONLY,audio_index=2).audio_index==2
    assert source.audio_tracks[0].clock and source.audio_tracks[1].clock


def test_native_no_audio_corrupt_unsupported_and_precancel(corpus):
    source=inspect(corpus,'silent')
    with pytest.raises(DubError) as error:output_plan(source,OutputMode.VIDEO_AUDIO_ONLY)
    assert error.value.code==Reason.NO_AUDIO
    directory,runtime,paths,_=corpus
    for name,content in (('corrupt.mp4',b'broken media'),('playlist.m3u',b'#EXTM3U\nhttps://example.invalid/test.mp4')):
        path=directory/name;path.write_bytes(content)
        with pytest.raises(DubError):FfprobeDubInspector(runtime,paths).inspect(path,DubControl())
    control=DubControl();control.cancel()
    with pytest.raises(DubError) as error:FfprobeDubInspector(runtime,paths).inspect(corpus[3]['wav'],control)
    assert error.value.code==Reason.CANCELLED


def test_native_source_hardlink_external_writer_and_destination_plan(corpus):
    directory,runtime,paths,sources=corpus
    source=inspect(corpus,'wav');plan=output_plan(source,OutputMode.AUDIO,audio_format=AudioFormat.WAV)
    alias=directory/'alias.wav';os.link(sources['wav'],alias)
    with ProtectedDubSource(sources['wav'],DubControl()):
        command=[sys.executable,'-c',
                 'import sys; from pathlib import Path; Path(sys.argv[1]).open("r+b")',str(alias)]
        attempt=subprocess.run(command,capture_output=True,timeout=10,cwd=paths.temporary,env=paths.child_environment())
        assert attempt.returncode!=0 and b'PermissionError' in attempt.stderr
    with sources['wav'].open('r+b'):
        with pytest.raises(DubError) as error:
            with ProtectedDubSource(sources['wav'],DubControl()):pass
        assert error.value.code==Reason.SOURCE_BUSY
    assert sources['wav'].stat().st_size==source.identity.size
    with pytest.raises(DubError) as error:destination_plan(source.identity,plan.route,directory,filename=alias.name)
    assert error.value.code==Reason.SOURCE_ALIAS
    target=destination_plan(source.identity,plan.route,directory)
    assert not target.exists() and target.suffix=='.wav'
    target.write_bytes(b'existing')
    with pytest.raises(DubError) as error:destination_plan(source.identity,plan.route,directory)
    assert error.value.code==Reason.DESTINATION_EXISTS and target.read_bytes()==b'existing'


def test_existing_media_probe_accepts_unchanged_native_inputs(corpus):
    for key in ('mp4','prores','mkv','wav','mp3'):
        metadata=FfprobeMediaMetadataProbe(corpus[1].ffprobe).probe(corpus[3][key])
        assert metadata.duration_seconds>0
        assert metadata.audio_only==(key in ('wav','mp3'))
