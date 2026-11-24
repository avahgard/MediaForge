"""Behavioral QML checks; concrete Windows playback is checked by the runtime harness."""

import pytest

SETUP = r'''
import logging
import time
import shiboken6
from pathlib import Path
from PySide6.QtCore import QObject, QPoint, QPointF, QUrl, Signal, Qt, QMetaObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from mediaforge.application.contracts.media import MediaMetadataDto, OpenMediaResult
from mediaforge.bootstrap.application import load_shell
from mediaforge.infrastructure.resources.paths import qml_root
from mediaforge.presentation.bridge.media import MediaViewModel
from mediaforge.presentation.bridge.playback import PlaybackController

class Player(QObject):
    playbackStateChanged = Signal(object); mediaStatusChanged = Signal(object)
    positionChanged = Signal(int); durationChanged = Signal(int); errorOccurred = Signal(object, str)
    def setAudioOutput(self, value): self.audio = value
    def stop(self): self.playbackStateChanged.emit(QMediaPlayer.StoppedState)
    def setSource(self, value): self.source = value
    def setPosition(self, value): self.positionChanged.emit(value)
    def play(self): self.playbackStateChanged.emit(QMediaPlayer.PlayingState)
    def pause(self): self.playbackStateChanged.emit(QMediaPlayer.PausedState)
    def setVideoOutput(self, value): self.output = value
    def setVideoSink(self, value): self.sink = value

class Audio(QObject):
    volumeChanged = Signal(float); mutedChanged = Signal(bool)
    def __init__(self): super().__init__(); self.v = 0; self.m = False
    def setVolume(self, value): self.v = value; self.volumeChanged.emit(value)
    def volume(self): return self.v
    def setMuted(self, value): self.m = value; self.mutedChanged.emit(value)
    def isMuted(self): return self.m

class Open:
    def __init__(self): self.calls = []
    def open_media(self, path):
        self.calls.append(path)
        return OpenMediaResult(metadata=MediaMetadataDto(Path(path).name, path, 'mp4',
                               10., 'h264', 1920, 1080, 25., 25., 250))
    def cancel(self): pass

class NoProcessing:
    busy = False
    def cancel(self): pass
    def set_source(self, source): return True
    def shutdown(self): pass
    def __getattr__(self, name):
        raise AssertionError('Workspace UI invoked processing: ' + name)

def wait(predicate):
    end = time.monotonic() + 3
    while time.monotonic() < end:
        app.processEvents()
        if predicate(): return
        QTest.qWait(5)
    raise AssertionError('timeout')

def item(name):
    matches = list(window.findChildren(QObject, name))
    def visual_find(target):
        if target.objectName() == name: matches.append(target)
        for child in target.childItems():
            visual_find(child)
    visual_find(window.contentItem())
    assert matches, name
    return next((obj for obj in matches if hasattr(obj,'isVisible') and obj.isVisible()), matches[0])

def click(target, button=Qt.LeftButton):
    point = target.mapToScene(QPointF(target.width()/2, target.height()/2))
    QTest.mouseClick(window, button, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
    QTest.qWait(30)

def load(path):
    path.write_bytes(b'fixture')
    assert model.openMedia(QUrl.fromLocalFile(str(path)))
    wait(lambda: model.state == 'loaded' and model.canOpen)
    playback._player.durationChanged.emit(10000)
    QTest.qWait(220)

app = QGuiApplication([])
use = Open()
playback = PlaybackController(Player(), Audio())
model = MediaViewModel(use, playback_controller=playback, trim_use_case=NoProcessing(),
                       crop_use_case=NoProcessing(), resize_use_case=NoProcessing())
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty('mediaViewModel', model)
warnings = []
engine.warnings.connect(lambda values: warnings.extend(v.toString() for v in values))
window = load_shell(engine, qml_root(), logging.getLogger('workspace-test'))
window.requestActivate()
QTest.qWait(60)
'''

CLEANUP = r'''
assert not warnings, warnings
model.shutdown(); window.close(); shiboken6.delete(engine)
'''


@pytest.mark.integration
def test_tray_visible_click_keeps_offset_and_geometry_with_snapshot_updates(run_python):
    result=run_python(SETUP + r'''
load(Path.cwd()/'tray.mp4')
first=model.activeMediaId
for _ in range(16): assert model.duplicateMedia(first)
QTest.qWait(100)
tray=item('mediaTrayList')
tray.setProperty('contentX',650);QTest.qWait(50)
for index in [3,4,3,4,3]:
    card=item('trayItem'+str(index))
    offset=tray.property('contentX');x=card.mapToScene(QPointF()).x();width=card.width()
    # Narrow test window: explicitly ensure this chosen card is visible first.
    from PySide6.QtQml import QQmlExpression
    engine.rootContext().setContextProperty('testTray',tray)
    QQmlExpression(engine.rootContext(),window,f'testTray.positionViewAtIndex({index},0)').evaluate()
    QTest.qWait(30)
    offset=tray.property('contentX');x=card.mapToScene(QPointF()).x()
    click(card);QTest.qWait(60)
    assert model.activeMediaId==model._workspace.items[index].id
    assert tray.property('currentIndex')==index
    assert abs(tray.property('contentX')-offset)<1
    assert abs(card.mapToScene(QPointF()).x()-x)<1 and card.width()==width
selected=model.activeMediaId
assert model.moveMedia(selected,-1);QTest.qWait(40)
assert model.activeMediaId==selected
assert model.removeMedia(model._workspace.items[0].id);QTest.qWait(40)
assert model.activeMediaId==selected
assert model.duplicateMedia(selected);QTest.qWait(40)
assert model.activeMediaId==selected
''' + CLEANUP)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.integration
def test_product_operations_whole_pill_and_all_pointer_paths(run_python):
    result = run_python(SETUP + r'''
load(Path.cwd() / 'operations.mp4')
for operation in ('trim', 'crop', 'resize'):
    pill = item('operation' + ('Reformat' if operation == 'resize' else operation.title()))
    indicator = item('enable' + ('Reformat' if operation == 'resize' else operation.title()))
    origin = indicator.mapToItem(pill, QPointF())
    assert 0 <= origin.x() and origin.x()+indicator.width() <= pill.width()
    assert 0 <= origin.y() and origin.y()+indicator.height() <= pill.height()
    model.selectOperation('resize' if operation != 'resize' else 'trim')
    selected = model.selectedOperation
    model.setOperationEnabled(operation, False)
    click(item('select' + ('Reformat' if operation == 'resize' else operation.title())), Qt.RightButton)
    assert model.selectedOperation == selected and model.editState[operation+'Enabled']
    click(indicator, Qt.RightButton)
    assert model.selectedOperation == selected and not model.editState[operation+'Enabled']
    click(indicator)
    assert model.selectedOperation == selected and model.editState[operation+'Enabled']
    click(item('select' + ('Reformat' if operation == 'resize' else operation.title())))
    assert model.selectedOperation == operation and model.editState[operation+'Enabled']
    # The selected border belongs to the entire parent pill, including the toggle.
    assert pill.property('selected')
    click(item('activeOperationToggle'))
    assert not model.editState[operation+'Enabled'] and not pill.property('enabledOperation')
    click(indicator, Qt.RightButton)
    assert model.editState[operation+'Enabled'] and pill.property('enabledOperation')
    click(item('activeOperationToggle'))
assert window.findChild(QObject,'operationNoise') is None
assert not any(child.objectName().startswith('operationMenu') for child in window.findChildren(QObject))
widths = [item('operation'+name).width() for name in ('Trim','Crop','Reformat','Output')]
row=item('operationsViewport');surface=item('operationSurface')
assert max(widths)-min(widths)<.01, widths
assert abs(sum(widths)+3*row.property('spacing')+2*row.x()-surface.width())<.01
before = model.editState.copy()
click(item('selectOutput'))
assert model.selectedOperation == 'output' and model.editState == before
''' + CLEANUP)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_product_trim_reset_shortcuts_and_half_open_loop(run_python):
    result = run_python(SETUP + r'''
load(Path.cwd() / 'loop.mp4')
player = playback._player
model.setOperationEnabled('trim', True)
assert model.setTrimRange(2000, 4000)
playback.seek(2000)
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoFrame
frame = QVideoFrame(QImage(16,16,QImage.Format_RGB32));frame.setStartTime(2000000)
playback._staging_sink.setVideoFrame(frame)
playback.togglePlayback()
assert playback.trimLoopActive and playback.playing
player.positionChanged.emit(4000)
assert playback.position == 2000 and playback.trimLoopActive and playback.playing
# The next loop is armed by actual in-range presentation, not a clock-only echo.
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoFrame
frame = QVideoFrame(QImage(16,16,QImage.Format_RGB32));frame.setStartTime(2000000)
playback._staging_sink.setVideoFrame(frame)
player.positionChanged.emit(4700)
assert playback.position == 2000
playback.seek(3000); assert playback.trimLoopActive
playback.seek(4000); assert not playback.trimLoopActive and playback.position == 4000
playback.togglePlayback(); playback.togglePlayback()
assert playback.playing and not playback.trimLoopActive  # Play at Out is outside.
playback.togglePlayback(); playback.seek(2000); playback.togglePlayback()
assert playback.trimLoopActive
assert model.setTrimRange(1500, 3500) and playback.trimLoopActive
assert model.setTrimRange(2500, 3500) and not playback.trimLoopActive and playback.position == 2000
playback.togglePlayback(); playback.seek(3000); playback.togglePlayback()
assert playback.trimLoopActive
model.setOperationEnabled('trim', False)
assert not playback.trimLoopActive and playback.position == 3000 and playback.playing
model.setOperationEnabled('trim', True)
assert not playback.trimLoopActive
playback.togglePlayback(); playback.togglePlayback(); assert playback.trimLoopActive
item('workspaceKeyboardFocus').forceActiveFocus()
QTest.keyClick(window, Qt.Key_I, Qt.ShiftModifier)
assert model.editState['trimIn'] == 0
QTest.keyClick(window, Qt.Key_O, Qt.ShiftModifier)
assert model.editState['trimOut'] == 10000
assert item('trimInInput').property('displayedValue') == '00:00.000'
assert item('trimOutInput').property('displayedValue') == '00:10.000'
for operation, field in (('trim','trimInInput'), ('crop','cropXInput'), ('resize','resizeWidthInput')):
    model.selectOperation(operation)
    if operation == 'resize': model.setReformat('mode', 'format')
    QTest.qWait(220)
    editor = item(field); click(editor)
    before = model.editState.copy()
    QTest.keyClick(window, Qt.Key_I, Qt.ShiftModifier)
    QTest.keyClick(window, Qt.Key_O, Qt.ShiftModifier)
    assert model.editState == before and playback._player is player
''' + CLEANUP)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_product_audio_restore_and_windowed_fullscreen_agreement(run_python):
    result = run_python(SETUP + r'''
load(Path.cwd() / 'audio.mp4')
assert playback.volumePercent == 0 and playback.lastNonZeroVolume == 100 and playback.effectiveMuted
playback.toggleMuted(); assert playback.volume == 100 and not playback.muted
for value in (37, 64, 0, 0, 22, 0):
    playback.setVolume(value)
    assert item('volumeSlider').property('value') == value
    assert playback.effectiveMuted == (value == 0)
    assert item('muteButton').property('glyphName') == ('mute' if value == 0 else 'volume')
playback.toggleMuted(); assert playback.volume == 22
playback.enterFullscreen(); app.processEvents()
wait(lambda: item('fullscreenTransport').y() == window.height()-126)
click(item('fullscreenMuteButton'))
assert playback.volume == 0 and playback.lastNonZeroVolume == 22, (playback.volume, playback.lastNonZeroVolume, playback.state, item('fullscreenTransport').y())
assert item('fullscreenVolumeSlider').property('value') == 0 and item('volumeSlider').property('value') == 0
playback.setVolume(31)
assert not playback.effectiveMuted and not playback.muted
assert item('fullscreenVolumeSlider').property('value') == 31
assert item('fullscreenMuteButton').property('glyphName') == 'volume'
playback.exitFullscreen()
assert item('volumeSlider').property('value') == 31
''' + CLEANUP)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_product_crop_digit_wheel_keeps_invalid_text_and_last_valid_geometry(run_python):
    result = run_python(SETUP + r'''
from PySide6.QtGui import QWheelEvent
load(Path.cwd() / 'wheel.mp4')
model.selectOperation('crop'); QTest.qWait(220)
assert model.setCropRect(0, 0, 828, 800)
editor = item('cropWInput')
def wheel(field, delta=120):
    p = field.mapToScene(QPointF(field.width()/2, field.height()/2))
    QTest.mouseMove(window, QPoint(round(p.x()),round(p.y())))
    event = QWheelEvent(p, QPointF(window.mapToGlobal(QPoint(round(p.x()),round(p.y())))), QPoint(), QPoint(0,delta), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    app.sendEvent(window, event); app.processEvents()
click(editor); editor.setProperty('text','828'); editor.setProperty('cursorPosition',3)
wheel(editor)
assert editor.property('text') == '829' and editor.property('invalid') and model.editState['cropWidth'] == 828
wheel(editor)
assert editor.property('text') == '830' and not editor.property('invalid') and model.editState['cropWidth'] == 830
editor.select(1,3); wheel(editor)
assert editor.property('text') == '840' and editor.property('wheelPlace') == '1'
wheel(editor); assert editor.property('text') == '850'
editor.setProperty('cursorPosition',1); wheel(editor)
assert editor.property('text') == '860'
editor.setProperty('cursorPosition',0); wheel(editor)
assert editor.property('text') == '960'
editor.setProperty('cursorPosition',3); wheel(editor,-120)
assert editor.property('text') == '959' and editor.property('invalid') and model.editState['cropWidth'] == 960
editor.setProperty('text','bad'); wheel(editor); assert editor.property('text') == 'bad'
editor.setProperty('text',''); wheel(editor); assert editor.property('text') == ''
editor.setProperty('text','959'); editor.setProperty('cursorPosition',3)
item('workspaceKeyboardFocus').forceActiveFocus(); wheel(editor)
assert editor.property('text') == '959' and editor.property('wheelPlace') == ''
assert model.editState['cropWidth'] == 960  # No silent rounding or invalid commit.
''' + CLEANUP)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_product_viewer_single_double_click_and_control_priority(run_python):
    result = run_python(SETUP + r'''
load(Path.cwd() / 'clicks.mp4')
player = playback._player
states = []
player.playbackStateChanged.connect(lambda state: states.append(state))
background = item('viewerBackgroundInput')
click(background); assert not playback.playing  # Single is pending for the system interval.
wait(lambda: playback.playing)
before = len(states)
click(background)
p = background.mapToScene(QPointF(background.width()/2, background.height()/2))
QTest.mouseDClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(round(p.x()), round(p.y())))
wait(lambda: playback.fullscreen)
QTest.qWait(app.styleHints().mouseDoubleClickInterval()+40)
assert len(states) == before and playback._player is player and playback.playing
background = item('viewerBackgroundInput')
before = len(states)
click(background)
p = background.mapToScene(QPointF(background.width()/2, background.height()/2))
QTest.mouseDClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(round(p.x()), round(p.y())))
wait(lambda: not playback.fullscreen)
QTest.qWait(app.styleHints().mouseDoubleClickInterval()+40)
assert len(states) == before
model.selectOperation('crop'); QTest.qWait(220)
crop = item('cropOverlay')
click(crop)
QTest.qWait(app.styleHints().mouseDoubleClickInterval()+40)
assert len(states) == before and not playback.fullscreen
click(item('muteButton'))
QTest.qWait(app.styleHints().mouseDoubleClickInterval()+40)
assert len(states) == before
# The complete active Crop surface owns fallback, including outside its rectangle.
p = crop.mapToScene(QPointF(2,2))
QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,QPoint(round(p.x()),round(p.y())))
QTest.qWait(app.styleHints().mouseDoubleClickInterval()+40)
assert len(states) == before
model.selectOperation('trim')
click(item('viewerBackgroundInput'))
load(Path.cwd()/'replacement.mp4')
QTest.qWait(app.styleHints().mouseDoubleClickInterval()+40)
assert not playback.playing  # An old pending single click cannot autoplay B.
''' + CLEANUP)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_home_drop_validation_info_same_button_and_approved_switches(run_python):
    result = run_python(SETUP + r'''
copy = [obj.property('text') for obj in window.findChildren(QObject) if obj.property('text')]
for text in ('MediaForge', 'Fast media processing without project overhead.',
                 'Drop media here', 'or click to browse', 'Choose file'):
    assert text in copy, text
source = Path.cwd() / 'space кириллица.mp4'
source.write_bytes(b'fixture')
local = QUrl.fromLocalFile(str(source))
assert model.canDropMedia([local])
for urls in ([], [QUrl('https://example.com/video.mp4')],
             [QUrl.fromLocalFile(str(source.parent / 'absent.mp4'))], [local, local]):
    assert not model.dropMedia(urls) and model.state == 'empty'
assert model.dropMedia([local])
wait(lambda: model.state == 'loaded' and model.canOpen)
QTest.qWait(220)
info = item('metadataInfoPopup')
for _ in range(3):
    click(item('metadataInfoButton')); assert info.property('visible')
    click(item('metadataInfoButton')); wait(lambda: not info.property('visible'))
assert len(use.calls) == 1
model.selectOperation('crop'); QTest.qWait(220)
preview = item('cropPreviewToggle')
assert not preview.property('checked')
click(preview); assert model.editState['cropPreview']
click(preview); assert not model.editState['cropPreview']
model.selectOperation('resize'); model.setReformat('mode', 'format'); QTest.qWait(220)
aspect = item('keepAspectRatioControl')
assert aspect.property('enabled') and not aspect.property('checked')
click(aspect); assert model.editState['reformatLocked']
assert window.findChild(QObject,'exportButton') is None
model.selectOperation('output'); QTest.qWait(220)
assert item('exportStart').property('enabled') is False
''' + CLEANUP)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_operation_combinations_strict_editing_focus_and_frame_shortcuts(run_python):
    result = run_python(SETUP + r'''
load(Path.cwd() / 'first.mp4')
for selected in ('trim', 'crop', 'resize'):
    for operation in ('trim', 'crop', 'resize'):
        for enabled in (False, True):
            model.selectOperation(selected)
            model.setOperationEnabled(operation, enabled)
            assert model.selectedOperation == selected
            assert model.editState[operation + 'Enabled'] is enabled
            model.selectOperation(operation)
            assert model.editState[operation + 'Enabled'] is enabled
before = model.editState.copy()
model.selectOperation('noise'); model.selectOperation('output')
model.setOperationEnabled('noise', True); model.setOperationEnabled('output', True)
assert model.editState == before and model.selectedOperation == 'output'
for operation in ('trim', 'crop', 'resize'):
    QTest.qWait(30)
    selected = model.selectedOperation
    was_enabled = model.editState[operation + 'Enabled']
    click(item('enable' + ('Reformat' if operation == 'resize' else operation.title())))
    assert model.selectedOperation == selected
    assert model.editState[operation + 'Enabled'] is not was_enabled
    click(item('select' + ('Reformat' if operation == 'resize' else operation.title())))
    assert model.selectedOperation == operation
    assert model.editState[operation + 'Enabled'] is not was_enabled
model.selectOperation('crop'); assert model.setCropRect(0, 0, 1800, 1000); QTest.qWait(220)
editor = item('cropXInput')
for invalid in ('1', '4294967296', '1.5', '-2', 'abc', '99999999999999999999999999'):
    click(editor); editor.setProperty('text', invalid)
    QTest.keyClick(window, Qt.Key_Return); QTest.qWait(20)
    assert editor.property('invalid') and model.editState['cropX'] == 0, invalid
click(editor); editor.setProperty('text', '20'); QTest.keyClick(window, Qt.Key_Return)
assert not editor.property('invalid') and model.editState['cropX'] == 20
playback.seek(4000)
before = model.editState.copy()
for key, mods in ((Qt.Key_Space, Qt.NoModifier), (Qt.Key_Left, Qt.NoModifier),
                  (Qt.Key_Right, Qt.ShiftModifier), (Qt.Key_I, Qt.NoModifier),
                  (Qt.Key_O, Qt.NoModifier), (Qt.Key_F, Qt.NoModifier), (Qt.Key_Delete, Qt.NoModifier)):
    QTest.keyClick(window, key, mods)
assert model.editState == before and playback.position == 4000 and not playback.fullscreen
for operation, name in (('resize', 'resizeWidthInput'), ('trim', 'trimInInput')):
    model.selectOperation(operation)
    if operation == 'resize': model.setReformat('mode', 'format')
    QTest.qWait(60)
    editor = item(name); click(editor)
    before = model.editState.copy()
    for key in (Qt.Key_Space, Qt.Key_Left, Qt.Key_I, Qt.Key_O, Qt.Key_F, Qt.Key_Delete):
        QTest.keyClick(window, key)
    QTest.keyClick(window, Qt.Key_Right, Qt.ShiftModifier)
    assert model.editState == before and playback.position == 4000 and not playback.fullscreen
    item('workspaceKeyboardFocus').forceActiveFocus()
item('workspaceKeyboardFocus').forceActiveFocus()
playback.seek(4000)
QTest.keyClick(window, Qt.Key_Right, Qt.ShiftModifier)
assert playback.position == 4000 and playback.frameText == 'Frame timeline unavailable'
# A fake player's position signal is not evidence of a displayed frame.
# Exact stepping is covered by the permanent native VideoOutput tests.
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoFrame
frame = QVideoFrame(QImage(16, 16, QImage.Format_RGB32))
frame.setStartTime(4000000)
playback._staging_sink.setVideoFrame(frame)
QTest.keyClick(window, Qt.Key_Right, Qt.ShiftModifier)
assert playback.position == 4000 and playback.frameText == 'Frame timeline unavailable' and not playback.exactStepAvailable
frame = QVideoFrame(QImage(16, 16, QImage.Format_RGB32))
frame.setStartTime(4040000)
playback._staging_sink.setVideoFrame(frame)
assert playback.frameText == 'Frame timeline unavailable' and not playback.framesReliable
QTest.keyClick(window, Qt.Key_Left, Qt.ShiftModifier); assert playback.position == 4000
playback.seek(4000)
QTest.keyClick(window, Qt.Key_Right); assert playback.position == 9000
QTest.keyClick(window, Qt.Key_Right); assert playback.position == 10000
QTest.keyClick(window, Qt.Key_Left); assert playback.position == 5000
QTest.keyClick(window, Qt.Key_Space); assert playback.playing
QTest.keyClick(window, Qt.Key_Space); assert playback.state == 'Paused'
playback.configure_timeline(model.activeMediaId, str(Path.cwd()/"none.mp4"))
before = playback.position
QTest.keyClick(window, Qt.Key_Right, Qt.ShiftModifier)
assert playback.position == before and playback.frameText == 'Frame timeline unavailable'
''' + CLEANUP)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_timeline_trim_and_tray_context_delete_preserve_current_source(run_python):
    result = run_python(SETUP + r'''
source = Path.cwd() / 'first.mp4'
load(source)
player = playback._player
timeline = item('mediaTimeline')
model.setOperationEnabled('trim', True)
click(timeline); assert 4900 <= playback.position <= 5100
item('workspaceKeyboardFocus').forceActiveFocus()
QTest.keyClick(window, Qt.Key_I); assert model.editState['trimIn'] == playback.position
click(item('setTrimInButton'), Qt.RightButton); assert model.editState['trimIn'] == 0
playback.seek(8000)
QTest.keyClick(window, Qt.Key_O); assert model.editState['trimOut'] == 8000
click(item('setTrimOutButton'), Qt.RightButton); assert model.editState['trimOut'] == 10000
assert not model.setTrimRange(9000, 8000)
for operation in ('trim', 'crop', 'resize'):
    model.selectOperation(operation)
    model.setOperationEnabled(operation, True)
model.setCropPreview(True)
assert playback._player is player and playback.position == 8000
model.setCropPreview(False)
playback.enterFullscreen(); playback.exitFullscreen()
assert playback._player is player and playback.position == 8000
item('workspaceKeyboardFocus').forceActiveFocus()
QTest.keyClick(window, Qt.Key_Delete); assert len(model.mediaItems) == 1
tray = item('mediaTray'); tray.forceActiveFocus()
QTest.keyClick(window, Qt.Key_Delete)
assert model.state == 'empty' and len(model.mediaItems) == 0 and source.exists()
from PySide6.QtQml import QQmlExpression,qmlContext
for delta in (-1,1):
    expression=QQmlExpression(qmlContext(tray),tray,'navigate('+str(delta)+')')
    expression.evaluate();assert not expression.hasError(),expression.error().toString()
assert model.activeMediaId==''
''' + CLEANUP)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_context_menu_clicks_reorder_duplicate_and_remove_the_target(run_python):
    result = run_python(SETUP + r'''
sources = [Path.cwd() / (name + '.mp4') for name in ('first', 'second', 'third')]
for source in sources: load(source)
ids = [row['id'] for row in model.mediaItems]
assert model.selectMedia(ids[0]); model.setResizeSize(1024, 768)
playback._player.durationChanged.emit(10000)
playback.seek(4000); QTest.qWait(60)
def context(index):
    click(item('trayItem' + str(index)), Qt.RightButton)
    QTest.qWait(130)
context(0)
assert not item('contextMoveleft').property('enabled')
assert item('contextMoveright').property('enabled')
click(item('contextMoveright')); QTest.qWait(130)
assert [row['id'] for row in model.mediaItems] == [ids[1], ids[0], ids[2]]
assert model.activeMediaId == ids[0] and playback.position == 4000, (model.activeMediaId, ids[0], playback.position)
context(1); click(item('contextDuplicate')); QTest.qWait(130)
assert len(model.mediaItems) == 4
clone = model.mediaItems[2]['id']
assert model.activeMediaId == ids[0]
player = playback._player
click(item('trayItem2'))
assert model.activeMediaId == clone and model.editState['resizeWidth'] == 1024
assert playback._player is player and playback.position == 4000
assert model.setResizeSize(800, 600)
assert model.selectMedia(ids[0]) and model.editState['resizeWidth'] == 1024
context(2); click(item('contextMoveleft')); QTest.qWait(130)
assert model.mediaItems[1]['id'] == clone and model.activeMediaId == ids[0]
context(0); click(item('contextRemove')); QTest.qWait(130)
assert ids[1] not in [row['id'] for row in model.mediaItems]
assert model.activeMediaId == ids[0] and playback._player is player and playback.position == 4000
context(2)
assert item('contextMoveleft').property('enabled')
assert not item('contextMoveright').property('enabled')
QTest.keyClick(window, Qt.Key_Escape)
assert all(source.exists() for source in sources)
''' + CLEANUP)
    assert result.returncode == 0, result.stdout + result.stderr
