import io
import subprocess
import sys
from threading import Event, Thread
import pytest
from mediaforge.application.contracts.source_timeline import SourceSignature, TimelineRequest, TimelineBuildControl
from mediaforge.infrastructure.ffmpeg.source_timeline import FfprobeTimelineIndexer


class CompletedProcess:
    def __init__(self,data,returncode=0):
        self.stdout=io.BytesIO(data);self.stderr=io.BytesIO(b'diagnostic');self.returncode=returncode
    def poll(self):return self.returncode
    def wait(self,timeout):return self.returncode


def request(tmp_path):
    source=tmp_path/'spaces Кириллица.mp4';source.write_bytes(b'fixture')
    return TimelineRequest('item',SourceSignature.capture(source),7)


def test_streamed_actual_frame_times_and_exact_command(tmp_path):
    calls=[];control=TimelineBuildControl()
    def factory(command,**options):
        calls.append(command)
        return CompletedProcess(b'frame|best_effort_timestamp=0|duration=2\nframe|best_effort_timestamp=2|duration=3\nframe|best_effort_timestamp=5|duration=1\nstream|time_base=1/50\n')
    result=FfprobeTimelineIndexer('authenticated-ffprobe',process_factory=factory).build(request(tmp_path),control)
    assert list(result.timeline.timestamps)==[0,40000,100000]
    assert result.timeline.count==3 and result.timeline.last_duration_us==20000
    assert len(calls)==1 and '-show_frames' in calls[0] and '-show_packets' not in calls[0]
    assert calls[0][calls[0].index('-select_streams')+1]=='v:0'
    assert not control.owns_process


@pytest.mark.parametrize('data',[
    b'frame|duration=40\nstream|time_base=1/1000\n',
    b'frame|best_effort_timestamp=0|duration=40\nframe|best_effort_timestamp=0|duration=40\nstream|time_base=1/1000\n',
    b'frame|best_effort_timestamp=40|duration=40\nframe|best_effort_timestamp=0|duration=40\nstream|time_base=1/1000\n',
    b'frame|best_effort_timestamp=N/A|duration=40\nstream|time_base=1/1000\n',
    b'frame|best_effort_timestamp=0|duration=40\nstream|time_base=1/1000\nstream|time_base=1/1000\n',
    b'frame|best_effort_timestamp=0|duration=40\nstream|time_base=0/1\n',
])
def test_malformed_ambiguous_missing_order_fails_closed(tmp_path,data):
    control=TimelineBuildControl()
    result=FfprobeTimelineIndexer('probe',process_factory=lambda *a,**k:CompletedProcess(data)).build(request(tmp_path),control)
    assert result.timeline is None and result.error and not control.owns_process


@pytest.mark.integration
@pytest.mark.parametrize('cause',['cancel','timeout'])
def test_actual_owned_child_is_reaped_before_return(tmp_path,monkeypatch,cause):
    import mediaforge.infrastructure.ffmpeg.source_timeline as module
    if cause=='timeout':monkeypatch.setattr(module,'INDEX_TIMEOUT_SECONDS',.05)
    control=TimelineBuildControl();started=Event();children=[];results=[]
    def factory(command,**options):
        # Project interpreter is the test child; product still receives only the
        # authenticated managed executable at Bootstrap composition.
        child=subprocess.Popen([sys.executable,'-c','import sys; sys.stdin.read()'],stdin=subprocess.PIPE,**options)
        children.append(child);started.set();return child
    worker=Thread(target=lambda:results.append(FfprobeTimelineIndexer('probe',process_factory=factory).build(request(tmp_path),control)))
    worker.start();assert started.wait(2)
    if cause=='cancel':control.request_cancel()
    worker.join(5)
    assert not worker.is_alive() and children[0].poll() is not None and not control.owns_process
    assert results[0].timeline is None and 'cancelled or timed out' in results[0].error


@pytest.mark.parametrize('incidental', [
    b'stream_group|stream|time_base=1/9999\n',
    b'stream_group|stream|time_base=N/A|best_effort_timestamp=-999|duration=99999\n',
    b'stream_group|stream|time_base=1/0|opaque=\xff\n',
])
def test_class_e_ignores_entire_group_and_uses_only_top_level_authority(tmp_path, incidental):
    data = (b'frame|best_effort_timestamp=250|duration=2\n'
            b'frame|best_effort_timestamp=252|duration=3\n'
            b'frame|best_effort_timestamp=255|duration=1\n'
            + incidental + b'stream|time_base=1/50\n')
    control = TimelineBuildControl()
    result = FfprobeTimelineIndexer('probe', process_factory=lambda *a, **k: CompletedProcess(data)).build(request(tmp_path), control)
    assert result.error == ''
    assert list(result.timeline.timestamps) == [5000000, 5040000, 5100000]
    assert result.timeline.count == 3 and result.timeline.last_duration_us == 20000
    assert result.timeline.map_timestamp(5040000) == 1
    assert not control.owns_process


@pytest.mark.parametrize('frames,ending', [
    (b'frame|best_effort_timestamp=0|duration=1\n', b'unknown_section|stream|time_base=1/50\nstream|time_base=1/50\n'),
    (b'frame|best_effort_timestamp=0|duration=1\n', b''),
    (b'frame|duration=1\n', b'stream|time_base=1/50\n'),
    (b'frame|best_effort_timestamp=N/A|duration=1\n', b'stream|time_base=1/50\n'),
    (b'frame|best_effort_timestamp=0|duration=1\nframe|best_effort_timestamp=0|duration=1\n', b'stream|time_base=1/50\n'),
    (b'frame|best_effort_timestamp=2|duration=1\nframe|best_effort_timestamp=1|duration=1\n', b'stream|time_base=1/50\n'),
    (b'frame|best_effort_timestamp=0|duration=1\n', b'stream|time_base=0/1\n'),
    (b'frame|best_effort_timestamp=0|duration=1\n', b'stream|time_base=-1/50\n'),
    (b'frame|best_effort_timestamp=0|duration=1\n', b'stream|time_base=N/A\n'),
    (b'frame|best_effort_timestamp=0|duration=1\n', b'stream|time_base=1/50\nstream|time_base=1/50\n'),
    (b'frame|best_effort_timestamp=0|duration=1\n', b'stream|time_base=1/50\nstream|time_base=1/1000\n'),
])
def test_class_e_cannot_rescue_unknown_missing_invalid_or_ambiguous_authority(tmp_path, frames, ending):
    data = frames + b'stream_group|stream|time_base=1/50\n' + ending
    control = TimelineBuildControl()
    result = FfprobeTimelineIndexer('probe', process_factory=lambda *a, **k: CompletedProcess(data)).build(request(tmp_path), control)
    assert result.timeline is None and result.error
    assert not control.owns_process


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != 'win32', reason='Authenticated Windows managed runtime')
def test_class_e_generated_mov_real_compact_framing_and_exact_timeline(tmp_path):
    from fractions import Fraction
    from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime

    runtime = resolve_ffmpeg_runtime()
    for generation in (1, 2):
        source = tmp_path / f'public synthetic timecode {generation}.mov'
        made = subprocess.run([
            str(runtime.ffmpeg), '-hide_banner', '-loglevel', 'error',
            '-f', 'lavfi', '-i', 'testsrc2=size=64x48:rate=24000/1001:duration=0.3',
            '-an', '-c:v', 'prores_ks', '-profile:v', '4', '-pix_fmt', 'yuv444p10le',
            '-timecode', '01:00:00:00', str(source),
        ], capture_output=True, timeout=60, shell=False)
        assert made.returncode == 0, made.stderr
        observed = subprocess.run([
            str(runtime.ffprobe), '-v', 'error', '-select_streams', 'v:0',
            '-show_frames', '-show_streams', '-show_entries',
            'frame=best_effort_timestamp,duration:frame_side_data=:stream=time_base',
            '-of', 'compact=p=1:nk=0', str(source),
        ], capture_output=True, timeout=60, shell=False)
        assert observed.returncode == 0, observed.stderr
        records = observed.stdout.splitlines()
        groups = [i for i, line in enumerate(records) if line.startswith(b'stream_group|')]
        streams = [i for i, line in enumerate(records) if line.startswith(b'stream|')]
        assert groups and len(streams) == 1 and groups[-1] < streams[0]
        assert b'\r\n' in observed.stdout.splitlines(keepends=True)[groups[-1]+1:streams[0]]
        facts = [dict(token.split(b'=', 1) for token in line.split(b'|')[1:] if b'=' in token)
                 for line in records if line.startswith(b'frame|')]
        authority = dict(token.split(b'=', 1) for token in records[streams[0]].split(b'|')[1:] if b'=' in token)
        time_base = Fraction(authority[b'time_base'].decode('ascii'))
        expected = [round(int(frame[b'best_effort_timestamp']) * time_base * 1000000) for frame in facts]
        control = TimelineBuildControl()
        result = FfprobeTimelineIndexer(runtime.ffprobe, decoder_threads=4).build(
            TimelineRequest('synthetic', SourceSignature.capture(source), generation), control)
        assert not control.owns_process
        assert result.timeline is not None, result.error
        assert result.timeline.count == len(facts) == 8
        assert list(result.timeline.timestamps) == expected
        assert result.timeline.last_duration_us == round(int(facts[-1][b'duration']) * time_base * 1000000)
        from mediaforge.application.services.media import OpenMediaService
        from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe
        from mediaforge.presentation.models.media_info import info_rows
        metadata = OpenMediaService(FfprobeMediaMetadataProbe(runtime.ffprobe)).open_media(str(source.resolve())).metadata
        assert metadata is not None
        rows = info_rows(metadata, str(result.timeline.count))
        assert next(r['value'] for r in rows if r['label'] == 'Container') == 'QuickTime / MOV'
        assert next(r['value'] for r in rows if r['label'] == 'Codec') == 'Apple ProRes 4444'
        assert next(r['value'] for r in rows if r['label'] == 'Frame rate') == '23.98 fps (average)'
        assert next(r['value'] for r in rows if r['label'] == 'Frames') == '8'
        assert rows[-1]['label'] == 'None'


@pytest.mark.parametrize('data', [
    b'frame|best_effort_timestamp=250|duration=2\r\nframe|best_effort_timestamp=252|duration=3\r\nstream_group|stream|time_base=1/9999\r\n\r\nstream|time_base=1/50\r\n',
    b'\nframe|best_effort_timestamp=250|duration=2\n\r\nframe|best_effort_timestamp=252|duration=3\nstream|time_base=1/50\n\n',
])
def test_class_e1_empty_separators_have_zero_authority_anywhere(tmp_path, data):
    control = TimelineBuildControl()
    result = FfprobeTimelineIndexer('probe', process_factory=lambda *a, **k: CompletedProcess(data)).build(request(tmp_path), control)
    assert result.error == ''
    assert list(result.timeline.timestamps) == [5000000, 5040000]
    assert result.timeline.count == 2 and result.timeline.last_duration_us == 60000
    assert result.timeline.map_timestamp(5040000) == 1
    assert not control.owns_process


@pytest.mark.parametrize('data', [
    b'frame|best_effort_timestamp=0|duration=1\n\r\nunknown_section|time_base=1/50\nstream|time_base=1/50\n',
    b'frame|best_effort_timestamp=0|duration=1\nstream_group|stream|time_base=1/50\n\r\n',
    b'frame|duration=1\n\r\nstream_group|stream|time_base=1/50\nstream|time_base=1/50\n',
    b'frame|best_effort_timestamp=N/A|duration=1\n\r\nstream|time_base=1/50\n',
    b'frame|best_effort_timestamp=0|duration=1\n\r\nframe|best_effort_timestamp=0|duration=1\nstream|time_base=1/50\n',
    b'frame|best_effort_timestamp=2|duration=1\n\r\nframe|best_effort_timestamp=1|duration=1\nstream|time_base=1/50\n',
    b'frame|best_effort_timestamp=0|duration=0\n\r\nstream|time_base=1/50\n',
] + [b'frame|best_effort_timestamp=0|duration=1\n' + whitespace + b'\r\nstream|time_base=1/50\n'
      for whitespace in (b' ', b'\t', b'   ', b'\t ')])
def test_class_e1_separators_cannot_rescue_invalid_nonempty_records(tmp_path, data):
    control = TimelineBuildControl()
    result = FfprobeTimelineIndexer('probe', process_factory=lambda *a, **k: CompletedProcess(data)).build(request(tmp_path), control)
    assert result.timeline is None and result.error
    assert not control.owns_process
