"""Preview timing and actual contained/canceled Windows proxy ownership."""
import logging,subprocess,threading,time
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
import pytest
from mediaforge.infrastructure.ffmpeg.video_preview import FfmpegVideoPreviews,matching_audio,matching_clock
from mediaforge.infrastructure.ffmpeg.preview_job import PreviewJobs
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.application.services.video_preview import VideoPreviewService
from mediaforge.application.contracts.media import MediaInfoFactsDto,MediaMetadataDto
from mediaforge.features.media.processing_control import ProcessingControl
from test_image_preview import Resources


def test_proxy_requires_every_exact_rational_timestamp():
    def data(ticks,tb):return dict(streams=[dict(time_base=tb)],frames=[dict(best_effort_timestamp=t,duration=d) for t,d in ticks])
    original=data([(100,2),(102,3),(105,1)],'1/24')
    assert matching_clock(original,data([(0,4),(4,6),(10,2)],'1/48'))==3
    with pytest.raises(ValueError):matching_clock(original,data([(0,4),(4,4),(8,2)],'1/48'))


def test_audio_clock_missing_duration_is_an_explicit_refusal():
    track = dict(codec_type='audio', codec_name='aac', sample_rate='48000', channels=2,
                 time_base='1/48000', start_pts=0, duration_ts=48000)
    result = {'streams': [track]}
    no_duration = {key: value for key, value in track.items() if key != 'duration_ts'}
    with pytest.raises(ValueError, match='Exact audio timing is unavailable'):
        matching_audio({'streams': [no_duration]}, result)
    with pytest.raises(ValueError, match='changed audio timing'):
        matching_audio({'streams': [dict(track, duration_ts=48002)]}, result)
    matching_audio({'streams': [dict(track, duration_ts=48001)]}, result)


def test_known_alpha_refuses_before_reserving_preview_resources(tmp_path):
    adapter = FfmpegVideoPreviews(object(), None, tmp_path / 'preview')
    metadata = MediaMetadataDto('alpha.mov', str(tmp_path / 'alpha.mov'), 'mov', 2, 'prores',
                                320, 180, 24, info_facts=MediaInfoFactsDto(pixel_format='yuva444p12le'))
    with pytest.raises(ValueError, match='alpha channel'):
        adapter.prepare(metadata, 2, ProcessingControl(RuntimeError))
    assert not adapter.root.exists() and not adapter.entries


def test_proxy_restore_waits_for_original_timeline_and_rejects_stale_generation():
    from PySide6.QtCore import QObject,Signal
    from mediaforge.presentation.bridge.video_preview import VideoPreviewController
    class Playback(QObject):
        changed=Signal()
        canPlay=True
        timelineAvailable=False
        def seek(self,value):calls.append(('seek',value))
        def togglePlayback(self):calls.append(('play',))
    class Model(QObject):pass
    calls=[];discarded=[];item=object();model=Model();model.playback=Playback(model)
    model._workspace=SimpleNamespace(active=item);model.canOpen=True
    service=SimpleNamespace(discard=discarded.append,close=lambda:None,gate=SimpleNamespace(failure=None))
    controller=VideoPreviewController(service,model)
    try:
        controller.restore=(417,True);controller._ready()
        assert calls==[] and controller.restore==(417,True)
        model.playback.timelineAvailable=True;controller._ready()
        assert calls==[('seek',417),('play',)] and controller.restore is None
        controller.serial=2;value=object()
        controller._done((1,item,'1:2'),value,None)
        assert discarded==[value] and controller.current is None
        controller.quality='1:2';controller.active_quality='1:1'
        controller._done((2,item,'1:2'),None,'Reduced preview for this color/rotation class is not qualified.')
        assert controller.quality=='1:1' and controller.current is None
        assert 'color/rotation' in controller.status and 'Using 1:1.' in controller.status
        assert controller.status!='Preparing video preview…'
        model.canOpen=False;controller.quality='1:4'
        controller._done((2,item,'1:4'),value,None)
        assert discarded==[value,value] and controller.quality=='1:1'
        assert 'controls changed' in controller.status and controller.current is None
    finally:controller.shutdown()


def fixture(tmp_path):
    runtime=resolve_ffmpeg_runtime();source=tmp_path/'source.mov'
    subprocess.run([str(runtime.ffmpeg),'-v','error','-n','-f','lavfi','-i',
        'testsrc2=s=320x180:r=24000/1001:d=0.5','-f','lavfi','-i','sine=frequency=440:duration=0.5',
        '-c:v','libx264','-pix_fmt','yuv420p','-c:a','pcm_s16le','-threads','1',str(source)],check=True,capture_output=True,timeout=15)
    service=VideoPreviewService(FfmpegVideoPreviews(runtime,Resources(),tmp_path/'preview'),MediaProcessingGate())
    metadata=MediaMetadataDto(source.name,str(source),'mov',.5,'h264',320,180,24000/1001)
    return service,metadata


@pytest.mark.native
@pytest.mark.integration
def test_actual_proxy_quality_identity_audio_and_cleanup(tmp_path):
    service,metadata=fixture(tmp_path);control=ProcessingControl(RuntimeError)
    import hashlib
    before=hashlib.sha256(Path(metadata.source_path).read_bytes()).hexdigest()
    value=service.prepare(metadata,2,control)
    assert (value.width,value.height)==(160,90) and 0<value.peak_job<=512*1024**2
    probe=subprocess.run([str(service.adapter.runtime.ffprobe),'-v','error','-show_streams','-of','json',value.path],capture_output=True,text=True,check=True,timeout=10)
    assert 'pcm_s16le' in probe.stdout
    # Source write/delete denied for the full proxy lifetime.
    with pytest.raises(PermissionError):Path(metadata.source_path).write_bytes(b'changed')
    assert hashlib.sha256(Path(metadata.source_path).read_bytes()).hexdigest()==before
    service.discard(value)
    assert not Path(value.path).exists() and not service.gate.busy and not service.adapter.entries


@pytest.mark.native
@pytest.mark.integration
def test_aac_copy_proxy_keeps_declared_track_end_and_packet_bytes(tmp_path):
    from hashlib import sha256
    import json
    from struct import pack_into, unpack_from
    from mediaforge.application.services.media import OpenMediaService
    from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe

    runtime = resolve_ffmpeg_runtime()
    source = tmp_path / 'Recorder AAC Кириллица.mp4'
    subprocess.run([str(runtime.ffmpeg), '-v', 'error', '-n',
        '-f', 'lavfi', '-i', 'testsrc2=s=128x96:r=60:d=0.5',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=0.5',
        '-c:v', 'libx264', '-bf', '0', '-threads', '1', '-c:a', 'aac',
        '-use_editlist', '0', str(source)], check=True, capture_output=True, timeout=15)

    # Recorder-style MP4: encoded tail packets extend past the audio mdhd
    # duration. Change only this test fixture's own declared track duration.
    data = bytearray(source.read_bytes())

    def boxes(start, end):
        while start < end:
            size, kind = unpack_from('>I4s', data, start)
            assert 8 <= size <= end - start
            yield kind, start + 8, start + size
            start += size

    moov = next((a, b) for k, a, b in boxes(0, len(data)) if k == b'moov')
    audio_headers = []
    for _, a, b in (box for box in boxes(*moov) if box[0] == b'trak'):
        mdia = next((lo, hi) for k, lo, hi in boxes(a, b) if k == b'mdia')
        children = {k: lo for k, lo, _ in boxes(*mdia)}
        if data[children[b'hdlr'] + 8:children[b'hdlr'] + 12] == b'soun':
            audio_headers.append(children[b'mdhd'])
    assert len(audio_headers) == 1
    header = audio_headers[0]
    assert data[header] == 0
    rate, duration = unpack_from('>II', data, header + 12)
    assert rate == 48000 and duration > 20000
    end = duration - 1450
    pack_into('>I', data, header + 16, end)
    source.write_bytes(data)
    original = sha256(data).hexdigest()

    def audio(path):
        return json.loads(subprocess.check_output([str(runtime.ffprobe), '-v', 'error',
            '-select_streams', 'a:0', '-show_streams', '-show_packets',
            '-show_data_hash', 'sha256', '-of', 'json', str(path)], timeout=15))

    before = audio(source)
    assert before['streams'][0]['start_pts'] == 0 and before['streams'][0]['duration_ts'] == end
    assert any(packet['pts'] >= end for packet in before['packets'])
    expected = [(p['pts'], p['data_hash']) for p in before['packets'] if p['pts'] < end]
    metadata = OpenMediaService(FfprobeMediaMetadataProbe(runtime.ffprobe)).open_media(str(source)).metadata
    assert metadata is not None
    service = VideoPreviewService(FfmpegVideoPreviews(runtime, Resources(), tmp_path / 'preview'), MediaProcessingGate())
    try:
        value = service.prepare(metadata, 2, service.new_control())
        assert (value.width, value.height) == (64, 48)
        after = audio(value.path)
        assert after['streams'][0]['start_pts'] == 0 and after['streams'][0]['duration_ts'] == end
        assert [(p['pts'], p['data_hash']) for p in after['packets']] == expected
        last = after['packets'][-1]
        assert last['pts'] + last['duration'] == end
        assert sha256(source.read_bytes()).hexdigest() == original
    finally:
        service.close()
    assert not service.gate.busy and not service.gate.failure


@pytest.mark.native
@pytest.mark.integration
def test_verified_proxy_cache_reuses_tiers_and_releases_all_owners(tmp_path):
    service,metadata=fixture(tmp_path)
    half=service.prepare(metadata,2,ProcessingControl(RuntimeError));service.retire(half)
    quarter=service.prepare(metadata,4,ProcessingControl(RuntimeError));service.retire(quarter)
    assert any(s.get('phase')=='source-timing-reused-under-immutable-hash' for s in service.last_metrics['stages'])
    again=service.prepare(metadata,2,ProcessingControl(RuntimeError))
    assert again is half and service.last_metrics['reused'] and service.last_metrics['peak_job']==0
    assert len(service.adapter.entries)==2
    with pytest.raises(PermissionError):Path(half.path).write_bytes(b'invalid proxy')
    with pytest.raises(PermissionError):Path(metadata.source_path).write_bytes(b'invalid source')
    service.retire(again);service.close()
    assert not service.adapter.entries and not service.adapter.retained and not list(service.adapter.root.iterdir())


@pytest.mark.native
@pytest.mark.integration
def test_proxy_cancellation_before_spawn_and_host_refusal(tmp_path):
    service,metadata=fixture(tmp_path);control=ProcessingControl(RuntimeError);control.cancel()
    with pytest.raises(RuntimeError):service.prepare(metadata,2,control)
    assert not service.gate.busy and not service.adapter.entries
    sample=service.adapter.resources.sample();sample['available_physical']=0;service.adapter.resources.sample=lambda:sample
    with pytest.raises(ValueError,match='headroom'):service.prepare(metadata,2,ProcessingControl(RuntimeError))
    assert not service.gate.busy


@pytest.mark.native
@pytest.mark.integration
def test_suspended_assignment_failure_reaps_without_execution(tmp_path,monkeypatch):
    jobs=PreviewJobs(32*1024**2);created=[];real=subprocess.Popen
    def spawn(*args,**kwargs):
        child=real(*args,**kwargs);created.append(child);return child
    monkeypatch.setattr('mediaforge.infrastructure.ffmpeg.preview_job.subprocess.Popen',spawn)
    jobs.api.AssignProcessToJobObject=lambda *_:0
    import sys
    witness=tmp_path/'uncontained.txt'
    try:
        with pytest.raises(OSError):jobs([sys.executable,'-c',f'open({str(witness)!r},"w").write("unsafe")'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        assert len(created)==1 and created[0].poll() is not None and not witness.exists()
    finally:jobs.close()


@pytest.mark.native
@pytest.mark.integration
def test_proxy_cancel_during_owned_decode_reaps_and_cleans(tmp_path,monkeypatch):
    service,metadata=fixture(tmp_path);control=ProcessingControl(RuntimeError)
    created=[];started=threading.Event();factory=PreviewJobs.__call__;errors=[]
    def start(self,command,**options):
        process=factory(self,command,**options);created.append(process)
        if Path(command[0]).name=='ffmpeg.exe':started.set()
        return process
    monkeypatch.setattr(PreviewJobs,'__call__',start)
    def work():
        try:service.prepare(metadata,2,control)
        except Exception as error:errors.append(str(error))
    thread=threading.Thread(target=work);thread.start()
    assert started.wait(10);control.cancel();thread.join(10)
    assert not thread.is_alive() and errors and all(p.poll() is not None for p in created)
    assert not service.adapter.entries and not service.gate.busy and not list(service.adapter.root.iterdir())


@pytest.mark.native
@pytest.mark.integration
def test_job_commit_refuses_large_allocation_without_escape(tmp_path):
    import sys
    jobs=PreviewJobs(32*1024**2)
    # The venv's Windows launcher starts another process; this control tests
    # commit rather than the separately tested one-process containment limit.
    child=jobs([sys._base_executable,'-c','x=bytearray(80*1024**2)'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        _,error=child.communicate(timeout=8)
        assert child.returncode!=0 and b'MemoryError' in error
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=2)
        jobs.close()


def test_unresolved_retirement_quarantines_shared_processing(tmp_path,monkeypatch):
    gate=MediaProcessingGate();service=VideoPreviewService(FfmpegVideoPreviews(None,None,tmp_path),gate)
    value=SimpleNamespace(path=str(tmp_path/'owned/picture.mov'),source_guard=SimpleNamespace(close=lambda:None))
    service.adapter.entries.append(value)
    monkeypatch.setattr(service.adapter,'_remove',lambda *_:(_ for _ in ()).throw(PermissionError('locked scratch')))
    with pytest.raises(PermissionError):service.discard(value)
    assert gate.busy and gate.failure and gate.acquire() is None and service.adapter.entries==[value]
