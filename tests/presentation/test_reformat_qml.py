"""New inspector and effective pipeline behavior with processing forbidden."""
import pytest
from tests.presentation.test_workspace_qml import SETUP, CLEANUP


@pytest.mark.integration
@pytest.mark.parametrize("dpi", ["1", "1.5", "2"])
def test_reformat_editing_pipeline_and_viewport_are_non_destructive(run_python, isolated_environment, dpi):
    isolated_environment["QT_SCALE_FACTOR"] = dpi
    result = run_python(SETUP + r'''
from itertools import product
from PySide6.QtGui import QImage, QWheelEvent
from PySide6.QtMultimedia import QVideoFrame
load(Path.cwd() / 'reformat.mp4')
frame = QVideoFrame(QImage(1920, 1080, QImage.Format_RGB32)); frame.setStartTime(0)
playback._staging_sink.setVideoFrame(frame)
player, audio, video = playback._player, playback._audio, item('viewerVideoOutput')
assert model.setCropRect(240, 0, 1440, 1080)
model.selectOperation('resize')
assert not model.editState['resizeEnabled']
click(item('reformatFormatMode'))
assert model.editState['reformatMode'] == 'format'
assert model.setReformat('preset', 'FHD')
click(item('reformatstretch'))
assert model.editState['reformatPlacement'] == 'stretch'
for trim, crop, reformat in product((False, True), repeat=3):
    for op, enabled in (('trim', trim), ('crop', crop), ('resize', reformat)):
        model.setOperationEnabled(op, enabled)
    p = model.previewPlan
    assert p['cropX'] == (240 if crop else 0)
    assert p['inputWidth'] == (1440 if crop else 1920)
    assert p['width'] == (1920 if reformat or not crop else 1440)
    assert p['reformatted'] == reformat
    if not crop:
        model.selectOperation('crop')
        # Current Owner-approved Crop Edit contract also permits editing an
        # inactive Crop when no downstream Reformat is enabled.
        assert model.previewPlan['editing'] == (not reformat)
        assert model.previewPlan['inputWidth'] == 1920 and model.previewPlan['cropX'] == 0
        if reformat: assert model.previewPlan == p
        model.selectOperation('resize')
saved = model.editState.copy()
model.selectOperation('crop'); assert model.previewPlan['editing']
assert model.previewPlan['inputWidth'] == 1920 and not model.previewPlan['reformatted']
model.setCropPreview(True); assert model.previewPlan['reformatted']
model.selectOperation('resize'); assert model.previewPlan['reformatted']
model.selectOperation('crop'); model.setCropPreview(False)
assert model.editState['cropX'] == saved['cropX'] and model.editState['cropWidth'] == saved['cropWidth']
before = model.editState.copy()
area = item('viewerZoomInput'); local = QPointF(area.width()*.7, area.height()*.5)
scene = area.mapToScene(local)
QTest.mouseMove(window, scene.toPoint())
event = QWheelEvent(scene, scene, QPoint(), QPoint(0,480), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
app.sendEvent(window, event); app.processEvents()
assert event.isAccepted() and model.viewerViewport.mode == 'zoom'
assert model.editState == before
assert model.viewerViewport.panX < 0
QTest.mousePress(window, Qt.MiddleButton, Qt.NoModifier, scene.toPoint())
QTest.mouseMove(window, (scene + QPointF(-25, 15)).toPoint())
QTest.mouseRelease(window, Qt.MiddleButton, Qt.NoModifier, (scene + QPointF(-25, 15)).toPoint())
assert model.editState == before
click(item('viewerFitButton')); assert model.viewerViewport.mode == 'fit'
assert model.viewerViewport.zoomText == '100.0%'
assert not window.findChildren(QObject, 'viewerPixelsButton')
assert (playback._player, playback._audio, item('viewerVideoOutput')) == (player, audio, video)
model.selectOperation('resize')
model.setReformat('mode', 'scale')
assert not model.setReformat('scale', 'NaN')
assert not model.previewPlan['valid'] and not model.startStandaloneReformat()
assert model.setReformat('scale', '0.5') and model.editState['reformatOutputWidth'] == 720
assert model.selectMedia(model.activeMediaId)
load(Path.cwd() / 'second.mp4')
assert model.viewerViewport.mode == 'fit'
''' + CLEANUP)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_viewer_controls_do_not_overlap_at_minimum_and_normal_size(run_python):
    result = run_python(SETUP + r'''
load(Path.cwd() / 'layout.mp4')
from PySide6.QtCore import QRectF
def bounds(obj):
    return QRectF(obj.mapToScene(QPointF()), obj.size())
for width,height in ((800,560),(1440,900)):
    window.setWidth(width); window.setHeight(height); QTest.qWait(250)
    for mode in ('fit', 'zoom'):
        model.viewerViewport.fit()
        if mode == 'zoom': model.viewerViewport.wheel(400,300,4)
        app.processEvents()
        assert window.findChild(QObject,'imagePreviewQuality') is None
        channel = bounds(item('viewerChannel'))
        for name in ('viewerFitButton', 'viewerZoomReadout', 'fullscreenButton'):
            target = item(name)
            assert target.isVisible()
            assert not bounds(target).intersects(channel), (width, height, mode, name, bounds(target), channel)
    click(item('viewerFitButton')); assert model.viewerViewport.mode == 'fit'
    fit, readout = item('viewerFitButton'), item('viewerZoomReadout')
    assert fit.height() == readout.height() == 32
    assert bounds(fit).y() == bounds(readout).y()
''' + CLEANUP)
    assert result.returncode == 0, result.stdout + result.stderr
