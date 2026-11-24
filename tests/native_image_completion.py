"""Finite native GUI product proof, source fixtures and sparse independent paint."""
import sys
sys.dont_write_bytecode=True
import os,time,json,hashlib,logging,threading,traceback
from pathlib import Path
from PySide6.QtCore import QTimer,QEventLoop,QUrl,QPointF,QSize,Qt,QObject,QMetaObject,QMimeData,Slot
from PySide6.QtGui import QGuiApplication,QImage,QColor,QPainter,QDragEnterEvent,QDragMoveEvent,QDropEvent
from PySide6.QtQml import QQmlApplicationEngine,QQmlExpression
from PySide6.QtTest import QTest
import shiboken6
from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.resources.paths import qml_root
from native_window_policy import load_native_shell,resize_native_window,close_native_windows


def main(root,video):
    root=root.resolve();video=video.resolve()
    root.mkdir(exist_ok=False);fixtures=Path(sys.argv[3]).resolve() if len(sys.argv)>3 else root/'source';fresh=not fixtures.exists();fixtures.mkdir(exist_ok=True);dest=root/'output';dest.mkdir()
    app=QGuiApplication([]);rows=[];result={'checks':[],'rows':rows}
    watchdog=threading.Timer(110,lambda:os._exit(124));watchdog.daemon=True;watchdog.start()
    for n in range(103) if fresh else ():
        image=QImage(1550,1400,QImage.Format_RGBA8888);image.fill(QColor(0,255,0,0));p=QPainter(image)
        p.fillRect(520,0,510,1400,QColor(200,n,40,128));p.fillRect(1030,0,520,1400,QColor(40,70+n,180,255));p.end()
        assert image.save(str(fixtures/f'picture_{1001+n:04d}.png'))
    if fresh:del image
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in fixtures.iterdir()}
    model=build_media_view_model(logging.getLogger('completion-native'));engine=QQmlApplicationEngine()
    engine.rootContext().setContextProperty('mediaViewModel',model);warnings=[]
    engine.warnings.connect(lambda values:warnings.extend(v.toString() for v in values))
    window=load_native_shell(engine,qml_root(),logging.getLogger('completion-native'));resize_native_window(window,800,560)
    pc=model.playback;preview=model.imagePreview
    class SceneCounter(QObject):
        count=0
        @Slot()
        def swapped(self):self.count+=1
    scene_counter=SceneCounter();window.frameSwapped.connect(scene_counter.swapped,Qt.QueuedConnection)
    beats=[];beat=QTimer();beat.setInterval(10);beat.timeout.connect(lambda:beats.append(time.monotonic()));beat.start()
    model.sequencePublished.connect(lambda slot,metrics:rows.append(dict(slot=slot,**metrics)))
    def wait(predicate,label='condition',seconds=12):
        if predicate():return
        loop=QEventLoop();poll=QTimer();poll.setInterval(5);deadline=QTimer();deadline.setSingleShot(True);expired=[]
        poll.timeout.connect(lambda:loop.quit() if predicate() else None);deadline.timeout.connect(lambda:(expired.append(True),loop.quit()))
        poll.start();deadline.start(int(seconds*1000));loop.exec();poll.stop();deadline.stop()
        assert not expired and predicate(),(label,preview.status,model.output.message)
    def find(name):
        def visit(item):
            if item.objectName()==name:return item
            for child in item.childItems():
                answer=visit(child)
                if answer is not None:return answer
        return visit(window.contentItem())
    def grab(item,size=QSize(320,280)):
        # Scene progress only, never used to release GPU leases. Actual pixels
        # below remain the independent paint criterion.
        for _ in range(2):
            before=scene_counter.count;window.update();wait(lambda:scene_counter.count>before,'scene update')
        capture=item.grabToImage(size);ready=[];capture.ready.connect(lambda:ready.append(True));wait(lambda:ready,'paint');return capture.image()
    def click(item):
        assert item is not None and item.isVisible() and item.isEnabled()
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint())
    def conversion(kind,container,name,matte=''):
        model.selectOperation('output')
        for key,value in [('kind',kind),('container',container),('folder',str(dest)),('filename',name),('matte',matte)]:
            assert model.output.setSetting(key,value),(key,value)
        assert model.output.canExport,model.output.summary
        assert find('exportStart') is not None and find('exportStart').isVisible()
        click(find('exportStart'))
        assert model.output.state in ('Running','Completed'),model.output.message
        wait(lambda:not model.output.busy,'conversion',25);model.output.refresh()
        assert model.output.state=='Completed',(model.output.state,model.output.message)
        result['checks'].append('GUI Output '+kind+' -> '+container)
    try:
        wait(lambda:preview._backend is not None,'backend');result['backend']=preview._backend
        result['dpr']=window.devicePixelRatio();viewer=find('viewerVideoOutput');sink=pc._presentation_sink
        start=time.perf_counter();picker=window.findChild(QObject,'mediaFileDialog');assert picker.setProperty('selectedFile',QUrl.fromLocalFile(str(fixtures/'picture_1001.png')));assert QMetaObject.invokeMethod(picker,'accepted')
        wait(lambda:bool(model.sequenceConfirmation),'unified discovery')
        assert model.sequenceConfirmation['count']==103 and model.sequenceConfirmation['resolution']=='1550×1400'
        dialog=window.findChild(QObject,'sequenceConfirmationDialog');assert dialog is not None and dialog.property('visible')
        expression=QQmlExpression(engine.rootContext(),dialog,'popupType === 0');assert expression.evaluate()[0] is True
        editor=dialog.findChild(QObject,'sequenceFpsConfirmation');editor.setProperty('editText','24/0');QMetaObject.invokeMethod(dialog,'accept')
        wait(lambda:bool(model.sequenceConfirmation.get('error')) and dialog.property('visible'),'invalid FPS remains correctable')
        assert not model.activeMediaId;editor.setProperty('editText','24/1')
        screenshot=window.grabWindow().scaledToWidth(640);assert screenshot.save(str(root/'fps-dialog.png'))
        QMetaObject.invokeMethod(dialog,'reject');assert not model.activeMediaId and not model.sequenceConfirmation
        # The same unified decision is exercised by drop and picker commands.
        mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(fixtures/'picture_1001.png'))])
        zone=find('homeDropArea');point=zone.mapToScene(QPointF(zone.width()/2,zone.height()/2))
        enter=QDragEnterEvent(point.toPoint(),Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier);app.sendEvent(window,enter);assert enter.isAccepted()
        move=QDragMoveEvent(point.toPoint(),Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier);app.sendEvent(window,move)
        drop=QDropEvent(point,Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier);app.sendEvent(window,drop);assert drop.isAccepted()
        wait(lambda:bool(model.sequenceConfirmation));editor=dialog.findChild(QObject,'sequenceFpsConfirmation')
        editor.setProperty('editText','24000/1001');QMetaObject.invokeMethod(dialog,'accept')
        wait(lambda:not preview.busy and model.sourceKind=='sequence','1550 activation')
        def display_sample(width,height):
            divisor=pc._image_prepared.raster.plan.divisor
            assert divisor in (1,2,4)
            assert (pc.imageWidth,pc.imageHeight)==((width+divisor-1)//divisor,(height+divisor-1)//divisor)
        assert not preview.status
        display_sample(1550,1400)
        assert len(model.mediaItems)==1 and model._workspace.active.metadata.width==1550
        assert pc._player is None and pc._staging_sink is None
        wait(lambda:pc._image_ready,'initial image scene ready')
        canvas=find('videoViewport');checker=find('imageCheckerboard');checker.setProperty('visible',False)
        background=canvas.childItems()[0]
        result['paint']=[]
        for matte in (0,255):
            background.setProperty('color',QColor(matte,matte,matte));image=grab(canvas)
            for x,color,alpha in [(250,(0,255,0),0),(770,(200,0,40),128),(1300,(40,70,180),255)]:
                pos=viewer.mapToItem(canvas,QPointF(x,700));actual=image.pixelColor(round(pos.x()*image.width()/canvas.width()),round(pos.y()*image.height()/canvas.height())).getRgb()
                expected=tuple(round(c*alpha/255+matte*(255-alpha)/255) for c in color)
                assert max(abs(a-b) for a,b in zip(actual,expected))<=1,(matte,x,actual,expected)
                result['paint'].append(dict(matte=matte,source_x=x,actual=actual,expected=expected))
        result['cold_first_correct_paint_ms']=(time.perf_counter()-start)*1000
        checker.setProperty('visible',True);background.setProperty('color',QColor(0,0,0))
        assert window.grabWindow().scaledToWidth(640).save(str(root/'viewer.png'))
        result['checks'].append('103-member 1550x1400 alpha discovery/confirmation/cancel/drop; reduced native alpha0/128/255 on dark/light')
        assert find('imagePreviewQuality') is None
        assert find('viewerChannel').isVisible() and not preview.status
        display_sample(1550,1400)
        pc.seek(3);wait(lambda:not preview.busy and pc.sequence.slot==3);pc.stepFrame(1);wait(lambda:not preview.busy and pc.sequence.slot==4)
        model.setOperationEnabled('trim',True);assert model.setTrimRange(2,5);pc.seek(2);wait(lambda:not preview.busy and pc.sequence.slot==2)
        initial=len(rows);pc.togglePlayback();wait(lambda:len(rows)>=initial+4,'alpha playback',15);pc.togglePlayback();wait(lambda:not preview.busy)
        assert [r['slot'] for r in rows[initial:]]==[3,4,2,3]
        result['playback']={'identities':[r['slot'] for r in rows[initial:]],'requested':pc.sequence.requested,'delivered':pc.sequence.delivered,'late':pc.sequence.late,'screen_fps':'not inferred from publication callbacks'}
        # F is Fit-only; W is absent. Reformat retains its exact settings off/on.
        model.viewerViewport.wheel(120,120,2);find('workspaceKeyboardFocus').forceActiveFocus();window.requestActivate();QTest.keyClick(window,Qt.Key_F)
        before=model.previewPlan.copy();QTest.keyClick(window,Qt.Key_W);assert model.previewPlan==before
        assert find('viewerCompareSource') is None and find('viewerCompareOutput') is None
        model.selectOperation('resize');assert model.setReformat('scale','0.5');model.setOperationEnabled('resize',True)
        saved=model.editState['resizeWidth'];model.setOperationEnabled('resize',False);assert model.editState['resizeWidth']==saved
        model.setOperationEnabled('resize',True);assert model.editState['resizeWidth']==saved;model.setOperationEnabled('resize',False)
        # A low-resource source conversion can be completed while the GUI retains
        # the independently admitted reduced image. No Viewer pixels enter export.
        model.setOperationEnabled('trim',False)
        bad_image_probe=root/'bad-on-image.mp4';bad_image_probe.write_bytes(b'not video');active=model.activeMediaId
        assert model.openMedia(QUrl.fromLocalFile(str(bad_image_probe)));wait(lambda:model.canOpen,'failed probe on image')
        assert model.activeMediaId==active and pc.imageMode and preview._monitor.isActive()
        conversion('frame','png','selected-original')
        from mediaforge.infrastructure.images.reader import QtImageReader
        exported=QtImageReader().probe(str(dest/'selected-original.png'));assert (exported.facts.width,exported.facts.height)==(1550,1400)
        model.setOperationEnabled('trim',True);assert model.setTrimRange(2,5)
        conversion('media','mp4','trimmed-sequence','black')
        model.windowPresentation.enter(window);wait(lambda:pc.fullscreen);model.windowPresentation.exit();wait(lambda:not pc.fullscreen)
        assert model.openMedia(QUrl.fromLocalFile(str(video)));wait(lambda:model.sourceKind=='video' and pc.framesReliable,'video',15)
        assert pc._player is not None and pc._presentation_sink is sink and find('viewerVideoOutput') is viewer
        conversion('frame','png','video-current')
        conversion('sequence','tiff','video-pictures')
        active=model.activeMediaId;serial=pc._image_serial
        bad=root/'bad.mp4';bad.write_bytes(b'not video');assert model.openMedia(QUrl.fromLocalFile(str(bad)))
        wait(lambda:model.canOpen,'failed video probe');assert model.activeMediaId==active and not pc.imageMode
        assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items())
        result['source_hashes']=hashes;result['max_heartbeat_gap_ms']=max((b-a)*1000 for a,b in zip(beats,beats[1:]))
        result['logical_peak']=model._image_service.budget.peak;result['resource_sample']=model._image_service.resources.last
        assert not warnings,warnings
        result['passed']=True
    except BaseException:result['passed']=False;result['failure']=traceback.format_exc()
    finally:
        try:model.shutdown();close_native_windows(window);shiboken6.delete(engine);result['shutdown']='clean'
        except BaseException:result['shutdown']=traceback.format_exc();result['passed']=False
        watchdog.cancel();beat.stop();(root/'results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k not in ('source_hashes','rows')},indent=2),flush=True)
    assert result['passed'],result.get('failure')


if __name__=='__main__':main(Path(sys.argv[1]),Path(sys.argv[2]))
