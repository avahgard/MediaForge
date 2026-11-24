"""Owner UX replacement: Crop Edit/Preview admission, W inert, mouse/touch safety."""
import json,os,subprocess,sys
from pathlib import Path
import pytest
from tests.presentation.test_export_qml import EXPORT_SETUP
from tests.presentation.test_workspace_qml import CLEANUP


CHECKS=r'''
import json
from PySide6.QtGui import QImage,QInputDevice
from PySide6.QtMultimedia import QVideoFrame
touch=QTest.createTouchDevice(QInputDevice.DeviceType.TouchScreen)
rows=[]
def crop_values():return tuple(model.editState[k] for k in ('cropX','cropY','cropWidth','cropHeight'))
def pixel(obj,x=None,y=None):
    return obj.mapToScene(QPointF(obj.width()/2 if x is None else x,obj.height()/2 if y is None else y)).toPoint()
def gesture(point,dx=20,dy=0,touching=False):
    end=point+QPoint(dx,dy)
    if touching:
        QTest.touchEvent(window,touch).press(0,point,window).commit();QTest.qWait(20)
        QTest.touchEvent(window,touch).move(0,end,window).commit();QTest.qWait(30)
        QTest.touchEvent(window,touch).release(0,end,window).commit();QTest.qWait(30)
    else:
        QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point)
        QTest.mouseMove(window,end);QTest.qWait(30)
        QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,end);QTest.qWait(30)
def select_source(source,keyboard=False):
    before=model.editState.copy();summary=model.output.summary.copy()
    # Owner replaced Source/Output with explicit Crop Preview/edit permission.
    if keyboard:
        item('workspaceKeyboardFocus').forceActiveFocus();QTest.keyClick(window,Qt.Key_W);QTest.qWait(30)
        assert model.editState==before  # W is inert outside text entry.
    model.setCropPreview(not source);app.processEvents()
    assert {k:v for k,v in model.editState.items() if k!='cropPreview'}=={k:v for k,v in before.items() if k!='cropPreview'}
    assert model.output.summary==summary
def visible_edit(allowed):
    overlay=item('cropOverlay');handle=item('cropHandle-left-top')
    assert model.cropEditAllowed==allowed and model.previewPlan['editing']==allowed
    assert overlay.isVisible()==allowed and overlay.isEnabled()==allowed
    assert handle.isVisible()==allowed and handle.isEnabled()==allowed
    return overlay,handle
def paint_policy(name):
    if 'native_proof_root' not in globals():return
    before=swaps[0];window.update();wait(app,lambda:swaps[0]>before,'policy paint')
    image=window.grabWindow();assert not image.isNull()
    rect=item('cropOverlayRect');p=rect.mapToScene(QPointF(1,rect.height()/2))
    dpr=image.width()/window.width();c=image.pixelColor(round(p.x()*dpr),round(p.y()*dpr))
    purple=c.blue()>c.green()+60 and c.red()<210
    assert purple==model.cropEditAllowed,(name,[c.red(),c.green(),c.blue()])
    assert image.save(str(native_proof_root/(name+'.png')))
player,renderer=playback._player,item('viewerVideoOutput')
model.setOperationEnabled('crop',True);assert model.setCropRect(100,20,960,540)
model.setOperationEnabled('resize',True);assert model.setReformat('pixel_aspect','4/3')
model.selectOperation('crop');QTest.qWait(240)
overlay,handle=visible_edit(True)
paint_policy('source-edit')
select_source(False)
overlay,handle=visible_edit(False);before=crop_values()
paint_policy('output-no-overlay')
gesture(pixel(item('cropOverlayRect')));gesture(pixel(handle))
assert crop_values()==before and not model.previewSource
rows.append(dict(case='original-reviewer-output',crop=list(before),overlay=False,handle=False))
# Central source coordinates keep gestures away from Viewer chrome at every zoom.
for fullscreen in (False,True):
    if fullscreen:click(item('fullscreenButton'));assert playback.fullscreen;QTest.qWait(200)
    for zoom in (False,True):
        select_source(True);model.viewerViewport.fit();assert model.setCropRect(480,270,960,540)
        if zoom:
            canvas=item('videoViewport')
            model.viewerViewport.wheel(canvas.width()/2,canvas.height()/2,2);model.viewerViewport.pan(-10,5)
        QTest.qWait(40);overlay,handle=visible_edit(True)
        scale=overlay.property('scaleX');before=crop_values();gesture(pixel(item('cropOverlayRect')))
        expected=round((before[0]+20/scale)/2)*2
        assert crop_values()==(expected,before[1],before[2],before[3]),(before,crop_values(),scale)
        # Source handle edits anchor the opposite edge in original coded coordinates.
        before=crop_values();scale=overlay.property('scaleX');gesture(pixel(handle),12)
        expected=round((before[0]+12/scale)/2)*2
        assert crop_values()==(expected,before[1],before[2]-(expected-before[0]),before[3])
        saved=crop_values();select_source(False,keyboard=zoom)
        overlay,handle=visible_edit(False)
        for touching in (False,True):
            gesture(pixel(item('cropOverlayRect')),20,touching=touching)
            gesture(pixel(handle),12,touching=touching)
            assert crop_values()==saved and not model.previewSource
        select_source(True,keyboard=not zoom);visible_edit(True);assert crop_values()==saved
        # Touch synthesis edits Source but is forbidden for Output above.
        scale=overlay.property('scaleX');gesture(pixel(item('cropOverlayRect')),20,touching=True)
        expected=round((saved[0]+20/scale)/2)*2
        assert crop_values()==(expected,saved[1],saved[2],saved[3])
        # Revoking permission during an existing body grab also blocks continuation.
        saved=crop_values();p=pixel(item('cropOverlayRect'))
        QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,p)
        model.setCropPreview(True);app.processEvents()
        QTest.mouseMove(window,p+QPoint(20,0));QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,p+QPoint(20,0))
        QTest.qWait(30);assert crop_values()==saved;visible_edit(False)
        assert not model.dragCrop('move',*saved,20.,0.)
        rows.append(dict(case='input-policy',fullscreen=fullscreen,zoom=zoom,source_drag_x=expected,
                         output_crop=list(saved),source_scale=scale,touch_checked=True))
    if fullscreen:
        if 'activate_native_input' in globals():activate_native_input(window)
        item('workspaceKeyboardFocus').forceActiveFocus();QTest.keyClick(window,Qt.Key_Escape);QTest.qWait(100)
        assert not playback.fullscreen
model.viewerViewport.fit();select_source(True)
assert model.setCropRect(480,270,960,540)
model.setCropPreview(True);app.processEvents();visible_edit(False)
visible_edit(False)  # Crop Preview never admits editing.
model.setCropPreview(False);app.processEvents();visible_edit(True)
model.setOperationEnabled('crop',False);app.processEvents();visible_edit(False)
model.setOperationEnabled('resize',False);app.processEvents();visible_edit(True)
assert model.previewSource  # Accepted disabled-Crop preparation on an upstream unprocessed source.
saved=crop_values();gesture(pixel(item('cropOverlayRect')));assert crop_values()!=saved
saved=crop_values();select_source(False);visible_edit(False)
gesture(pixel(item('cropOverlayRect')));gesture(pixel(item('cropHandle-left-top')))
assert crop_values()==saved
select_source(True);visible_edit(True)
model.setOperationEnabled('resize',True);app.processEvents();visible_edit(False)
model.setOperationEnabled('crop',True);app.processEvents();visible_edit(True)
assert crop_values()==saved
assert (playback._player,item('viewerVideoOutput'))==(player,renderer) and not exports.requests
print('P01_INPUT_PROOF '+json.dumps(dict(cases=rows,processing_calls=len(exports.requests),warnings=warnings)))
'''


@pytest.mark.integration
@pytest.mark.parametrize('dpi',['1','1.5','2'])
def test_crop_comparison_visibility_and_mouse_touch_admission(run_python,isolated_environment,tmp_path,dpi):
    isolated_environment['QT_SCALE_FACTOR']=dpi
    setup=EXPORT_SETUP+r'''
load(Path.cwd()/'crop-comparison.mp4')
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoFrame
image=QImage(1920,1080,QImage.Format_RGB32);image.fill(Qt.yellow)
frame=QVideoFrame(image);frame.setStartTime(0);playback._staging_sink.setVideoFrame(frame)
'''
    result=run_python(setup+CHECKS+CLEANUP)
    (tmp_path/'input-events.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.native
@pytest.mark.integration
def test_native_crop_comparison_event_admission(tmp_path):
    from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
    runtime=resolve_ffmpeg_runtime();source=tmp_path/'source 1920.mp4'
    subprocess.run([str(runtime.ffmpeg),'-v','error','-n','-f','lavfi','-i',
        'color=yellow:s=1920x1080:r=25:d=0.4','-c:v','libx264','-pix_fmt','yuv420p',str(source)],
        capture_output=True,check=True,timeout=20)
    code=r'''
import logging,time,sys,json
from pathlib import Path
import shiboken6
from PySide6.QtCore import QObject,QPoint,QPointF,QUrl,Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.resources.paths import qml_root
sys.path.insert(0,str(Path.cwd()/'tests'))
from native_window_policy import load_native_shell,close_native_windows
from native_workspace_regressions import find,wait,activate_native_input
app=QGuiApplication([]);model=build_media_view_model(logging.getLogger('p01'))
playback=model.playback
class Exports:requests=[]
exports=Exports()
for bridge in (model.trim,model.crop,model.resize,model.output):
    def forbidden(args,**kwargs):exports.requests.append(args);raise AssertionError('Unexpected processing from comparison')
    bridge._use_case._executor._processes._factory=forbidden
engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('mediaViewModel',model)
warnings=[];engine.warnings.connect(lambda values:warnings.extend(v.toString() for v in values))
window=load_native_shell(engine,qml_root(),logging.getLogger('p01'))
native_proof_root=Path(sys.argv[1]).parent
swaps=[0];window.frameSwapped.connect(lambda:swaps.__setitem__(0,swaps[0]+1))
def item(name):return find(window,name)
def click(target):
    p=target.mapToScene(QPointF(target.width()/2,target.height()/2)).toPoint()
    QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,p);QTest.qWait(40)
assert model.openMedia(QUrl.fromLocalFile(sys.argv[1]))
wait(app,lambda:model.state=='loaded' and model.canOpen,'open')
wait(app,lambda:playback.framesReliable and item('viewerVideoOutput').property('videoSink').videoFrame().isValid(),'frame',timeout=20)
activate_native_input(window)
'''
    cleanup=r'''
assert not warnings,warnings
model.shutdown();close_native_windows(window);shiboken6.delete(engine)
'''
    env=os.environ.copy();env.pop('QT_QPA_PLATFORM',None)
    result=subprocess.run([sys.executable,'-c',code+CHECKS+cleanup,str(source)],cwd=Path(__file__).resolve().parents[2],
        env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
    (tmp_path/'native-events.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode==77 and 'NATIVE SCREEN SKIP:' in result.stderr:pytest.skip(result.stderr)
    assert result.returncode==0,result.stdout+result.stderr
