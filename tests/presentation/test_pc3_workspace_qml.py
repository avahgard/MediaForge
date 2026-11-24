import pytest

from test_workspace_qml import SETUP,CLEANUP


@pytest.mark.integration
def test_fullscreen_shared_frame_trim_gesture_and_read_only_state(run_python):
    result=run_python(SETUP+r'''
load(Path.cwd()/'fullscreen.mp4')
model.setOperationEnabled('trim',True)
assert model.setTrimRange(1000,8000)
normal=item('mediaTimeline')
playback._indexing=True;playback.changed.emit()
assert item('frameReliabilityText').property('text')=='Indexing frames…'
window.enterViewerFullscreen()
wait(lambda:playback.fullscreen)
full=item('fullscreenTimeline')
assert item('fullscreenFrameReliabilityText').property('text')=='Indexing frames…'
assert full.property('handleIn')/full.width()==0.1
assert full.property('handleOut')/full.width()==0.8
playback._indexing=False;playback.changed.emit()
assert item('fullscreenFrameReliabilityText').property('text')==playback.frameText
start=item('fullscreenTrimInHandle').mapToScene(QPointF(4,10))
QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,QPoint(round(start.x()),round(start.y())))
assert playback._controls_interactions==1
end=QPoint(round(start.x()+full.width()*0.1),round(start.y()-180))
QTest.mouseMove(window,end)
QTest.qWait(1400)
assert playback.controlsVisible and playback._controls_interactions==1
assert 1800<model.editState['trimIn']<2200
QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,end)
assert playback._controls_interactions==0
saved=model.editState['trimIn']
window.exitViewerFullscreen();wait(lambda:not playback.fullscreen)
assert abs(normal.property('handleIn')/normal.width()-saved/10000)<0.001
assert model.setTrimRange(1500,7500)
window.enterViewerFullscreen();wait(lambda:playback.fullscreen)
assert abs(full.property('handleIn')/full.width()-0.15)<0.001
model.setOperationEnabled('trim',False)
saved=(model.editState['trimIn'],model.editState['trimOut'])
for name in ('fullscreenTrimInHandle','fullscreenTrimOutHandle'):
    handle=item(name);assert handle.opacity()==0.35
    start=handle.mapToScene(QPointF(4,10));a=QPoint(round(start.x()),round(start.y()));b=QPoint(a.x()+30,a.y())
    QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,a);QTest.mouseMove(window,b);QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,b)
item('workspaceKeyboardFocus').forceActiveFocus()
for key,modifier in ((Qt.Key_I,Qt.NoModifier),(Qt.Key_O,Qt.NoModifier),(Qt.Key_I,Qt.ShiftModifier),(Qt.Key_O,Qt.ShiftModifier)):
    QTest.keyClick(window,key,modifier)
assert (model.editState['trimIn'],model.editState['trimOut'])==saved
window.exitViewerFullscreen()
''' + CLEANUP)
    assert result.returncode==0,result.stdout+result.stderr
