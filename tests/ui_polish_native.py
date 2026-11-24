"""One bounded native comparison/input/footer proof on the persistent real player."""
import argparse,hashlib,json,logging
from pathlib import Path
import shiboken6
from PySide6.QtCore import QPoint,QPointF,QUrl,Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.resources.paths import qml_root
from native_window_policy import load_native_shell,resize_native_window,close_native_windows
from native_workspace_regressions import find,wait,activate_native_input


def run(root,source):
    root.mkdir(parents=True,exist_ok=False);digest=hashlib.sha256(source.read_bytes()).hexdigest()
    app=QGuiApplication([]);model=build_media_view_model(logging.getLogger('ui-polish'))
    engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('mediaViewModel',model)
    warnings=[];engine.warnings.connect(lambda errors:warnings.extend(e.toString() for e in errors))
    window=load_native_shell(engine,qml_root(),logging.getLogger('ui-polish'));proof=dict(cases=[],processing=[])
    swaps=[0];window.frameSwapped.connect(lambda:swaps.__setitem__(0,swaps[0]+1))
    for bridge in (model.trim,model.crop,model.resize,model.output):
        def forbidden(args,**_):proof['processing'].append(args);raise AssertionError('Comparison launched processing')
        bridge._use_case._executor._processes._factory=forbidden
    def key(k):
        activate_native_input(window);find(window,'workspaceKeyboardFocus').forceActiveFocus()
        QTest.keyClick(window,k);QTest.qWait(40)
    def click(name):
        obj=find(window,name);p=obj.mapToScene(QPointF(obj.width()/2,obj.height()/2))
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,QPoint(round(p.x()),round(p.y())))
        QTest.qWait(40)
    def paint(name):
        revision=swaps[0];window.update();wait(app,lambda:swaps[0]>revision,'paint '+name)
        image=window.grabWindow();assert not image.isNull();assert image.save(str(root/(name+'.png')))
        return image
    try:
        assert model.openMedia(QUrl.fromLocalFile(str(source)))
        wait(app,lambda:model.state=='loaded' and model.canOpen,'media')
        renderer=find(window,'viewerVideoOutput');sink=renderer.property('videoSink')
        wait(app,lambda:sink.videoFrame().isValid() and model.playback.framesReliable,'decoded frame',timeout=20)
        player=model.playback._player;initial_frame=sink.videoFrame().startTime();position=model.playback.position
        model.setOperationEnabled('crop',True);assert model.setCropRect(512,0,512,768)
        model.setOperationEnabled('resize',True)
        assert model.setReformat('mode','format') and model.setReformat('preset','Custom')
        assert model.setReformat('width','1024') and model.setReformat('height','768')
        assert model.setReformat('placement','stretch') and model.setReformat('pixel_aspect','16:9')
        model.selectOperation('output');QTest.qWait(240)
        settings=model.editState.copy();summary=model.output.summary.copy()
        for fullscreen in (False,True):
            if fullscreen:click('fullscreenButton');assert model.playback.fullscreen
            for source_view in (True,False,True,False):
                key(Qt.Key_W);assert model.previewSource==source_view
                key(Qt.Key_F);QTest.qWait(80)
                canvas=find(window,'videoViewport');aspect=canvas.width()/canvas.height()
                assert abs(aspect-(4/3 if source_view else 16/9))<.00001
                image=paint(('full' if fullscreen else 'normal')+('-source' if source_view else '-output')+str(len(proof['cases'])))
                point=canvas.mapToScene(QPointF(canvas.width()*.25,canvas.height()*.5))
                dpr=image.width()/window.width();color=image.pixelColor(round(point.x()*dpr),round(point.y()*dpr))
                assert color.red()>180 and color.blue()<80
                assert (color.green()>180)==source_view  # Original yellow left half vs cropped red picture.
                assert model.playback._player is player and find(window,'viewerVideoOutput') is renderer
                assert sink.videoFrame().startTime()==initial_frame and model.playback.position==position
                assert model.editState==settings and model.output.summary==summary
                proof['cases'].append(dict(fullscreen=fullscreen,source=source_view,aspect=aspect,
                    sample_rgb=[color.red(),color.green(),color.blue()],frame_us=initial_frame,position_ms=position))
            model.viewerViewport.wheel(200,100,3);model.viewerViewport.pan(-40,20);key(Qt.Key_F)
            assert model.viewerViewport.zoomText=='100.0%' and model.viewerViewport.panX==model.viewerViewport.panY==0
            assert model.playback.fullscreen==fullscreen
            if fullscreen:key(Qt.Key_Escape);assert not model.playback.fullscreen
        for size in ((800,560),(1280,800)):
            resize_native_window(window,*size);model.selectOperation('resize');QTest.qWait(240)
            paint(str(size[0])+'-reformat')
            model.selectOperation('output');QTest.qWait(240)
            footer=find(window,'outputActionArea');button=find(window,'exportStart');scroll=find(window,'inspectorScroll')
            assert scroll.mapToScene(QPointF(0,scroll.height())).y()<footer.mapToScene(QPointF()).y()
            start=button.mapToScene(QPointF());view=scroll.property('contentItem')
            view.setProperty('contentY',max(0,view.property('contentHeight')-view.height()));QTest.qWait(100)
            assert button.mapToScene(QPointF())==start and button.isVisible()
            assert button.mapToScene(QPointF(button.width(),0)).x()<=find(window,'inspectorVerticalBar').mapToScene(QPointF()).x()-10
            paint(str(size[0])+'-output')
        assert not proof['processing'] and not warnings,warnings
        assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
        proof.update(status='PASS',source_sha256=digest,screen=window.screen().name(),dpr=window.devicePixelRatio(),warnings=warnings)
        print('PASS: actual Source/Output colors and geometry, identical frame/position/player/renderer, F/W/fullscreen, fixed footer, no processing')
    except BaseException as error:proof.update(status='FAILED',failure=repr(error));raise
    finally:
        (root/'ui-polish-proof.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
        model.shutdown();close_native_windows(window);shiboken6.delete(engine)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--source',type=Path,required=True)
    args=parser.parse_args();run(args.output.resolve(),args.source.resolve())
