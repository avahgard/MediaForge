"""Boundary controls/reset semantics, with processing forbidden by the fixture."""
import pytest
from tests.presentation.test_workspace_qml import SETUP, CLEANUP


@pytest.mark.integration
@pytest.mark.parametrize("dpi", ["1", "1.5", "2"])
def test_final_crop_handles_resets_and_placement_help(run_python, isolated_environment, tmp_path, dpi):
    isolated_environment["QT_SCALE_FACTOR"] = dpi
    result = run_python(SETUP + r'''
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoFrame
load(Path.cwd()/'final ux.mp4')
image=QImage(1920,1080,QImage.Format_RGB32); image.fill(Qt.yellow)
frame=QVideoFrame(image); frame.setStartTime(0); playback._staging_sink.setVideoFrame(frame)
identities=playback._player,playback._audio,item('viewerVideoOutput')
model.selectOperation('crop'); model.setOperationEnabled('crop',True); QTest.qWait(220)
modes=('left-top','right-top','left-bottom','right-bottom','left','right','top','bottom')
def bounds(obj): return QRectF(obj.mapToScene(QPointF()),obj.size())
anchors={'left-top':(0,0),'right-top':(1,0),'left-bottom':(0,1),'right-bottom':(1,1),
         'left':(0,.5),'right':(1,.5),'top':(.5,0),'bottom':(.5,1)}
def check_centers():
    crop=bounds(item('cropOverlayRect'))
    host=bounds(item('cropHandleLayer'))
    for mode in modes:
        target=item('cropHandle-'+mode);circle=item('cropHandleCircle-'+mode)
        ax,ay=anchors[mode]
        expected=QPointF(crop.x()+ax*crop.width(),crop.y()+ay*crop.height())
        assert circle.isVisible() and circle.width()==circle.height()==10
        assert (bounds(circle).center()-expected).manhattanLength()<.001,(mode,bounds(circle),expected)
        assert host.adjusted(-.001,-.001,.001,.001).contains(bounds(circle)),(mode,host,bounds(circle))
for size in ((800,560),(1440,900)):
    window.setWidth(size[0]); window.setHeight(size[1]); QTest.qWait(250)
    for rect in ((0,0,1920,1080),(240,120,1440,840)):
        assert model.setCropRect(*rect); model.viewerViewport.fit(); app.processEvents()
        crop=bounds(item('cropOverlayRect'))
        check_centers()
        for mode in modes:
            target=item('cropHandle-'+mode)
            assert target.isVisible() and target.width()==target.height()==24
            b=bounds(target)
            ax,ay=anchors[mode]
            expected=QPointF(crop.x()+ax*crop.width(),crop.y()+ay*crop.height())
            assert (b.center()-expected).manhattanLength()<.001, (size,rect,mode,b.center(),expected)
            for otherMode in modes:
                if otherMode != mode: assert not b.intersects(bounds(item('cropHandle-'+otherMode))), (size,mode,otherMode)
    for mode in modes:
        assert model.setCropRect(0,0,1920,1080); app.processEvents()
        target=item('cropHandle-'+mode)
        start=target.mapToScene(QPointF(12,12)).toPoint()
        dx=-12 if 'right' in mode else 12 if 'left' in mode else 0
        dy=-10 if 'bottom' in mode else 10 if 'top' in mode else 0
        QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
        QTest.mouseMove(window,start+QPoint(dx,dy))
        QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,start+QPoint(dx,dy))
        state=model.editState
        assert (state['cropWidth'],state['cropHeight']) != (1920,1080), (size,mode,state)
        assert (state['cropWidth'] < 1920) == (dx != 0), (mode,state)
        assert (state['cropHeight'] < 1080) == (dy != 0), (mode,state)
        assert all(state[k]%2==0 for k in ('cropX','cropY','cropWidth','cropHeight'))
assert model.setCropRect(480,270,960,540)
model.viewerViewport.wheel(item('cropOverlay').width()/2,item('cropOverlay').height()/2,2)
model.viewerViewport.pan(-5,3); app.processEvents(); check_centers()
playback.enterFullscreen(); QTest.qWait(250); check_centers()
model.viewerViewport.fit(); assert model.setCropRect(0,0,1920,1080); app.processEvents(); check_centers()
playback.exitFullscreen(); QTest.qWait(250); model.viewerViewport.fit()
assert model.setCropRect(240,120,1440,840)
model.setOperationEnabled('trim',True); assert model.setTrimRange(100,8000)
model.setOperationEnabled('resize',True); assert model.setReformat('mode','format')
assert model.setReformat('preset','Source') and model.setReformat('placement','fill')
before=model.editState.copy()
# Reset clears an invalid local field too, and never changes the selected tab.
entry=item('cropWInput'); click(entry); entry.setProperty('text','829')
QTest.keyClick(window,Qt.Key_Return); assert entry.property('invalid')
click(item('resetCropButton'))
state=model.editState
assert (state['cropX'],state['cropY'],state['cropWidth'],state['cropHeight']) == (0,0,1920,1080)
assert not state['cropEnabled'] and not state['cropPreview'] and model.selectedOperation=='crop'
assert entry.property('text')=='1920' and not entry.property('invalid')
for key in ('trimEnabled','trimIn','trimOut','resizeEnabled','reformatPreset','reformatPlacement','reformatMode'):
    assert state[key]==before[key]
model.setOperationEnabled('crop',True); assert model.setCropRect(240,120,1440,840)
model.setCropPreview(True)
item('resetCropButton').forceActiveFocus(); QTest.keyClick(window,Qt.Key_Space)
assert not model.editState['cropEnabled'] and not model.editState['cropPreview']
model.selectOperation('resize'); assert model.setReformat('mode','scale')
assert model.setReformat('scale','.7'); QTest.qWait(220)
saved=model.editState.copy()
click(item('reformatScaleSlider'),Qt.RightButton)
state=model.editState
assert state['reformatScale']=='1.0' and item('reformatScaleInput').property('text')=='1.0'
assert item('reformatScaleSlider').property('value')==1
assert (state['reformatOutputWidth'],state['reformatOutputHeight'])==(1920,1080)
for key in ('cropEnabled','cropX','cropWidth','trimIn','trimOut','reformatPreset','reformatPlacement'):
    assert state[key]==saved[key]
item('reformatScaleSlider').forceActiveFocus(); QTest.keyClick(window,Qt.Key_Right)
assert model.editState['reformatScale']=='1.01'
slider=item('reformatScaleSlider')
start=slider.mapToScene(QPointF(slider.property('leftPadding')+slider.property('visualPosition')*(slider.property('availableWidth')-14)+7,slider.height()/2)).toPoint()
QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
QTest.mouseMove(window,start+QPoint(25,0)); QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,start+QPoint(25,0))
assert float(model.editState['reformatScale'])>1.01
model.setReformat('mode','format'); QTest.qWait(220)
for name,word in (('fit','padding'),('fill','cropped'),('stretch','proportions')):
    assert word in item('reformat'+name).property('helpText')
assert (playback._player,playback._audio,item('viewerVideoOutput'))==identities
''' + CLEANUP)
    (tmp_path/'stdout.txt').write_text(result.stdout,encoding='utf-8')
    (tmp_path/'stderr.txt').write_text(result.stderr,encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_task009_hover_helpers_are_absent_and_actions_remain(run_python, tmp_path):
    result = run_python(SETUP + r'''
from PySide6.QtQml import QQmlExpression, QQmlEngine
load(Path.cwd()/'tooltip removal.mp4')
def no_popup(target):
    point=target.mapToScene(QPointF(target.width()/2,target.height()/2)).toPoint()
    QTest.mouseMove(window,point); QTest.qWait(300)
    expr=QQmlExpression(QQmlEngine.contextForObject(target),target,'ToolTip.visible')
    value=expr.evaluate()
    assert not expr.hasError(),expr.error().toString()
    # PySide returns (value, isUndefined) for evaluate().
    assert not value[0],(target.objectName(),value)
no_popup(item('metadataInfoButton'))
assert item('metadataInfoButton').property('accessibleText')=='Source Info'
click(item('metadataInfoButton'));QTest.qWait(160)
no_popup(item('revealSourceButton'))
assert item('revealSourceButton').property('accessibleText')=='Reveal source in Explorer'
QMetaObject.invokeMethod(item('metadataInfoPopup'),'close')
no_popup(item('viewerFitButton'));click(item('viewerFitButton'))
assert model.viewerViewport.mode=='fit'
no_popup(item('viewerZoomReadout'))
model.selectOperation('crop');model.setOperationEnabled('crop',True);QTest.qWait(220)
no_popup(item('resetCropButton'));no_popup(item('cropPreviewToggle'))
model.selectOperation('resize');model.setReformat('mode','scale');QTest.qWait(220)
no_popup(item('reformatScaleSlider'))
model.setReformat('scale','.7');click(item('reformatScaleSlider'),Qt.RightButton)
assert model.editState['reformatScale']=='1.0'
model.setReformat('mode','format');QTest.qWait(220)
for mode in ('fit','fill','stretch'):
    target=item('reformat'+mode);no_popup(target);click(target)
    assert model.editState['reformatPlacement']==mode
''' + CLEANUP)
    (tmp_path/'stdout.txt').write_text(result.stdout,encoding='utf-8')
    (tmp_path/'stderr.txt').write_text(result.stderr,encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
