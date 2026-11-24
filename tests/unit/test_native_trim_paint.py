"""Small pixel-level and failure controls; no decoder/native window required."""
import hashlib
import importlib
import json
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QPointF, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QImage

from tests import native_trim_paint as paint


def patterned_image(width=11, height=7):
    # Include invisible RGB, unequal channels, alpha extremes and partial alpha.
    raw = bytes(value for y in range(height) for x in range(width)
                for value in ((x*31+y*7)%256, (x*13+y*47)%256,
                              (255-x*17-y*3)%256, (0,1,127,254,255)[(x+y)%5]))
    return QImage(raw, width, height, width*4, QImage.Format_RGBA8888).copy(), raw


class Window(QObject):
    beforeSynchronizing = Signal()
    beforeRendering = Signal()
    afterRendering = Signal()
    frameSwapped = Signal()

    def __init__(self, image):
        super().__init__()
        self.image = image
        self.grabs = 0

    def grabWindow(self):
        self.grabs += 1
        self.beforeSynchronizing.emit()
        self.beforeRendering.emit()
        self.afterRendering.emit()
        self.frameSwapped.emit()
        return self.image.copy()

    def devicePixelRatio(self):
        return 1.5


class Video:
    def property(self, name):
        assert name == 'contentRect'
        return QRectF(1, 1, 5, 3)

    def mapToScene(self, point):
        return point


def oracle_at(tmp_path, monkeypatch, mode='png-only'):
    if mode is None:
        monkeypatch.delenv(paint.STORAGE_ENV, raising=False)
    else:
        monkeypatch.setenv(paint.STORAGE_ENV, mode)
    image, _ = patterned_image()
    window = Window(image)
    playback = SimpleNamespace(_source_generation=7, _player_generation=12,
        _player=object(), _staging_sink=object(), _presentation_sink=object(),
        _presented_start=400000, frameText='Frame 11 / 100', _trim_in=400,
        _trim_out=800, trimLoopActive=True, _loop_seeking=False)
    return paint.ScenePaintOracle(window, Video(), playback, tmp_path/'Кириллица space Ω')


def rows(oracle):
    return [json.loads(line) for line in (oracle.directory/'readbacks.jsonl').read_text(
        encoding='utf8').splitlines()]


def expected_regions(oracle):
    image = oracle.window.image
    # Independent fixed expectations for the supplied fractional-DPR geometry.
    return {'full':image, 'video':image.copy(2,2,8,4), 'proof':image.copy(3,3,4,2)}


@pytest.mark.parametrize('mode', ['raw+png', 'png-only'])
def test_scene_proof_maps_transformed_extents_and_dpr(tmp_path, monkeypatch, mode):
    oracle = oracle_at(tmp_path, monkeypatch, mode)
    image, _ = patterned_image(50, 40)
    oracle.window.image = image
    class TransformedVideo(Video):
        def mapToScene(self, point):
            return QPointF(point.x() * 2 + 2, point.y() * 3 + 1)
    oracle.video = TransformedVideo()
    digest = oracle.capture('reference', 400000)
    row, = rows(oracle)
    assert row['geometry'] == {'video':[6,6,15,14], 'proof':[10,9,8,7]}
    proof = image.copy(10,9,8,7)
    assert digest == hashlib.sha256(bytes(proof.constBits())).hexdigest()
    assert row['images']['video']['size'] == [15,14]
    assert row['images']['proof']['size'] == [8,7]


@pytest.mark.parametrize('mode', [None, 'raw+png', 'png-only'])
def test_all_regions_exact_pixels_legacy_hashes_and_facts(tmp_path, monkeypatch, mode):
    oracle = oracle_at(tmp_path, monkeypatch, mode)
    expected = expected_regions(oracle)
    digest = oracle.capture('reference', 400000)
    row, = rows(oracle)
    assert digest == hashlib.sha256(bytes(expected['proof'].constBits())).hexdigest()
    assert oracle.references == {digest:400000}
    assert oracle.window.grabs == 1
    assert row['before'] == dict.fromkeys(oracle.spies, 0)
    assert row['after'] == dict.fromkeys(oracle.spies, 1)
    assert row['readback_revision'] == 1 and row['reference_timestamp_us'] == 400000
    assert row['source_generation'] == 7 and row['player_generation'] == 12
    for name, attr in [('player','_player'), ('staging','_staging_sink'), ('viewer','_presentation_sink')]:
        assert row[name+'_identity'] == id(getattr(oracle.playback, attr))
    assert row['presentation_us'] == 400000 and row['frame_label'] == 'Frame 11 / 100'
    assert row['range_ms'] == [400,800] and row['armed'] and not row['loop_seeking']
    assert row['dpr'] == 1.5 and row['geometry'] == {'video':[2,2,8,4], 'proof':[3,3,4,2]}
    assert row['storage']['version'] == 1 and row['storage']['status'] == 'complete'
    assert row['storage']['mode'] == (mode or 'raw+png') and not row['storage']['errors']
    assert set(row['images']) == set(expected)
    for name, original in expected.items():
        info = row['images'][name]
        path = oracle.directory/info['png']
        assert path.name == f'reference-0001-{name}.png'
        encoded = path.read_bytes()
        decoded = QImage(str(path)).convertToFormat(QImage.Format_RGBA8888)
        raw = bytes(original.constBits())
        assert decoded.size() == original.size() and bytes(decoded.constBits()) == raw
        assert info['raw_sha256'] == hashlib.sha256(raw).hexdigest()
        assert info['png_sha256'] == hashlib.sha256(encoded).hexdigest()
        assert info['raw_sha256'] != info['png_sha256']
        assert info['size'] == [original.width(), original.height()]
        assert info['format'] == 'RGBA8888' and info['bytes_per_line'] == original.width()*4
        assert info['byte_count'] == len(raw) and info['png_byte_count'] == len(encoded)
        assert info['serialization_ns'] > 0 and info['roundtrip_ns'] > 0
        assert info['roundtrip_verified'] == (mode == 'png-only')
        assert info['raw_present'] == (mode != 'png-only')
        if mode != 'png-only':
            assert path.with_suffix('.rgba').read_bytes() == raw
        else:
            assert not path.with_suffix('.rgba').exists()


@pytest.mark.parametrize('width,height', [(1,1), (3,5), (17,2)])
def test_awkward_pixels_roundtrip_and_padding(tmp_path, width, height):
    image, raw = patterned_image(width, height)
    stride = width*4+12
    padded = b''.join(raw[y*width*4:(y+1)*width*4]+b'\xee'*12 for y in range(height))
    external = QImage(padded, width, height, stride, QImage.Format_RGBA8888)
    canonical, actual = paint.canonical_rgba(external)
    assert actual == raw and canonical.bytesPerLine() == width*4
    path = tmp_path/'awkward.png'
    assert canonical.save(str(path))
    encoded = paint.verify_png(path, canonical, raw)
    assert encoded == path.read_bytes()


def test_rgb888_padded_conversion(tmp_path):
    raw = bytes([1,2,3, 101,102,103, 251,252,253, 77,78,79])
    image = QImage(raw, 3, 1, 12, QImage.Format_RGB888)
    canonical, actual = paint.canonical_rgba(image)
    assert actual == bytes([1,2,3,255,101,102,103,255,251,252,253,255])
    path = tmp_path/'rgb.png'
    assert canonical.save(str(path))
    paint.verify_png(path, canonical, actual)


@pytest.mark.parametrize('invalid', ['', 'PNG-only', ' png-only', 'png-only ', 'png', '0'])
def test_invalid_configuration_before_capture_or_allocation(tmp_path, monkeypatch, invalid):
    with pytest.raises(ValueError, match=paint.STORAGE_ENV):
        oracle_at(tmp_path, monkeypatch, invalid)
    assert not list(tmp_path.iterdir())


def test_mode_is_chosen_before_capture(tmp_path, monkeypatch):
    oracle = oracle_at(tmp_path, monkeypatch)
    monkeypatch.setenv(paint.STORAGE_ENV, 'invalid-later-change')
    oracle.capture('held-out')
    assert rows(oracle)[0]['storage']['mode'] == 'png-only'


@pytest.mark.parametrize('mode', ['raw+png', 'png-only'])
def test_failed_unmapped_caller_keeps_every_event_before_assertion(tmp_path, monkeypatch, mode):
    oracle = oracle_at(tmp_path, monkeypatch, mode)
    oracle.capture('reference', 400000)
    image, _ = patterned_image()
    image.setPixelColor(3,3, image.pixelColor(0,0))
    oracle.window.image = image
    with pytest.raises(AssertionError, match='unmapped painted scene'):
        digest = oracle.capture('failed-unmapped')
        assert digest in oracle.references, 'unmapped painted scene'
    records = rows(oracle)
    assert len(records) == 2 and oracle.window.grabs == 2
    for row in records:
        assert row['storage']['status'] == 'complete'
        assert set(row['images']) == {'full','video','proof'}
        assert all((oracle.directory/i['png']).exists() for i in row['images'].values())


PNG_FAULTS = ['save', 'read', 'decode', 'corrupt', 'missing', 'truncated',
    'wrong-size', 'wrong-pixel', 'reconstruction', 'hash', 'serialize', 'journal-open',
    'journal-write', 'journal-close']


@pytest.mark.parametrize('mode,fault', [('png-only', fault) for fault in PNG_FAULTS]
    + [('raw+png', fault) for fault in ('save','read','hash','serialize','journal-open',
                                       'journal-write','journal-close')])
def test_storage_faults_fail_closed_and_retain_all_originals(tmp_path, monkeypatch, mode, fault):
    oracle = oracle_at(tmp_path, monkeypatch, mode)
    originals = {name:bytes(image.constBits()) for name,image in expected_regions(oracle).items()}
    original_save, original_read, original_open = QImage.save, Path.read_bytes, Path.open
    original_canonical = paint.canonical_rgba
    if fault == 'save':
        def save(image, path, *args):
            if path.endswith('-full.png'):
                return False
            return original_save(image, path, *args)
        monkeypatch.setattr(QImage, 'save', save)
    elif fault in ('read','corrupt','missing','truncated','wrong-size','wrong-pixel'):
        def read(path):
            if path.name.endswith('-full.png'):
                if fault == 'read':
                    raise OSError('injected PNG read failure')
                if fault == 'missing':
                    path.unlink()
                    return original_read(path)
                if fault == 'corrupt':
                    path.write_bytes(b'not a PNG')
                elif fault == 'truncated':
                    path.write_bytes(original_read(path)[:25])
                else:
                    image, _ = patterned_image(1,1) if fault == 'wrong-size' else patterned_image()
                    if fault == 'wrong-pixel':
                        image.setPixelColor(0,0,image.pixelColor(1,1))
                    assert original_save(image, str(path), 'PNG')
            return original_read(path)
        monkeypatch.setattr(Path, 'read_bytes', read)
    elif fault == 'decode':
        monkeypatch.setattr(QImage, 'fromData', lambda *args:QImage())
    elif fault == 'reconstruction':
        count = 0
        def reconstruct(image):
            nonlocal count
            count += 1
            if count > 3:
                raise ValueError('injected reconstruction failure')
            return original_canonical(image)
        monkeypatch.setattr(paint, 'canonical_rgba', reconstruct)
    elif fault == 'hash':
        monkeypatch.setattr(paint.hashlib, 'sha256', lambda *args:(_ for _ in ()).throw(
            ValueError('injected hash failure')))
    elif fault == 'serialize':
        monkeypatch.setattr(paint.json, 'dumps', lambda *args,**kwargs:(_ for _ in ()).throw(
            ValueError('injected serialization failure')))
    else:
        class BrokenJournal:
            def __enter__(self):
                return self
            def write(self, value):
                if fault == 'journal-write':
                    raise OSError('injected journal write failure')
                return len(value)
            def __exit__(self, *args):
                if fault == 'journal-close':
                    raise OSError('injected journal close failure')
        def open_file(path, *args, **kwargs):
            if path.name == 'readbacks.jsonl':
                if fault == 'journal-open':
                    raise OSError('injected journal open failure')
                return BrokenJournal()
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', open_file)
    with pytest.raises(paint.CaptureStorageError, match='incomplete'):
        oracle.capture('reference', 400000)
    assert not oracle.references
    # The original bytes remain exact even when an encoded PNG is bad/missing.
    for name, raw in originals.items():
        assert original_read(oracle.directory/f'reference-0001-{name}.rgba') == raw
        recovery = oracle.directory/f'reference-0001-{name}.recovery.png'
        decoded = QImage(str(recovery)).convertToFormat(QImage.Format_RGBA8888)
        assert bytes(decoded.constBits()) == raw
    row = json.loads((oracle.directory/'capture-0001-incomplete.json').read_text(encoding='utf8'))
    assert row['storage']['status'] == 'incomplete' and row['storage']['errors']
    assert row['source_generation'] == 7 and row['player_generation'] == 12
    assert all(info['raw_present'] for info in row['images'].values())
    with pytest.raises(paint.CaptureStorageError):
        oracle.capture('after-error', 440000)
    assert oracle.window.grabs == 1 and not oracle.references


def test_snapshot_conversion_failure_keeps_original_layout(tmp_path, monkeypatch):
    oracle = oracle_at(tmp_path, monkeypatch)
    originals = expected_regions(oracle)
    monkeypatch.setattr(paint, 'canonical_rgba', lambda *args:(_ for _ in ()).throw(
        ValueError('injected conversion failure')))
    with pytest.raises(paint.CaptureStorageError, match='snapshot'):
        oracle.capture('conversion')
    row = json.loads((oracle.directory/'capture-0001-incomplete.json').read_text(encoding='utf8'))
    for name, image in originals.items():
        info = row['images'][name]
        assert info['raw_present'] and info['format'] == image.format().name
        assert (oracle.directory/f'conversion-0001-{name}.rgba').read_bytes() == bytes(image.constBits())
        assert (oracle.directory/info['recovery_png']).exists()
    assert not oracle.references


@pytest.mark.parametrize('layout', ['stride', 'format', 'byte-count'])
def test_reconstructed_layout_must_match_original(tmp_path, monkeypatch, layout):
    image, raw = patterned_image(3,2)
    path = tmp_path/'layout.png'
    assert image.save(str(path))
    if layout == 'stride':
        backing = raw[:12]+b'xxxx'+raw[12:]+b'xxxx'
        wrong = QImage(backing,3,2,16,QImage.Format_RGBA8888)
        reconstructed = raw
    elif layout == 'format':
        wrong = image.convertToFormat(QImage.Format_ARGB32)
        reconstructed = raw
    else:
        wrong, reconstructed = image, raw[:-1]
    monkeypatch.setattr(paint, 'canonical_rgba', lambda *args:(wrong,reconstructed))
    with pytest.raises(ValueError, match='pixel/layout mismatch'):
        paint.verify_png(path,image,raw)


@pytest.mark.parametrize('mode', ['raw+png', 'png-only'])
def test_unwritable_destination_reports_inability_to_persist(tmp_path, monkeypatch, mode):
    oracle = oracle_at(tmp_path, monkeypatch, mode)
    original_open = Path.open
    def open_file(path, *args, **kwargs):
        if path.parent == oracle.directory:
            raise OSError('injected exhausted/unwritable destination')
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', open_file)
    monkeypatch.setattr(QImage, 'save', lambda *args:False)
    with pytest.raises(paint.CaptureStorageError, match='incomplete sidecar.*unwritable'):
        oracle.capture('reference', 400000)
    assert not oracle.references
    assert not list(oracle.directory.iterdir())


@pytest.fixture
def signal_trim_case(tmp_path, monkeypatch):
    """Real Qt dispatch, unchanged caller predicates, tiny distinguishable pictures."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    # The native script imports this helper by its top-level name. Use the same
    # module as these tests so fault injection targets its real storage path.
    monkeypatch.setitem(sys.modules, 'native_trim_paint', paint)
    caller = importlib.import_module('reviewer_findings_native')
    app = QCoreApplication.instance() or QCoreApplication([])

    class Frame:
        def __init__(self, playback):
            self.playback = playback
        def isValid(self):
            return True
        def startTime(self):
            return self.playback._presented_start

    class Sink:
        def __init__(self, playback):
            self.playback = playback
        def videoFrame(self):
            return Frame(self.playback)

    class SignalPlayback(QObject):
        changed = Signal()
        def __init__(self):
            super().__init__()
            self._source_generation, self._player_generation = 1, 0
            self._player, self._staging_sink = object(), object()
            self._presentation_sink = Sink(self)
            self._presented_start, self.position = 0, 0
            self.frameText, self.state = '', 'Paused'
            self._trim_in, self._trim_out = 400, 800
            self.trimLoopActive = self.playing = self._loop_seeking = False
            self.delivered = []
        def seek(self, ms):
            self.position = ms
            self._presented_start = (ms//40)*40000
            self.frameText = f'Frame {self._presented_start//40000+1} / 100'
        def togglePlayback(self):
            self.playing = self.trimLoopActive = True
            self.state = 'Playing'
            def emit_returns():
                # Four completed presented returns and four good earlier paint
                # captures precede the last (fifth) boundary capture at rev 18.
                for timestamp in [400000,760000,400000,760000,400000,
                                  760000,400000,760000,400000]:
                    self.seek(timestamp//1000)
                    self.delivered.append(timestamp)
                    self.changed.emit()
                    window.frameSwapped.emit()
            if delivery == 'direct':
                emit_returns()
            else:
                QTimer.singleShot(0, emit_returns)

    class SignalWindow(Window):
        def __init__(self, playback):
            super().__init__(QImage())
            self.playback = playback
        def grabWindow(self):
            self.image = QImage(11,7,QImage.Format_RGBA8888)
            self.image.fill(QColor(self.playback._presented_start//40000,33,71,255))
            return super().grabWindow()

    class SignalVideo(Video):
        def property(self, name):
            if name == 'videoSink':
                return playback._presentation_sink
            return super().property(name)

    class Settlement:
        revision = 0
        def wait_for(self, label, predicate, **kwargs):
            assert predicate(Frame(playback)), label
            self.revision += 1

    playback = SignalPlayback()
    window, video = SignalWindow(playback), SignalVideo()
    monkeypatch.setattr(caller, 'find', lambda *args:video)
    def process_and_check(application, predicate, label):
        application.processEvents()  # Actual queued Qt callbacks, when selected.
        assert predicate(), label  # Counts/membership stay in the unchanged caller.
    monkeypatch.setattr(caller, 'wait', process_and_check)
    qt_errors, capture_errors, calls, oracles = [], [], [], []
    monkeypatch.setattr(sys, 'excepthook', lambda typ,value,tb:qt_errors.append(value))
    original_capture = paint.ScenePaintOracle.capture
    def observed_capture(oracle, kind, timestamp=None):
        calls.append((kind, timestamp))
        if not oracles:
            oracles.append(oracle)
        try:
            return original_capture(oracle, kind, timestamp)
        except Exception as error:
            capture_errors.append(error)
            raise
    monkeypatch.setattr(paint.ScenePaintOracle, 'capture', observed_capture)
    delivery = None
    directory = tmp_path/'Qt callback Кириллица Ω'
    def run(mode, dispatch):
        nonlocal delivery
        delivery = dispatch
        monkeypatch.setenv(paint.STORAGE_ENV, mode)
        return caller.trim_render_checks(app, window,
            SimpleNamespace(playback=playback, setOperationEnabled=lambda *args:None,
                            setTrimRange=lambda *args:None), Settlement(), directory)
    return SimpleNamespace(run=run, playback=playback, window=window, directory=directory,
        qt_errors=qt_errors, capture_errors=capture_errors, calls=calls, oracles=oracles)


def assert_signal_evidence(case, mode, incomplete=False):
    records = [json.loads(line) for line in (case.directory/'readbacks.jsonl').read_text(
        encoding='utf8').splitlines()]
    complete = [row for row in records if row['storage']['status'] == 'complete']
    references = [row for row in complete if row['kind'] == 'reference']
    assert [row['reference_timestamp_us'] for row in references] == list(range(360000,840001,40000))
    assert len({row['images']['proof']['raw_sha256'] for row in references}) == 13
    boundaries = [row for row in complete if row['kind'] == 'boundary']
    assert len(boundaries) == (4 if incomplete else 5)
    assert all(row['presentation_us'] == 400000 for row in boundaries)
    assert sum(b<a for a,b in zip(case.playback.delivered,case.playback.delivered[1:])) == 4
    assert case.calls == [('reference',timestamp) for timestamp in range(360000,840001,40000)] + [('boundary',None)]*5
    for row in complete:
        assert set(row['images']) == {'full','video','proof'}
        assert all((case.directory/info['png']).is_file() for info in row['images'].values())
        assert all(info['raw_present'] == (mode == 'raw+png') for info in row['images'].values())
    if incomplete:
        sidecar = json.loads((case.directory/'capture-0018-incomplete.json').read_text(encoding='utf8'))
        assert sidecar['storage']['status'] == 'incomplete' and sidecar['storage']['mode'] == mode
        assert sidecar['readback_revision'] == 18 and sidecar['kind'] == 'boundary'
        assert sidecar['storage']['errors']
        for info in sidecar['images'].values():
            assert info['raw_present']
            raw = (case.directory/Path(info['png']).with_suffix('.rgba')).read_bytes()
            recovered = QImage(str(case.directory/info['recovery_png'])).convertToFormat(QImage.Format_RGBA8888)
            assert bytes(recovered.constBits()) == raw
            assert hashlib.sha256(raw).hexdigest() == info['raw_sha256']
    # Enclosing finally must disconnect its observers on both success/failure.
    count = len(case.calls)
    case.playback.seek(760);case.playback.changed.emit();case.window.frameSwapped.emit()
    case.playback.seek(400);case.playback.changed.emit();case.window.frameSwapped.emit()
    assert len(case.calls) == count and not case.qt_errors
    assert case.playback.receivers('2changed()') == 0


@pytest.mark.parametrize('mode', ['png-only','raw+png'])
@pytest.mark.parametrize('fault', ['save','journal-open'])
@pytest.mark.parametrize('dispatch', ['direct','queued'])
def test_qt_final_boundary_storage_failure_rejects_enclosing_success(
        signal_trim_case, monkeypatch, mode, fault, dispatch):
    case = signal_trim_case
    original_save, original_open = QImage.save, Path.open
    def save(image, path, *args):
        if fault == 'save' and Path(path).name == 'boundary-0018-full.png':
            return False
        return original_save(image, path, *args)
    def open_file(path, *args, **kwargs):
        if (fault == 'journal-open' and path.name == 'readbacks.jsonl'
                and (path.parent/'boundary-0018-full.png').exists()):
            raise OSError('final-boundary journal-open fault')
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(QImage, 'save', save)
    monkeypatch.setattr(Path, 'open', open_file)
    with pytest.raises(paint.CaptureStorageError, match='incomplete') as raised:
        case.run(mode, dispatch)
    assert len(case.capture_errors) == 1 and raised.value is case.capture_errors[0]
    assert case.oracles[0].storage_error == str(raised.value)
    frames = {frame.name for frame in traceback.extract_tb(raised.value.__traceback__)}
    assert {'swapped','capture','_retain_incomplete','trim_render_checks'} <= frames
    # Verification reads must not retrigger the intentionally broken journal open.
    monkeypatch.setattr(Path, 'open', original_open)
    assert_signal_evidence(case, mode, incomplete=True)


@pytest.mark.parametrize('mode', ['png-only','raw+png'])
@pytest.mark.parametrize('dispatch', ['direct','queued'])
def test_qt_successful_callback_control(signal_trim_case, mode, dispatch):
    case = signal_trim_case
    result = case.run(mode, dispatch)
    assert result['painted_picture_us'] == [400000]*5
    assert sum(b<a for a,b in zip(result['presented_us'],result['presented_us'][1:])) == 4
    assert all(400000<=value<800000 for value in result['rendered_us'])
    assert result['excluded_out_us'] == 800000
    assert not case.capture_errors and case.oracles[0].storage_error is None
    assert_signal_evidence(case, mode)


@pytest.mark.parametrize('mode', ['png-only','raw+png'])
@pytest.mark.parametrize('dispatch', ['direct','queued'])
def test_qt_latched_storage_error_rejects_success_without_slot_exception(
        signal_trim_case, monkeypatch, mode, dispatch):
    case = signal_trim_case
    original_capture = paint.ScenePaintOracle.capture
    def latch(oracle, kind, timestamp=None):
        digest = original_capture(oracle, kind, timestamp)
        if oracle.revision == 18:
            oracle.storage_error = 'injected incomplete final capture latch'
        return digest
    monkeypatch.setattr(paint.ScenePaintOracle, 'capture', latch)
    with pytest.raises(paint.CaptureStorageError, match='incomplete final capture latch'):
        case.run(mode, dispatch)
    assert not case.capture_errors  # The independent oracle-state guard is required.
    assert_signal_evidence(case, mode)


@pytest.mark.parametrize('mode', ['png-only','raw+png'])
@pytest.mark.parametrize('dispatch', ['direct','queued'])
def test_qt_readback_exception_reaches_enclosing_check(
        signal_trim_case, monkeypatch, mode, dispatch):
    case = signal_trim_case
    original_grab = case.window.grabWindow
    original_error = ValueError('final boundary readback failure before storage latch')
    def grab():
        if case.window.grabs == 17:
            raise original_error
        return original_grab()
    monkeypatch.setattr(case.window, 'grabWindow', grab)
    with pytest.raises(ValueError, match='final boundary readback failure') as raised:
        case.run(mode, dispatch)
    assert raised.value is original_error and case.capture_errors == [original_error]
    assert case.oracles[0].storage_error is None  # The enclosing exception latch is required.
    assert case.window.grabs == 17 and len(case.calls) == 18
    records = [json.loads(line) for line in (case.directory/'readbacks.jsonl').read_text(
        encoding='utf8').splitlines()]
    assert len(records) == 17 and records[-1]['readback_revision'] == 17
    assert len({row['images']['proof']['raw_sha256'] for row in records[:13]}) == 13
    assert [row['kind'] for row in records] == ['reference']*13+['boundary']*4
    assert case.playback.receivers('2changed()') == 0 and not case.qt_errors


@pytest.mark.parametrize('mode', ['png-only','raw+png'])
def test_qt_capture_failure_keeps_context_if_wait_also_times_out(
        signal_trim_case, monkeypatch, mode):
    case = signal_trim_case
    caller = importlib.import_module('reviewer_findings_native')
    original_wait, original_save = caller.wait, QImage.save
    def save(image, path, *args):
        if Path(path).name == 'boundary-0018-full.png':
            return False
        return original_save(image, path, *args)
    def wait_with_timeout(app, predicate, label):
        original_wait(app, predicate, label)
        if label == 'four presented Out-to-In loops':
            raise AssertionError('Timeout after Qt callback processing')
    monkeypatch.setattr(QImage, 'save', save)
    monkeypatch.setattr(caller, 'wait', wait_with_timeout)
    with pytest.raises(paint.CaptureStorageError) as raised:
        case.run(mode, 'queued')
    assert raised.value is case.capture_errors[0]
    assert_signal_evidence(case, mode, incomplete=True)
