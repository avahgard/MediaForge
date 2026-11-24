"""Bounded Product Check controls with processing forbidden during editing."""
import pytest
from tests.presentation.test_workspace_qml import SETUP, CLEANUP


@pytest.mark.integration
@pytest.mark.parametrize("dpi", ["1", "1.5", "2"])
def test_pc_ui_slider_source_zoom_and_info(run_python, isolated_environment, dpi, tmp_path):
    isolated_environment["QT_SCALE_FACTOR"] = dpi
    setup = SETUP.replace("10., 'h264', 1920, 1080", "10., 'h264', 1024, 768")
    result = run_python(setup + r'''
from decimal import Decimal
from types import SimpleNamespace
from PySide6.QtGui import QImage, QWheelEvent
from PySide6.QtMultimedia import QVideoFrame
source = Path.cwd() / 'source Кириллица.mp4'
load(source)
image = QImage(1024,768,QImage.Format_RGB32); image.fill(Qt.yellow)
frame = QVideoFrame(image); frame.setStartTime(0)
playback._staging_sink.setVideoFrame(frame)
player, audio, video = playback._player, playback._audio, item('viewerVideoOutput')
model.selectOperation('resize'); model.setOperationEnabled('resize', True); QTest.qWait(220)
slider, entry = item('reformatScaleSlider'), item('reformatScaleInput')
assert model.editState['reformatScale'] == '1.0' and slider.property('value') == 1
assert slider.property('stepSize') == .01 and slider.property('live')
def enter(value):
    click(entry); QTest.keyClick(window,Qt.Key_A,Qt.ControlModifier)
    for char in value: QTest.keyClick(window,Qt.Key(ord(char.upper())))
    QTest.keyClick(window,Qt.Key_Return); app.processEvents()
enter('0.6')
assert slider.property('value') == .6 and not entry.property('invalid')
assert (model.editState['reformatOutputWidth'],model.editState['reformatOutputHeight']) == (614,460)
assert 'rounded to even pixels' in item('reformatOutputDimensions').property('text')
slider.forceActiveFocus()
for _ in range(10): QTest.keyClick(window,Qt.Key_Right)
assert Decimal(model.editState['reformatScale']) == Decimal('.7'), (model.editState['reformatScale'],slider.property('value'),slider.property('activeFocus'),playback.position)
assert entry.property('text') == model.editState['reformatScale']
assert (model.editState['reformatOutputWidth'],model.editState['reformatOutputHeight']) == (716,538)
enter('0.3333333333333333')
assert (model.editState['reformatOutputWidth'],model.editState['reformatOutputHeight']) == (342,256)
enter('NaN')
assert entry.property('invalid') and not model.previewPlan['valid'] and not slider.property('enabled')
enter('0.7'); assert slider.property('enabled') and not entry.property('invalid')
assert model.viewerViewport.zoomText == '100.0%'
assert model.setCropRect(112,84,800,600)
model.setOperationEnabled('crop',True); model.setOperationEnabled('trim',True)
assert model.setTrimRange(100,6000)
click(item('reformatFormatMode')); click(item('reformatfill'))
before = model.editState.copy()
preset = item('reformatPresetInput')
assert preset.property('model')[0] == 'Source'
preset.forceActiveFocus(); QTest.keyClick(window,Qt.Key_Home); app.processEvents()
assert model.editState['reformatPreset'] == 'Source'
assert preset.property('displayText') == 'Source — 1024×768'
assert (model.previewPlan['width'],model.previewPlan['height']) == (1024,768)
assert (model.previewPlan['inputWidth'],model.previewPlan['inputHeight']) == (800,600)
for name in ('trimIn','trimOut','cropX','cropY','cropWidth','cropHeight','cropEnabled','trimEnabled','reformatPlacement'):
    assert model.editState[name] == before[name]
first = model.activeMediaId
assert model.duplicateMedia(first)
clone = next(row['id'] for row in model.mediaItems if row['id'] != first)
assert model.moveMedia(clone,-1) and model.selectMedia(clone)
assert model.editState['reformatPreset'] == 'Source'
assert model.setReformat('preset','HD')
assert model.selectMedia(first) and model.editState['reformatPreset'] == 'Source'
assert model.editState['reformatScale'] == before['reformatScale']
revealed=[]
model._source_revealer = SimpleNamespace(reveal=lambda path: (revealed.append(path) or SimpleNamespace(succeeded=True,message='')))
click(item('metadataInfoButton')); QTest.qWait(160)
button=item('revealSourceButton')
assert button.isVisible() and button.width() == button.height() == 36
assert button.property('glyphName') == 'folderOpen'
assert button.property('accessibleText') == 'Reveal source in Explorer'
assert button.property('focusPolicy') == Qt.StrongFocus
assert button.property('text') == ''
assert window.grabWindow().save(str(Path.cwd() / 'media-info.png'))
button.forceActiveFocus(); QTest.keyClick(window,Qt.Key_Space)
assert len(revealed) == 1 and Path(revealed[0]) == source
QMetaObject.invokeMethod(item('metadataInfoPopup'), 'close')
assert (playback._player,playback._audio,item('viewerVideoOutput')) == (player,audio,video)
''' + CLEANUP)
    (tmp_path / "stdout.txt").write_text(result.stdout, encoding="utf-8")
    (tmp_path / "stderr.txt").write_text(result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
