import pytest
from tests.presentation.test_export_qml import EXPORT_SETUP
from tests.presentation.test_workspace_qml import CLEANUP


@pytest.mark.integration
def test_fit_comparison_shortcuts_and_keyboard_ownership(run_python):
    result=run_python(EXPORT_SETUP+r'''
load(Path.cwd()/'comparison.mp4')
model.setOperationEnabled('crop',True);assert model.setCropRect(100,20,960,540)
model.setOperationEnabled('resize',True);assert model.setReformat('pixel_aspect','4/3')
model.selectOperation('output');QTest.qWait(230)
playback.seek(2400);playback.togglePlayback()
player,renderer=playback._player,item('viewerVideoOutput')
before=model.editState.copy();summary=model.output.summary.copy();position=playback.position;state=playback.state
def key(k):item('workspaceKeyboardFocus').forceActiveFocus();QTest.keyClick(window,k);QTest.qWait(30)
for fullscreen in (False,True):
    if fullscreen:click(item('fullscreenButton'));assert playback.fullscreen
    for _ in range(3):
        model.viewerViewport.wheel(200,100,3);model.viewerViewport.pan(20,10)
        key(Qt.Key_F)
        assert model.viewerViewport.zoomText=='100.0%' and model.viewerViewport.panX==model.viewerViewport.panY==0
        assert playback.fullscreen==fullscreen
    plan=model.previewPlan.copy();key(Qt.Key_W);assert model.previewPlan==plan
    assert window.findChild(QObject,'viewerCompareSource') is None and window.findChild(QObject,'viewerCompareOutput') is None
    model.setOperationEnabled('resize',False);assert not model.previewPlan['reformatted']
    saved=model.editState['resizeWidth'];model.setOperationEnabled('resize',True)
    assert model.previewPlan['reformatted'] and model.editState['resizeWidth']==saved
    assert model.editState==before and model.output.summary==summary
    assert playback.position==position and playback.state==state
    assert (playback._player,item('viewerVideoOutput'))==(player,renderer) and not exports.requests
    if fullscreen:key(Qt.Key_Escape);assert not playback.fullscreen
# Double-click changes presentation only (deferred single click must not fire).
background=item('viewerBackgroundInput');p=background.mapToScene(QPointF(background.width()*.6,background.height()*.6))
for desired in (True,False):
    QTest.mouseDClick(window,Qt.LeftButton,Qt.NoModifier,QPoint(round(p.x()),round(p.y())))
    QTest.qWait(100);assert playback.fullscreen==desired
    QTest.qWait(500);assert playback.state==state
    p=background.mapToScene(QPointF(background.width()*.6,background.height()*.6))
click(item('fullscreenButton'));assert playback.fullscreen
playback.setControlsZone(True);QTest.qWait(200)
click(item('exitFullscreenButton'));assert not playback.fullscreen
for operation,name in (('output','exportFilename'),('resize','pixelAspectInput')):
    model.selectOperation(operation);QTest.qWait(230);field=item(name);field.forceActiveFocus();field.setProperty('text','')
    model.viewerViewport.wheel(100,100,2);zoom=model.viewerViewport.zoomText;mode=model.previewSource
    QTest.keyClick(window,Qt.Key_F);QTest.keyClick(window,Qt.Key_W)
    assert field.property('text')=='fw' and model.previewSource==mode and model.viewerViewport.zoomText==zoom
    field.setProperty('text',model.editState['pixelAspectText'] if operation=='resize' else model.output.settings['filename'])
    item('workspaceKeyboardFocus').forceActiveFocus()
model.selectOperation('output');QTest.qWait(230)
item('inspectorScroll').property('contentItem').setProperty('contentY',0);QTest.qWait(40)
combo=item('exportContainer');click(combo);QTest.qWait(40)
assert window.property('popupOverlay').isVisible()
zoom=model.viewerViewport.zoomText;mode=model.previewSource
QTest.keyClick(window,Qt.Key_F);QTest.keyClick(window,Qt.Key_W)
assert model.previewSource==mode and model.viewerViewport.zoomText==zoom
QTest.keyClick(window,Qt.Key_Escape)
popup=item('metadataInfoPopup');QMetaObject.invokeMethod(popup,'open');QTest.qWait(40)
QTest.keyClick(window,Qt.Key_F);QTest.keyClick(window,Qt.Key_W)
assert model.previewSource==mode and model.viewerViewport.zoomText==zoom
QMetaObject.invokeMethod(popup,'close')
model.selectOperation('crop');assert model.previewSource and model.previewPlan['editing']
model.setCropPreview(True);assert not model.previewPlan['editing'] and model.previewPlan['reformatted']
assert {k:v for k,v in model.editState.items() if k!='cropPreview'}=={k:v for k,v in before.items() if k!='cropPreview'} and not exports.requests
'''+CLEANUP)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.integration
@pytest.mark.parametrize('dpi',['1','1.5','2'])
def test_compact_reformat_and_reserved_export_footer(run_python,isolated_environment,dpi):
    isolated_environment['QT_SCALE_FACTOR']=dpi
    result=run_python(EXPORT_SETUP+r'''
load(Path.cwd()/'polish.mp4')
model.selectOperation('resize');model.setOperationEnabled('resize',True)
assert model.setReformat('mode','format') and model.setReformat('preset','Custom')
QTest.qWait(230)
lock=item('keepAspectRatioControl');click(lock);assert model.editState['reformatLocked']
assert model.setReformat('width','960') and model.editState['resizeHeight']==540
assert not model.setReformat('width','962')  # Fractional companion stays rejected, no silent rounding.
click(lock);assert not model.editState['reformatLocked']
assert model.setReformat('width','962') and model.editState['resizeHeight']==540
assert model.setReformat('preset','HD');assert not lock.isEnabled()
assert model.setReformat('preset','Custom');assert lock.isEnabled() and not lock.property('checked')
assert model.setCropRect(0,0,640,480);model.setOperationEnabled('crop',True)
assert model.setReformat('pixel_aspect','4/3');click(lock)
assert model.setReformat('width','960') and model.editState['resizeHeight']==720
assert model.setReformat('height','600') and model.editState['resizeWidth']==800
assert model.setReformat('preset','HD') and lock.property('checked') and not lock.isEnabled()
assert model.setReformat('preset','Custom') and lock.property('checked') and lock.isEnabled()
click(lock);assert model.setReformat('width','962') and model.editState['resizeHeight']==720
for placement in ('fit','fill','stretch'):
    assert model.setReformat('placement',placement) and model.previewPlan['valid']
assert window.findChild(QObject,'standaloneReformatButton') is None
for size in ((800,560),(1280,800)):
    window.setWidth(size[0]);window.setHeight(size[1]);QTest.qWait(240)
    for mode in ('scale','format'):
        model.setReformat('mode',mode);QTest.qWait(80)
        label=item('pixelAspectLabel');field=item('pixelAspectInput')
        a=label.mapToScene(QPointF(0,label.height()));b=field.mapToScene(QPointF())
        assert b.y()>a.y() and abs(a.x()-b.x())<.1
        for name in ('pixelAspect169','pixelAspectSource'):
            button=item(name);assert abs(button.mapToScene(QPointF()).y()-b.y())<4
    model.selectOperation('output');QTest.qWait(230)
    assert window.findChild(QObject,'exportButton') is None
    button=item('exportStart');footer=item('outputActionArea');scroll=item('inspectorScroll');view=scroll.property('contentItem')
    p=button.mapToScene(QPointF());f=footer.mapToScene(QPointF())
    assert button.isVisible() and f.y()<=p.y() and p.y()+button.height()<=window.height()
    assert window.findChild(QObject,'headerExportButton') is None
    assert str(button.property('background').property('color').name())=='#5aa9ff'
    assert scroll.mapToScene(QPointF(0,scroll.height())).y()<=f.y()
    view.setProperty('contentY',max(0,view.property('contentHeight')-view.height()));QTest.qWait(40)
    assert button.mapToScene(QPointF())==p
    assert scroll.property('contentWidth')<=scroll.property('availableWidth')+.1
    assert model.output.setSetting('container','mov') and model.output.setSetting('codec','prores')
    assert item('exportProresQuality').property('count')==6
    assert model.output.setSetting('prores_quality','5') and 'alpha omitted' in model.output.summary['message']
    assert not model.output.setSetting('prores_quality','6')
    model.output.startExport();QTest.qWait(30)
    assert len(exports.requests) and item('exportProgress').isVisible() and item('exportCancel').isVisible()
    assert not button.isEnabled();click(item('exportCancel'));assert not model.output.busy
    model.selectOperation('resize');QTest.qWait(230)
assert not exports.busy
'''+CLEANUP)
    assert result.returncode==0,result.stdout+result.stderr

@pytest.mark.integration
def test_fit_from_pointer_navigation_and_fullscreen_control_focus(run_python):
    result = run_python(EXPORT_SETUP + r'''
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QWheelEvent
load(Path.cwd()/'fit-from-controls.mp4')
def zoom_pan_and_fit():
    target=item('viewerZoomInput')
    point=target.mapToScene(QPointF(target.width()/2,target.height()/2))
    pos=QPoint(round(point.x()),round(point.y()))
    item('viewerZoomReadout').forceActiveFocus()
    assert window.activeFocusItem().objectName()=='viewerZoomReadout'
    event=QWheelEvent(point,point,QPoint(),QPoint(0,480),Qt.NoButton,Qt.NoModifier,Qt.ScrollUpdate,False)
    QCoreApplication.sendEvent(window,event);QTest.qWait(30)
    assert model.viewerViewport.zoomText!='100.0%'
    QTest.mousePress(window,Qt.MiddleButton,Qt.NoModifier,pos)
    QTest.mouseMove(window,pos+QPoint(32,20))
    QTest.mouseRelease(window,Qt.MiddleButton,Qt.NoModifier,pos+QPoint(32,20))
    assert model.viewerViewport.panX or model.viewerViewport.panY
    item('viewerZoomReadout').forceActiveFocus()
    QTest.keyClick(window,Qt.Key_F);QTest.qWait(25)
    assert model.viewerViewport.zoomText=='100.0%'
    assert model.viewerViewport.panX==model.viewerViewport.panY==0
player=playback._player;renderer=item('viewerVideoOutput')
zoom_pan_and_fit()
click(item('fullscreenButton'));assert playback.fullscreen
zoom_pan_and_fit()
QTest.keyClick(window,Qt.Key_Escape);QTest.qWait(90);assert not playback.fullscreen
zoom_pan_and_fit()
assert (playback._player,item('viewerVideoOutput'))==(player,renderer)
model.selectOperation('output');QTest.qWait(220)
field=item('exportFilename');field.forceActiveFocus();field.setProperty('text','')
model.viewerViewport.wheel(120,120,3);zoom=model.viewerViewport.zoomText
QTest.keyClick(window,Qt.Key_F)
assert field.property('text')=='f' and model.viewerViewport.zoomText==zoom
''' + CLEANUP)
    assert result.returncode == 0, result.stdout + result.stderr
