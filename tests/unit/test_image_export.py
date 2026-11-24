"""Source-authoritative conversion plans and native, bounded publication gates."""
from dataclasses import replace
from fractions import Fraction
import hashlib,threading,subprocess,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from PySide6.QtGui import QImage,QColor
from mediaforge.application.contracts.image_source import SequenceSource
from mediaforge.application.contracts.image_export import jpeg_qscale
from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.services.image_export import capture,describe
from mediaforge.application.services.images import ImageSourceService
from mediaforge.application.state.workspace import MediaItem
from mediaforge.infrastructure.images.reader import QtImageReader
from mediaforge.infrastructure.ffmpeg.export import FfmpegExportExecutor
from mediaforge.infrastructure.ffmpeg import image_export as image_adapter
from mediaforge.features.media.export import ExportError,ExportControl


def test_portable_image_codec_uses_authenticated_site(monkeypatch,tmp_path):
    app=tmp_path/'Core/App/mediaforge/infrastructure'
    python=tmp_path/'Core/Python/python.exe'
    worker=app/'images/encode_worker.py'
    plan=tmp_path/'plan.json'
    monkeypatch.setattr(image_adapter,'__file__',str(app/'ffmpeg/image_export.py'))
    monkeypatch.setattr(image_adapter.sys,'_base_executable',str(python))
    monkeypatch.setattr(image_adapter.sys,'mediaforge_portable_executable',str(tmp_path/'MediaForge.exe'),raising=False)
    monkeypatch.setattr(image_adapter.sys,'mediaforge_core_guard',None,raising=False)
    with pytest.raises(ExportError,match='authenticated'):
        image_adapter._codec_command(plan)
    guard=SimpleNamespace(root=tmp_path,members={python,worker})
    monkeypatch.setattr(image_adapter.sys,'mediaforge_core_guard',guard)
    assert image_adapter._codec_command(plan)==[str(python),'-I','-B',str(worker),str(tmp_path/'Core/Site'),str(plan)]
    guard.members.remove(worker)
    with pytest.raises(ExportError,match='authenticated'):
        image_adapter._codec_command(plan)


def make_item(tmp_path,fmt='png',alpha=True):
    folder=tmp_path/'inputs';folder.mkdir(exist_ok=True)
    path=folder/('source.'+fmt);image=QImage(128,96,QImage.Format_RGBA8888 if alpha else QImage.Format_RGB32)
    image.fill(QColor(200,40,80,128 if alpha else 255));assert image.save(str(path))
    facts=QtImageReader().probe(str(path));item=MediaItem.create(MediaMetadataDto('source',facts.path,fmt,None,'',128,96,None,source=facts))
    destination=tmp_path/'outputs';destination.mkdir(exist_ok=True);item.output.folder=str(destination)
    return item


def make_sequence(tmp_path,fmt='png',alpha=False):
    item=make_item(tmp_path,fmt,alpha)
    path=Path(item.metadata.source_path)
    for slot,n in enumerate((1001,1002,1003)):
        frame=QImage(128,96,QImage.Format_RGBA8888 if alpha else QImage.Format_RGB32);frame.fill(QColor(200,40+slot*30,80,128 if alpha else 255));assert frame.save(str(path.with_name(f'picture_{n:04d}.'+fmt)))
    manifest=QtImageReader().discover_sequence(str(path.with_name('picture_1001.'+fmt)),threading.Event())
    source=SequenceSource(manifest.members[0].path,manifest.identity,manifest)
    item=MediaItem.create(replace(item.metadata,source_path=source.path,source=source));item.sequence.confirmed=True
    item.sequence.rate=Fraction(24000,1001);item.output.folder=str(tmp_path/'outputs')
    return item


def test_source_plan_never_uses_preview_and_preserves_slot_identity(tmp_path):
    item=make_sequence(tmp_path);item.trim.enabled=True;item.sequence.in_slot=1;item.sequence.out_slot=3
    request=capture(item)
    assert (request.start,request.end,request.rate,request.width,request.height)==(1,3,Fraction(24000,1001),128,96)
    assert request.source_identity is item.metadata.source
    item.sequence.confirmed=False;assert not describe(item)['valid']
    item.sequence.confirmed=True;item.output.kind='frame';item.output.container='png';item.sequence.current_slot=2
    assert (capture(item).start,capture(item).end)==(2,3)


@pytest.mark.parametrize('ticks, expected', [((0, 16667, 33333), Fraction(60)), ((0, 16667, 34000), None)])
def test_exported_video_sequence_fps_hint_requires_the_decoded_cadence(tmp_path, ticks, expected):
    from array import array
    from mediaforge.application.contracts.image_export import PictureSnapshot
    from mediaforge.application.contracts.source_timeline import SourcePresentationTimeline, SourceSignature

    source = tmp_path / 'source.mp4'
    source.touch()
    item = MediaItem.create(MediaMetadataDto(source.name, str(source), 'mp4', .05, 'h264', 128, 96, 60.,
                                            navigation_rate_ratio='60'))
    item.output.kind, item.output.container = 'sequence', 'png'
    item.output.folder, item.output.filename = str(tmp_path), 'Sequence'
    timeline = SourcePresentationTimeline.from_timestamps(array('q', ticks), 16667)
    snapshot = PictureSnapshot(SourceSignature.capture(source), timeline, None, 1, 1, 0)
    assert capture(item, snapshot=snapshot).rate == expected


def test_explicit_matte_webp_ack_and_source_safety(tmp_path):
    item=make_item(tmp_path);item.output.container='jpeg';assert not describe(item)['valid']
    item.output.matte='white';assert describe(item)['valid']
    item.output.container='webp';assert not describe(item)['valid'];item.output.webp_ack=True;assert describe(item)['valid']
    item.output.filename='../source';
    with pytest.raises(ExportError):capture(item)
    item.output.filename='existing';item.output.container='png';(Path(item.output.folder)/'existing.png').write_bytes(b'protected')
    with pytest.raises(ExportError,match='exists'):capture(item)
    tiff=make_item(tmp_path,fmt='tiff');tiff.metadata=replace(tiff.metadata,source=replace(tiff.metadata.source,facts=replace(tiff.metadata.source.facts,variant='associated-alpha')))
    assert not describe(tiff)['valid'] and 'Associated-alpha TIFF' in describe(tiff)['message']


def test_jpeg_quality_mapping_is_explicit_and_monotonic():
    assert jpeg_qscale(1)==31 and jpeg_qscale(100)==2
    assert all(jpeg_qscale(q+1)<=jpeg_qscale(q) for q in range(1,100))
    for invalid in (0,101,True,'90'):
        with pytest.raises(ValueError):jpeg_qscale(invalid)


@pytest.mark.parametrize('start,numbers', [('1',('1','2','3')), ('01',('01','02','03')),
    ('001',('001','002','003')), ('000',('000','001','002')), ('1001',('1001','1002','1003')),
    ('0000000001',('0000000001','0000000002','0000000003')), ('0099',('0099','0100','0101')),
    ('99',('99','100','101'))])
def test_numbering_uses_text_width_and_filename(tmp_path,start,numbers):
    item=make_sequence(tmp_path)
    item.output.kind='sequence';item.output.container='png'
    item.output.filename='Merge Copy 雪';item.output.sequence_start=start
    request=capture(item)
    assert request.destination.name=='Merge Copy 雪'
    assert tuple(request.numbering.name(i) for i in range(3))==tuple(f'Merge Copy 雪.{n}.png' for n in numbers)


def test_sequence_numbering_and_disk_refusals(tmp_path,monkeypatch):
    from mediaforge.infrastructure.filesystem import image_capacity
    item=make_sequence(tmp_path)
    item.output.kind='sequence';item.output.container='png'
    item.output.sequence_start='9223372036854775807'
    with pytest.raises(ExportError,match='64-bit'):capture(item)
    item.output.sequence_start='1'
    request=capture(item)
    monkeypatch.setattr(image_capacity,'free_bytes',lambda _:0)
    with pytest.raises(ExportError,match='additional bytes'):image_capacity.check(request)
    with pytest.raises(ExportError,match='safety reserve'):image_capacity.check_floor(request)
    assert not request.destination.exists()


class Resources:
    # These native executor tests qualify processing/file pixels, not the GPU.
    def sample(self):return dict(total_physical=32*1024**3,available_physical=16*1024**3,total_commit=64*1024**3,available_commit=32*1024**3,dedicated=0,shared=0,image_private_growth=0)
    sample_host = sample


@pytest.mark.parametrize('source_depth', [8, 16])
def test_integer_transfer_lookup_matches_reference_at_every_sample(source_depth):
    import numpy as np
    from mediaforge.infrastructure.images import precision
    from mediaforge.infrastructure.images.encode_worker import transfer_lookup

    samples = np.arange(2**source_depth, dtype=np.uint16)
    triples = np.repeat(samples.reshape((1, -1, 1)), 3, axis=2)
    if source_depth == 8:
        triples *= 257
    reference = precision.convert(precision.bt709_to_srgb(triples), '8')[0, :, 1]
    assert np.array_equal(transfer_lookup(precision, source_depth), reference)


def executor():
    from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
    return FfmpegExportExecutor(resolve_ffmpeg_runtime(),image_service=ImageSourceService(QtImageReader(),resources=Resources()))


@pytest.fixture
def recorder_video(tmp_path):
    from mediaforge.application.contracts.image_export import PictureSnapshot
    from mediaforge.application.contracts.source_timeline import SourceSignature, TimelineBuildControl, TimelineRequest
    from mediaforge.application.services.media import OpenMediaService
    from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe
    from mediaforge.infrastructure.ffmpeg.source_timeline import FfprobeTimelineIndexer

    runner = executor()
    runtime = runner._runtime
    source = tmp_path/'Тест записи.mp4'
    subprocess.run([str(runtime.ffmpeg), '-v', 'error', '-n', '-f', 'lavfi', '-i', 'testsrc2=s=320x240:r=60000/1001:d=0.15',
                    '-vf', 'select=not(eq(mod(n\\,3)\\,1)),setpts=PTS+2/TB', '-fps_mode', 'vfr', '-c:v', 'libx264',
                    '-threads', '1', '-pix_fmt', 'yuv420p', '-colorspace', 'bt709', '-color_primaries', 'bt709',
                    '-color_trc', 'bt709', '-video_track_timescale', '90000', str(source)],
                   check=True, capture_output=True, timeout=15)
    metadata = OpenMediaService(FfprobeMediaMetadataProbe(runtime.ffprobe)).open_media(str(source)).metadata
    assert metadata is not None
    signature = SourceSignature.capture(source)
    indexed = FfprobeTimelineIndexer(runtime.ffprobe).build(TimelineRequest('recorder', signature, 1), TimelineBuildControl())
    assert indexed.timeline and indexed.timeline.count >= 4, indexed.error
    snapshot = PictureSnapshot(signature, indexed.timeline, 2, 1, 1, -indexed.timeline.timestamps[0])
    item = MediaItem.create(metadata)
    output = tmp_path/'outputs'
    output.mkdir()
    item.output.folder = str(output)
    return runner, item, snapshot


@pytest.mark.native
@pytest.mark.integration
def test_recorder_scaled_pixels_and_typed_output(recorder_video):
    runner, item, snapshot = recorder_video
    for name, container, mode, placement, crop, depth, trim in (
        ('original', 'png', 'frame', None, False, '8', False),
        ('scaled', 'png', 'frame', 'stretch', False, '8', False),
        ('scaled-jpeg', 'jpeg', 'frame', 'stretch', False, '8', False),
        ('trim-png', 'png', 'sequence', 'stretch', False, '16', True),
        ('crop-fill', 'tiff', 'sequence', 'fill', True, '16', True),
        ('transparent-fit', 'tiff', 'frame', 'fit', False, 'float', False),
    ):
        item.output.filename, item.output.kind, item.output.container = name, mode, container
        item.output.image_depth = depth
        item.crop.enabled = crop
        item.crop.x, item.crop.y, item.crop.width, item.crop.height = 20, 20, 280, 200
        item.resize.enabled = placement is not None
        item.resize.mode, item.resize.placement = 'format', placement or 'stretch'
        item.resize.width, item.resize.height = 144, 96
        item.trim.enabled, item.trim.in_ms, item.trim.out_ms = trim, 0, 40
        request = capture(item, snapshot=snapshot)
        result = runner.execute(request, ExportControl())
        paths = sorted(result.output.iterdir()) if mode == 'sequence' else [result.output]
        assert len(paths) == request.count
        if depth == 'float':
            import tifffile
            rgba = tifffile.imread(paths[0])
            assert rgba.shape == (96, 144, 4) and rgba.dtype.kind == 'f'
            assert rgba[48, 0, 3] == 0 and rgba[48, 72, 3] == 1
        else:
            assert all(QImage(str(path)).size().toTuple() == (request.width, request.height) for path in paths)


@pytest.mark.native
@pytest.mark.integration
def test_corrupt_scaled_intermediate_refused_before_publication(recorder_video, monkeypatch):
    from mediaforge.infrastructure.ffmpeg.child_process import ChildProcesses

    runner, item, snapshot = recorder_video
    item.output.kind, item.output.container, item.output.filename = 'frame', 'png', 'tampered'
    item.resize.enabled, item.resize.mode, item.resize.scale = True, 'scale', '0.5'
    request = capture(item, snapshot=snapshot)
    original = ChildProcesses.run
    def alter_intermediate(self, command, *args, **kwargs):
        result = original(self, command, *args, **kwargs)
        if str(command[-1]).endswith('codec-source_%06d.png'):
            path = Path(command[-1]).with_name('codec-source_000000.png')
            image = QImage(str(path)).convertToFormat(QImage.Format_RGB888)
            image.setPixelColor(0, 0, QColor('white') if image.pixelColor(0, 0) != QColor('white') else QColor('black'))
            assert image.save(str(path))
        return result
    monkeypatch.setattr(ChildProcesses, 'run', alter_intermediate)
    with pytest.raises(ExportError, match='Codec input does not match') as caught:
        runner.execute(request, ExportControl())
    assert caught.value.code == 'pixel_validation_failed'
    assert not list(request.destination.parent.iterdir())


@pytest.mark.native
@pytest.mark.integration
def test_scaled_high_depth_video_keeps_16bit_intermediate(tmp_path):
    import imagecodecs
    from mediaforge.application.contracts.image_export import PictureSnapshot
    from mediaforge.application.contracts.source_timeline import SourceSignature, TimelineBuildControl, TimelineRequest
    from mediaforge.application.services.media import OpenMediaService
    from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe
    from mediaforge.infrastructure.ffmpeg.source_timeline import FfprobeTimelineIndexer

    runner = executor()
    runtime = runner._runtime
    source = tmp_path/'prores-10bit.mov'
    subprocess.run([str(runtime.ffmpeg), '-v', 'error', '-n', '-f', 'lavfi', '-i', 'testsrc2=s=160x96:r=25:d=0.12',
                    '-c:v', 'prores_ks', '-profile:v', '3', '-pix_fmt', 'yuv422p10le', '-threads', '1',
                    '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', str(source)],
                   check=True, capture_output=True, timeout=15)
    metadata = OpenMediaService(FfprobeMediaMetadataProbe(runtime.ffprobe)).open_media(str(source)).metadata
    assert metadata and metadata.info_facts.pixel_format == 'yuv422p10le'
    signature = SourceSignature.capture(source)
    result = FfprobeTimelineIndexer(runtime.ffprobe).build(TimelineRequest('prores', signature, 1), TimelineBuildControl())
    assert result.timeline, result.error
    snapshot = PictureSnapshot(signature, result.timeline, 1, 1, 1, 0)
    item = MediaItem.create(metadata)
    item.resize.enabled, item.resize.mode, item.resize.scale = True, 'scale', '0.5'
    item.output.kind, item.output.container, item.output.image_depth, item.output.filename = 'frame', 'png', '16', 'high-depth'
    published = runner.execute(capture(item, snapshot=snapshot), ExportControl()).output
    pixels = imagecodecs.png_decode(published.read_bytes())
    assert pixels.shape == (48, 80, 3) and pixels.dtype.name == 'uint16'
    assert (pixels % 257).any()


@pytest.mark.native
@pytest.mark.integration
def test_render_pressure_still_refuses_presentation_but_not_cpu_export(tmp_path):
    runner = executor()
    service = runner._image_service
    request = capture(make_item(tmp_path))
    service.resources.sample = lambda: Resources().sample() | {'dedicated': service.render_reservation + 4096}
    with pytest.raises(ValueError, match='Qt renderer pressure'):
        service.check_pressure()
    assert runner.execute(request, ExportControl()).output.exists()
    assert runner.last_image_metrics['children'] > 0


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('host_available', [True, False])
def test_video_picture_export_checks_host_before_any_child(recorder_video, monkeypatch, host_available):
    from mediaforge.infrastructure.ffmpeg.preview_job import PreviewJobs

    runner, item, snapshot = recorder_video
    service = runner._image_service
    item.output.kind, item.output.container = 'frame', 'png'
    request = capture(item, snapshot=snapshot)
    launches = []
    factory = PreviewJobs.__call__

    def spawn(self, *args, **kwargs):
        launches.append(args[0])
        return factory(self, *args, **kwargs)

    monkeypatch.setattr(PreviewJobs, '__call__', spawn)
    def no_renderer():
        pytest.fail('Software export must not require GPU telemetry')
    service.resources.sample = no_renderer
    if not host_available:
        service.resources.sample_host = lambda: Resources().sample() | {'available_commit': 0}
    control = ExportControl()
    if host_available:
        result = runner.execute(request, control)
        assert result.output.exists() and launches and control.published
    else:
        with pytest.raises(ValueError, match='RAM/commit'):
            runner.execute(request, control)
        assert not launches and not list(request.destination.parent.iterdir())


@pytest.mark.native
@pytest.mark.integration
def test_exact_trim_vfr_sequence_members_and_movie(recorder_video):
    from mediaforge.application.services.export import capture as capture_movie
    from mediaforge.application.state.workspace import TrimPictureBoundary

    runner, item, snapshot = recorder_video
    stamps = snapshot.timeline.timestamps
    assert stamps[0] > 0 and (stamps[2] + snapshot.offset_us) % 1000
    assert len(set(b-a for a, b in zip(stamps, stamps[1:]))) > 1
    def boundary(ordinal):
        pts = stamps[ordinal]
        return TrimPictureBoundary(snapshot.signature, pts, pts + snapshot.offset_us)
    item.trim.enabled = True
    item.trim.in_picture = boundary(2)
    item.trim.out_picture = boundary(4)
    item.trim.in_ms = item.trim.in_picture.player_us // 1000
    item.trim.out_ms = (item.trim.out_picture.player_us + 999) // 1000
    item.output.kind, item.output.container, item.output.filename = 'sequence', 'png', 'trimmed'
    request = capture(item, snapshot=snapshot)
    assert (request.start, request.end) == (2, 4)
    sequence = runner.execute(request, ExportControl()).output
    for offset, ordinal in enumerate((2, 3)):
        item.output.kind, item.output.filename = 'frame', f'ordinal-{ordinal}'
        direct = runner.execute(capture(item, snapshot=replace(snapshot, ordinal=ordinal)), ExportControl()).output
        assert QImage(str(sequence/request.numbering.name(offset))) == QImage(str(direct))
    item.output.kind, item.output.container, item.output.codec = 'media', 'mp4', 'h264'
    item.output.filename = 'same-exact-trim'
    movie_request = capture_movie(item)
    assert movie_request.trim_pts_us == (stamps[2], stamps[4])
    movie = runner.execute(movie_request, ExportControl()).output
    runtime = runner._runtime
    command = [str(runtime.ffprobe), '-v', 'error', '-select_streams', 'v:0', '-show_frames',
               '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json']
    result = json.loads(subprocess.check_output(command + [str(movie)], timeout=15))['frames']
    assert len(result) == 2
    assert abs(Fraction(result[1]['best_effort_timestamp_time']) - Fraction(stamps[3]-stamps[2], 1_000_000)) <= Fraction(1, 1_000_000)


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('allowed', [False, True])
def test_software_av_copy_owns_host_and_preserves_bytes(recorder_video, monkeypatch, allowed):
    from mediaforge.application.services.export import capture as capture_movie
    from mediaforge.infrastructure.ffmpeg.preview_job import PreviewJobs

    runner, item, _ = recorder_video
    item.output.kind, item.output.container, item.output.codec = 'media', 'source', 'source'
    request = capture_movie(item)
    if allowed:
        source = request.source.read_bytes()
        result = runner.execute(request, ExportControl())
        assert result.output.read_bytes() == request.source.read_bytes() == source
    else:
        runner._image_service.resources.sample_host = lambda: Resources().sample() | {'available_commit': 0}
        monkeypatch.setattr(PreviewJobs, '__call__', lambda *a, **k: pytest.fail('Worker launched after host refusal'))
        with pytest.raises(ValueError, match='RAM/commit'):
            runner.execute(request, ExportControl())
        assert not request.destination.exists()


@pytest.mark.native
@pytest.mark.integration
def test_native_10001_picture_sequence_is_complete(tmp_path):
    from mediaforge.application.contracts.image_export import PictureSnapshot
    from mediaforge.application.contracts.source_timeline import SourceSignature, TimelineBuildControl, TimelineRequest
    from mediaforge.application.services.media import OpenMediaService
    from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe
    from mediaforge.infrastructure.ffmpeg.source_timeline import FfprobeTimelineIndexer
    from mediaforge.infrastructure.filesystem.image_capacity import required_bytes
    from time import monotonic

    runner=executor(); runtime=runner._runtime
    source=tmp_path/'Кадры 10001.mp4'
    subprocess.run([str(runtime.ffmpeg),'-v','error','-n','-f','lavfi','-i','testsrc2=s=16x16:r=60',
                    '-frames:v','10001','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(source)],
                   check=True,capture_output=True,timeout=30)
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    metadata=OpenMediaService(FfprobeMediaMetadataProbe(runtime.ffprobe)).open_media(str(source)).metadata
    assert metadata is not None
    signature=SourceSignature.capture(source)
    index=FfprobeTimelineIndexer(runtime.ffprobe).build(TimelineRequest('large',signature,1),TimelineBuildControl())
    assert index.timeline is not None and index.timeline.count==10001, index.error
    item=MediaItem.create(metadata)
    item.output.kind='sequence';item.output.container='png';item.output.sequence_start='0000000001'
    snapshot=PictureSnapshot(signature,index.timeline,None,1,1,0)
    request=capture(item,snapshot=snapshot)
    assert request.count==10001 and request.start==0
    stages=[]; times=[]; started=monotonic()
    control=ExportControl(lambda stage,_: (stages.append(stage),times.append(monotonic()-started)))
    result=runner.execute(request,control)
    expected={request.numbering.name(i) for i in range(10001)}
    assert len(expected)==10001 and expected=={p.name for p in result.output.iterdir()}
    assert [f.name for f in result.identity.files]==[request.numbering.name(i) for i in range(10001)]
    first,last=request.numbering.name(0),request.numbering.name(10000)
    assert (first,last)==('Кадры 10001.0000000001.png','Кадры 10001.0000010001.png')
    assert QImage(str(result.output/first)).size().toTuple()==(16,16)
    assert QImage(str(result.output/last)).size().toTuple()==(16,16)
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    assert not list(tmp_path.glob('.mediaforge-*'))
    receipt=dict(count=len(expected),first=first,last=last,source_sha256=before,seconds=monotonic()-started,
                 stages=list(zip(stages,times)),logical_bytes=sum(f.size for f in result.identity.files),
                 disk_forecast=required_bytes(request),metrics=runner.last_image_metrics)
    (tmp_path/'qualification.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('container',['png','tiff','jpeg','webp'])
def test_native_still_source_pixels_and_input_immutability(tmp_path,container):
    item=make_item(tmp_path);source=Path(item.metadata.source_path);before=source.read_bytes()
    item.output.container=container;item.output.filename=container;item.output.matte='white' if container=='jpeg' else '';item.output.webp_ack=True
    result=executor().execute(capture(item),ExportControl());image=QImage(str(result.output));assert (image.width(),image.height())==(128,96)
    sample=tmp_path/'sample.rgba';runtime=executor()._runtime
    subprocess.run([str(runtime.ffmpeg),'-v','error','-n','-i',str(result.output),'-vf','crop=1:1:50:50:exact=1,format=rgba','-frames:v','1','-f','rawvideo',str(sample)],check=True,capture_output=True,timeout=15)
    actual=tuple(sample.read_bytes());expected=(227,147,167,255) if container=='jpeg' else (200,40,80,128)
    assert max(abs(a-b) for a,b in zip(actual,expected))<=(3 if container=='jpeg' else 0),(actual,expected)
    assert source.read_bytes()==before and not list(Path(item.output.folder).glob('.mediaforge-*'))


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('fmt',['png','jpg','tiff'])
@pytest.mark.parametrize('rate',['24000/1001','30000/1001'])
def test_native_sequence_movie_exact_rate_trim_and_pixels(tmp_path,fmt,rate):
    item=make_sequence(tmp_path,fmt);item.sequence.rate=Fraction(rate);item.trim.enabled=True;item.sequence.in_slot=1;item.sequence.out_slot=3
    item.output.filename='movie';item.output.container='mp4';item.output.codec='h264'
    result=executor().execute(capture(item),ExportControl());assert result.output.exists()
    decoded=tmp_path/'decoded.png';runtime=executor()._runtime
    subprocess.run([str(runtime.ffmpeg),'-v','error','-n','-i',str(result.output),'-frames:v','1',str(decoded)],check=True,capture_output=True,timeout=15)
    assert max(abs(a-b) for a,b in zip(QImage(str(decoded)).pixelColor(20,20).getRgb()[:3],(200,70,80)))<=4


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('profile',range(6))
def test_native_prores_rgb_adapter_profiles(tmp_path,profile):
    item=make_sequence(tmp_path);item.output.filename='prores';item.output.container='mov';item.output.codec='prores';item.output.prores_quality=profile
    result=executor().execute(capture(item),ExportControl());assert result.output.exists()
    if profile>=4:
        sample=tmp_path/'opaque.rgba';runtime=executor()._runtime
        subprocess.run([str(runtime.ffmpeg),'-v','error','-n','-i',str(result.output),'-vf','crop=1:1:20:20:exact=1,format=rgba','-frames:v','1','-f','rawvideo',str(sample)],check=True,capture_output=True,timeout=15)
        actual=sample.read_bytes();assert len(actual)==4 and actual[3]==255 and max(abs(a-b) for a,b in zip(actual,(200,40,80)))<=2


@pytest.mark.native
@pytest.mark.integration
def test_native_directory_collision_cancel_and_cleanup(tmp_path):
    item=make_sequence(tmp_path);item.output.kind='sequence';item.output.container='png';item.output.filename='pictures'
    request=capture(item);runtime=executor();result=runtime.execute(request,ExportControl())
    assert [p.name for p in sorted(result.output.iterdir())]==['pictures.0001.png','pictures.0002.png','pictures.0003.png']
    old={p.name:p.read_bytes() for p in result.output.iterdir()}
    with pytest.raises(ExportError):runtime.execute(request,ExportControl())
    assert {p.name:p.read_bytes() for p in result.output.iterdir()}==old
    item.output.filename='cancel';request=capture(item);control=ExportControl(lambda stage,message:control.cancel() if stage=='encode' else None)
    with pytest.raises(ExportError,match='cancel'):runtime.execute(request,control)
    assert not request.destination.exists() and not list(Path(item.output.folder).glob('.mediaforge-*'))


@pytest.mark.native
@pytest.mark.integration
def test_native_source_write_denial_and_before_publication_collision(tmp_path):
    item=make_item(tmp_path);request=capture(item);source=Path(item.metadata.source_path);denied=[]
    def observe(stage,message):
        if stage=='encode':
            try:
                with source.open('r+b'):pass
            except PermissionError:denied.append(True)
        if stage=='publication':request.destination.write_bytes(b'opponent')
    with pytest.raises(ExportError):executor().execute(request,ExportControl(observe))
    assert denied==[True] and request.destination.read_bytes()==b'opponent'
    assert not list(Path(item.output.folder).glob('.mediaforge-*'))


@pytest.mark.native
@pytest.mark.integration
def test_cleanup_failure_retains_output_and_poisoned_shared_gate(tmp_path,monkeypatch):
    import time
    from mediaforge.infrastructure.filesystem.image_output import ImageOutput
    from mediaforge.application.services.export import ExportService
    from mediaforge.application.services.processing_gate import MediaProcessingGate
    held={};original=ImageOutput._delete_owned_directory
    def fail(output,handle):
        held['output']=output
        output._retain('Controlled owned-directory cleanup failure')
    monkeypatch.setattr(ImageOutput,'_delete_owned_directory',fail)
    gate=MediaProcessingGate();service=ExportService(executor(),gate=gate)
    assert service.start_request(capture(make_item(tmp_path)))
    end=time.monotonic()+15
    while service.busy and time.monotonic()<end:time.sleep(.01)
    assert not service.busy and service.snapshot().error=='lifecycle_failed'
    assert gate.busy and gate.failure and held['output'] in service._control.retained_resources
    with pytest.raises(RuntimeError,match='cleanup'):service.shutdown()
    # Remove only the injected fault, then reconcile these newly owned test
    # handles. The poisoned application gate remains poisoned permanently.
    monkeypatch.setattr(ImageOutput,'_delete_owned_directory',original)
    held['output']._cleanup();assert gate.failure


def test_unsafe_original_tier_refused_before_child_creation(tmp_path,monkeypatch):
    from mediaforge.application.contracts.image_export import ImageExportRequest
    item=make_item(tmp_path);request=replace(capture(item),source_width=16000,source_height=16000)
    class Runtime:ffmpeg=Path('not-started');ffprobe=Path('not-started')
    def forbidden(*a,**kw):raise AssertionError('Unsafe decoder started')
    from mediaforge.infrastructure.ffmpeg.preview_job import PreviewJobs
    monkeypatch.setattr(PreviewJobs,'__call__',forbidden)
    service=ImageSourceService(QtImageReader(),resources=Resources())
    runner=FfmpegExportExecutor(Runtime(),image_service=service,process_factory=forbidden)
    with pytest.raises(ExportError,match='processing plan'):runner.execute(request,ExportControl())
    assert service.budget.reserved==service.budget.overhead


@pytest.mark.native
@pytest.mark.integration
def test_video_vfr_bframes_nonzero_pts_current_and_numbered_formats(tmp_path):
    from array import array
    from mediaforge.application.contracts.image_export import PictureSnapshot
    from mediaforge.application.contracts.source_timeline import SourceSignature,SourcePresentationTimeline
    from mediaforge.application.services.media import OpenMediaService
    from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe
    runtime=executor()._runtime;source=tmp_path/'vfr B кадры.mp4'
    subprocess.run([str(runtime.ffmpeg),'-v','error','-n','-f','lavfi','-i','testsrc2=s=128x96:r=25:d=0.4','-vf',"select='not(eq(mod(n,3),1))',setpts=PTS+2/TB",'-fps_mode','vfr','-c:v','libx264','-bf','3','-pix_fmt','yuv420p',str(source)],check=True,capture_output=True,timeout=15)
    frames=json.loads(subprocess.check_output([str(runtime.ffprobe),'-v','error','-select_streams','v:0','-show_frames','-show_entries','frame=best_effort_timestamp_time','-of','json',str(source)],timeout=15))['frames']
    stamps=[round(Fraction(f['best_effort_timestamp_time'])*1_000_000) for f in frames]
    assert stamps[0]>0 and len(set(b-a for a,b in zip(stamps,stamps[1:])))>1
    timeline=SourcePresentationTimeline.from_timestamps(array('q',stamps),40000)
    metadata=OpenMediaService(FfprobeMediaMetadataProbe(runtime.ffprobe)).open_media(str(source)).metadata
    assert metadata is not None;item=MediaItem.create(metadata)
    output=tmp_path/'result';output.mkdir();item.output.folder=str(output)
    snapshot=PictureSnapshot(SourceSignature.capture(source),timeline,2,7,12,-stamps[0])
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    from mediaforge.application.contracts.export import output_aspect
    assert output_aspect('16:9',1280,1024,'1:1')==Fraction(64,45)
    item.output.kind='frame';item.output.container='png';item.output.filename='square-recovery'
    item.resize.enabled=True;item.resize.mode='format';item.resize.width=1280;item.resize.height=1024
    item.resize.pixel_aspect='16:9'
    baked=describe(item,snapshot)
    assert baked['valid'] and 'recipe SAR 64:45' in baked['message']
    request=capture(item,snapshot=snapshot)
    assert (request.width,request.height,request.sar)==(1820,1024,Fraction(1))
    assert QImage(str(executor().execute(request,ExportControl()).output)).size().toTuple()==(1820,1024)
    item.output.filename='square-reference'
    item.resize.pixel_aspect='square'
    request=capture(item,snapshot=snapshot)
    assert (request.width,request.height,request.sar,request.start,request.end)==(1280,1024,Fraction(1),2,3)
    recovered=executor().execute(request,ExportControl()).output
    assert QImage(str(recovered)).size().toTuple()==(1280,1024)
    item.resize.enabled=False
    for container in ('png','jpeg','tiff'):
        item.output.kind='frame';item.output.container=container;item.output.filename='current-'+container
        request=capture(item,snapshot=snapshot);assert (request.start,request.end)==(2,3)
        assert executor().execute(request,ExportControl()).output.exists()
        item.output.kind='sequence';item.output.filename='range-'+container;item.trim.enabled=True
        item.trim.in_ms=(stamps[1]-stamps[0])//1000;item.trim.out_ms=(stamps[4]-stamps[0])//1000
        request=capture(item,snapshot=snapshot);assert (request.start,request.end)==(1,4)
        result=executor().execute(request,ExportControl());assert len(list(result.output.iterdir()))==3
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before


@pytest.mark.native
@pytest.mark.integration
def test_native_rgba_edge_export_png_tiff_webp(tmp_path):
    from tests.unit.test_png_layouts import png
    item=make_item(tmp_path);path=Path(item.metadata.source_path)
    alphas=(0,1,64,128,254,255)
    row=b''.join(bytes((200,30,90,a)) for a in alphas)
    png(path,6,2,8,6,[row,row]);facts=QtImageReader().probe(str(path))
    item=MediaItem.create(replace(item.metadata,width=6,height=2,source=facts));item.output.folder=str(tmp_path/'outputs');item.output.webp_ack=True
    for container in ('png','tiff','webp'):
        item.output.container=container;item.output.filename='witness-'+container
        result=executor().execute(capture(item),ExportControl());sample=tmp_path/(container+'.rgba');runtime=executor()._runtime
        subprocess.run([str(runtime.ffmpeg),'-v','error','-n','-i',str(result.output),'-frames:v','1','-pix_fmt','rgba','-f','rawvideo',str(sample)],check=True,capture_output=True,timeout=15)
        actual=sample.read_bytes();assert len(actual)==6*2*4
        for i,a in enumerate(alphas):
            assert actual[i*4+3]==a
            if a or container!='webp':assert actual[i*4:i*4+3]==bytes((200,30,90))


@pytest.mark.native
@pytest.mark.integration
def test_native_geometry_sar_compressions_and_jpeg_mapping(tmp_path):
    item=make_sequence(tmp_path);item.output.codec='h264';item.output.container='mov';item.output.filename='geometry'
    item.crop.enabled=True;item.crop.x=4;item.crop.y=6;item.crop.width=96;item.crop.height=64
    item.resize.enabled=True;item.resize.mode='format';item.resize.width=64;item.resize.height=48;item.resize.pixel_aspect='4/3'
    item.output.matte='black'
    request=capture(item);assert (request.width,request.height,request.sar)==(64,48,Fraction(4,3))
    assert executor().execute(request,ExportControl()).output.exists()
    item=make_item(tmp_path,alpha=False);item.crop.enabled=True;item.crop.x=4;item.crop.y=6;item.crop.width=96;item.crop.height=64
    item.resize.enabled=True;item.resize.mode='scale';item.resize.scale='0.5'
    for container,field,values in [('png','png_compression',(0,6,9)),('tiff','tiff_compression',('raw','lzw','deflate'))]:
        for value in values:
            item.output.container=container;item.output.filename=container+'-'+str(value);setattr(item.output,field,value)
            request=capture(item);assert (request.width,request.height)==(48,32)
            assert executor().execute(request,ExportControl()).output.exists()
    source=Path(item.metadata.source_path);image=QImage(128,96,QImage.Format_RGB32)
    for y in range(96):
        for x in range(128):image.setPixelColor(x,y,QColor((x*7+y*3)%256,(x*3+y*5)%256,(x+y*2)%256))
    assert image.save(str(source));facts=QtImageReader().probe(str(source));item.metadata=replace(item.metadata,source=facts)
    item.crop.enabled=False;item.resize.enabled=False;item.output.container='jpeg';sizes=[]
    for quality in (1,50,100):
        item.output.jpeg_quality=quality;item.output.filename='quality-'+str(quality)
        result=executor().execute(capture(item),ExportControl());sizes.append(result.output.stat().st_size)
    assert sizes[0]<sizes[1]<sizes[2],sizes


@pytest.mark.native
@pytest.mark.integration
def test_native_odd_image_crop_custom_and_transparent_fit(tmp_path):
    item=make_item(tmp_path,alpha=False);item.output.container='png';item.output.filename='odd'
    item.crop.enabled=True;item.crop.x=1;item.crop.y=3;item.crop.width=31;item.crop.height=17
    item.resize.enabled=True;item.resize.mode='format';item.resize.width=17;item.resize.height=19;item.resize.placement='fit'
    request=capture(item);assert (request.width,request.height)==(17,19)
    result=executor().execute(request,ExportControl());image=QImage(str(result.output))
    assert image.width()==17 and image.height()==19 and image.pixelColor(8,0).alpha()==0 and image.pixelColor(8,9).alpha()==255


@pytest.mark.native
@pytest.mark.integration
def test_native_literal_numbering_unicode_folder_and_parent_ownership(tmp_path):
    from mediaforge.infrastructure.filesystem.image_output import ImageOutput
    item=make_sequence(tmp_path);item.output.kind='sequence';item.output.container='png';item.output.filename='выход jpeg 雪'
    item.output.filename='выход % 雪';item.output.sequence_start='007'
    request=capture(item)
    with ImageOutput(request,None) as owned:
        with pytest.raises(PermissionError):owned.directory.rename(owned.directory.with_name('unowned'))
        with pytest.raises(PermissionError):owned.staged.rename(owned.staged.with_name('swapped'))
    result=executor().execute(request,ExportControl())
    assert result.output==request.destination and sorted(p.name for p in result.output.iterdir())==['выход % 雪.007.png','выход % 雪.008.png','выход % 雪.009.png']
    assert not list(Path(item.output.folder).glob('.mediaforge-*'))


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('stage',['decode','codec'])
def test_native_cancel_with_owned_child_reaps_before_cleanup(tmp_path,monkeypatch,stage):
    from mediaforge.application.services.export import ExportService
    from mediaforge.application.services.processing_gate import MediaProcessingGate
    import time
    from mediaforge.infrastructure.ffmpeg.preview_job import PreviewJobs
    runner=executor();factory=PreviewJobs.__call__;children=[];entered=threading.Event()
    def spawn(self,*a,**kw):
        child=factory(self,*a,**kw);children.append(child)
        if stage=='decode' or any(str(part).endswith('encode_worker.py') for part in a[0]):entered.set()
        return child
    monkeypatch.setattr(PreviewJobs,'__call__',spawn);item=make_item(tmp_path)
    if stage=='codec':item.output.container='tiff';item.output.image_depth='float'
    request=capture(item);gate=MediaProcessingGate();service=ExportService(runner,gate=gate)
    assert service.start_request(request) and entered.wait(5) and service.cancel()
    end=time.monotonic()+6
    while service.busy and time.monotonic()<end:time.sleep(.01)
    assert not service.busy and service.snapshot().state=='Cancelled' and not gate.busy
    assert all(p.poll() is not None for p in children) and not request.destination.exists()
    assert not list(Path(item.output.folder).glob('.mediaforge-*'));service.shutdown()


@pytest.mark.native
@pytest.mark.integration
def test_native_corrupt_final_member_is_not_published(tmp_path):
    item=make_sequence(tmp_path);item.output.kind='sequence';item.output.container='png';item.output.filename='corrupt'
    request=capture(item)
    def corrupt(stage,message):
        if stage=='validation':
            stages=list(Path(item.output.folder).glob('.mediaforge-export-*'));assert len(stages)==1
            (stages[0]/'frames'/request.numbering.name(request.count-1)).write_bytes(b'bad output')
    with pytest.raises((ExportError,ValueError)):executor().execute(request,ExportControl(corrupt))
    assert not request.destination.exists() and not list(Path(item.output.folder).glob('.mediaforge-*'))


@pytest.mark.native
@pytest.mark.integration
def test_native_tiff_all_orientations_keep_source_pixels_and_coordinates(tmp_path):
    import struct
    item=make_item(tmp_path,fmt='tiff');source=Path(item.metadata.source_path)
    image=QImage(32,24,QImage.Format_RGBA8888)
    for y in range(24):
        for x in range(32):image.setPixelColor(x,y,QColor((x*7)%256,(y*9)%256,(x+y*3)%256,(0,128,255)[(x//8)%3]))
    assert image.save(str(source));original=source.read_bytes();order='<' if original[:2]==b'II' else '>'
    offset=struct.unpack_from(order+'I',original,4)[0];count=struct.unpack_from(order+'H',original,offset)[0]
    orientation=next(offset+2+i*12+8 for i in range(count) if struct.unpack_from(order+'H',original,offset+2+i*12)[0]==274)
    runtime=executor()._runtime;reference=tmp_path/'coded.rgba'
    subprocess.run([str(runtime.ffmpeg),'-v','error','-n','-noautorotate','-i',str(source),'-frames:v','1','-pix_fmt','rgba','-f','rawvideo',str(reference)],check=True,capture_output=True,timeout=15)
    coded=reference.read_bytes();assert len(coded)==32*24*4
    for value in range(1,9):
        data=bytearray(original);struct.pack_into(order+'H',data,orientation,value);source.write_bytes(data)
        facts=QtImageReader().probe(str(source));w,h=(24,32) if value>=5 else (32,24)
        item.metadata=replace(item.metadata,width=w,height=h,source=facts);item.output.container='png';item.output.filename='orientation-'+str(value)
        result=executor().execute(capture(item),ExportControl());raw=tmp_path/(str(value)+'.rgba')
        subprocess.run([str(runtime.ffmpeg),'-v','error','-n','-i',str(result.output),'-frames:v','1','-pix_fmt','rgba','-f','rawvideo',str(raw)],check=True,capture_output=True,timeout=15)
        expected=bytearray()
        for y in range(h):
            for x in range(w):
                sx,sy={1:(x,y),2:(31-x,y),3:(31-x,23-y),4:(x,23-y),5:(y,x),6:(y,23-x),7:(31-y,23-x),8:(31-y,x)}[value]
                expected+=coded[(sy*32+sx)*4:(sy*32+sx+1)*4]
        assert raw.read_bytes()==expected and source.read_bytes()==data
