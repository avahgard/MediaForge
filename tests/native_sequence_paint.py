"""Finite native product oracle; compact fixtures, no screenshot/export authority."""
import sys
sys.dont_write_bytecode=True
import os, time, json, hashlib, logging, traceback, threading
from pathlib import Path
from fractions import Fraction
from PySide6.QtCore import QTimer,QEventLoop,QUrl,QSize,QPointF,Qt,QMetaObject,QObject,Slot
from PySide6.QtGui import QGuiApplication,QImage,QColor,QPainter
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from PySide6.QtMultimedia import QMediaPlayer
import shiboken6
from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.resources.paths import qml_root
from native_window_policy import load_native_shell,resize_native_window,close_native_windows


def main(out,video,fixture_root=None):
    out.mkdir(exist_ok=False)
    os.environ['QML_DISABLE_DISK_CACHE']='1'
    app=QGuiApplication([])
    result={'rows':[],'checks':[],'paint':[]}
    watchdog=threading.Timer(100,lambda:os._exit(124));watchdog.daemon=True;watchdog.start()
    fixtures=fixture_root or out
    if fixture_root:
        retained=json.loads((fixture_root/'results.json').read_text(encoding='utf-8'))
        assert retained['passed'] and all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in retained['source_hashes'].items())
    paths=[];expected={}
    for kind,dimensions,count in [('jpg',(1920,1080),12),('png',(320,180),3),('tiff',(320,180),3),('uhd',(3840,2160),2)]:
        folder=fixtures/kind
        if not fixture_root: folder.mkdir()
        for slot in range(count):
            alpha=128 if kind=='tiff' else 255
            color=(45+slot*14,80+slot*5,180-slot*9)
            path=folder/f'кадр_{101+slot:04d}.{"jpg" if kind=="uhd" else kind}'
            if fixture_root:
                paths.append(path);expected[str(path)]=color
                continue
            image=QImage(*dimensions,QImage.Format_RGBA8888 if alpha<255 else QImage.Format_RGB32)
            image.fill(QColor(*color,alpha));p=QPainter(image)
            for n in range(24):p.fillRect(n*dimensions[0]//24,0,dimensions[0]//24,dimensions[1]//8,QColor((n*43+slot*7)%255,(n*17)%255,90,alpha))
            p.end();path=folder/f'кадр_{101+slot:04d}.{"jpg" if kind=="uhd" else kind}'
            assert image.save(str(path));paths.append(path);expected[str(path)]=color
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    model=build_media_view_model(logging.getLogger('sequence-native'))
    engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('mediaViewModel',model)
    warnings=[];engine.warnings.connect(lambda values:warnings.extend(v.toString() for v in values))
    window=load_native_shell(engine,qml_root(),logging.getLogger('sequence-native'));resize_native_window(window,800,560)
    pc=model.playback;preview=model.imagePreview
    class SceneCounter(QObject):
        count=0
        @Slot()
        def swapped(self):self.count+=1
    scene_counter=SceneCounter();window.frameSwapped.connect(scene_counter.swapped,Qt.QueuedConnection)
    result['screens']=[{'name':s.name(),'dpr':s.devicePixelRatio()} for s in app.screens()]
    heartbeat=[];beat=QTimer();beat.setInterval(10);beat.timeout.connect(lambda:heartbeat.append(time.perf_counter()));beat.start()
    model.sequencePublished.connect(lambda slot,metrics:result['rows'].append(dict(slot=slot,**metrics,published_at_ns=time.monotonic_ns(),
        logical=model._image_service.budget.reserved,resources=dict(model._image_service.resources.last))))

    def wait(test,label='condition',timeout=8):
        if test():return
        loop=QEventLoop();poll=QTimer();poll.setInterval(5);limit=QTimer();limit.setSingleShot(True);expired=[]
        poll.timeout.connect(lambda:loop.quit() if test() else None)
        limit.timeout.connect(lambda:(expired.append(True),loop.quit()))
        poll.start();limit.start(int(timeout*1000));loop.exec();poll.stop();limit.stop()
        assert not expired and test(),(label,preview.status,pc.frameText,model.state)

    def find(item,name):
        if item.objectName()==name:return item
        for child in item.childItems():
            answer=find(child,name)
            if answer is not None:return answer

    def click(item):
        assert item is not None and item.isVisible()
        pt=item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint()
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,pt)

    def capture(canvas):
        # Request draw progress after scene/matte changes before checking pixels.
        # These swaps never release leases or certify GPU resource retirement.
        for _ in range(2):
            before=scene_counter.count;window.update();wait(lambda:scene_counter.count>before,'draw progress')
        grab=canvas.grabToImage(QSize(320,180));ready=[];grab.ready.connect(lambda:ready.append(True));wait(lambda:ready,'paint grab')
        return grab.image()

    def paint(slot):
        wait(lambda:not preview.busy and pc.sequence.slot==slot,'slot paint ready')
        canvas=find(window.contentItem(),'videoViewport');vo=find(window.contentItem(),'viewerVideoOutput')
        picture=capture(canvas)
        pos=vo.mapToItem(canvas,QPointF(model._workspace.active.metadata.width/2,model._workspace.active.metadata.height/2))
        actual=picture.pixelColor(round(pos.x()*320/canvas.width()),round(pos.y()*180/canvas.height())).getRgb()
        source=pc._image_prepared.raster.source
        color=expected.get(source.path)
        if source.facts.alpha:
            assert pc._image_prepared.raster.display.pixelColor(pc.imageWidth//2,pc.imageHeight//2).alpha()==128
            # Independently verified opaque matte under the existing alpha layer.
            checker=find(window.contentItem(),'imageCheckerboard');checker.setProperty('visible',False)
            background=canvas.childItems()[0];background.setProperty('color',QColor(64,64,64))
            picture=capture(canvas)
            actual=picture.pixelColor(round(pos.x()*320/canvas.width()),round(pos.y()*180/canvas.height())).getRgb()
            color=tuple(round(v*128/255+64*127/255) for v in color)
            checker.setProperty('visible',True);background.setProperty('color',QColor(0,0,0))
        tolerance=4 if source.facts.format=='jpeg' else 1
        assert color and max(abs(a-b) for a,b in zip(actual,color))<=tolerance,(source.path,actual,color)
        assert source==model._workspace.active.metadata.source.manifest.members[slot]
        result['paint'].append(dict(slot=slot,path=source.path,actual=actual,expected=color))

    def sequence(path,rate='24/1'):
        assert model.openMedia(QUrl.fromLocalFile(str(path)))
        wait(lambda:bool(model.sequenceConfirmation),'discovery')
        assert model.sequenceConfirmation['fps']=='24' and 'ASSUMED' in model.sequenceConfirmation['authority']
        confirm=window.findChild(__import__('PySide6.QtCore',fromlist=['QObject']).QObject,'sequenceConfirmationDialog')
        assert confirm is not None and confirm.property('visible')
        editor=confirm.findChild(__import__('PySide6.QtCore',fromlist=['QObject']).QObject,'sequenceFpsConfirmation')
        assert editor is not None
        editor.setProperty('editText',rate)
        QMetaObject.invokeMethod(confirm,'accept')
        wait(lambda:not preview.busy and model.sourceKind=='sequence','activation')
        assert not preview.status,preview.status

    try:
        wait(lambda:preview._backend is not None,'renderer')
        result['backend']=preview._backend
        initial=time.perf_counter();sequence(paths[0]);paint(0)
        result['first_correct_paint_ms']=(time.perf_counter()-initial)*1000
        assert len(model.mediaItems)==1 and pc._player is None and not window.findChildren(QMediaPlayer)
        assert pc.frameText=='Frame 1 / 12'
        # Exact FPS editing without a new manifest or Tray item.
        # Actual keyboard decimal entry must never autocomplete 23.9 to 23.976.
        editor=find(window.contentItem(),'sequenceFpsEditor').property('contentItem')
        click(editor);editor.forceActiveFocus();QTest.keyClick(window,Qt.Key_A,Qt.ControlModifier)
        for key in [Qt.Key_2,Qt.Key_3,Qt.Key_Period,Qt.Key_9]:QTest.keyClick(window,key)
        assert editor.property('text')=='23.9'
        QTest.keyClick(window,Qt.Key_Return)
        assert model._workspace.active.sequence.rate==Fraction(239,10)
        control=find(window.contentItem(),'sequenceFpsEditor')
        for text,fps_expected in [('2997/125',Fraction(2997,125)),('2997/100',Fraction(2997,100)),
                ('2997/50',Fraction(2997,50)),('23.976000',Fraction(2997,125)),('29.970',Fraction(2997,100)),
                ('59.940',Fraction(2997,50))]:
            click(editor);QTest.keyClick(window,Qt.Key_A,Qt.ControlModifier)
            for char in text:
                QTest.keyClick(window,Qt.Key_Period if char=='.' else Qt.Key_Slash if char=='/' else getattr(Qt,'Key_'+char))
            QTest.keyClick(window,Qt.Key_Return)
            assert model._workspace.active.sequence.rate==fps_expected
            QTest.keyClick(window,Qt.Key_Tab);click(editor);QTest.keyClick(window,Qt.Key_Return)
            assert model._workspace.active.sequence.rate==fps_expected  # unchanged friendly text
            assert model._workspace.active.metadata.source.manifest.time(12,fps_expected)==Fraction(12,1)/fps_expected
        presets=control.findChild(QObject,'fpsPresetMenu')
        for index,fps_expected in [(0,Fraction(24000,1001)),(3,Fraction(30000,1001)),(6,Fraction(60000,1001))]:
            click(presets);QTest.keyClick(window,Qt.Key_Home)
            for _ in range(index):QTest.keyClick(window,Qt.Key_Down)
            QTest.keyClick(window,Qt.Key_Return)
            assert model._workspace.active.sequence.rate==fps_expected
            click(editor);QTest.keyClick(window,Qt.Key_Return)
            assert model._workspace.active.sequence.rate==fps_expected
        manifest_id=model._workspace.active.metadata.source.manifest_id
        for rate in ('24000/1001','30000/1001','25/1','30/1','24/1'):
            assert model.setSequenceFps(rate) and model._workspace.active.sequence.rate==Fraction(rate)
        assert model._workspace.active.metadata.source.manifest_id==manifest_id
        assert not model.setSequenceFps('24/0') and model.sequenceInfo['error']
        assert model.setSequenceFps('24000/1001')
        fps_editor=find(window.contentItem(),'sequenceFpsEditor')
        fps_editor.property('contentItem').forceActiveFocus()
        fps_editor.setProperty('editText','30000/1001');QTest.keyClick(window,Qt.Key_Return)
        assert model._workspace.active.sequence.rate==Fraction(30000,1001)
        assert model.setSequenceFps('24000/1001')
        # Actual existing play button; observe first pass identities, not FPS claims.
        first=len(result['rows']);click(find(window.contentItem(),'playPauseButton'))
        wait(lambda:len(result['rows'])>=first+16,'first pass',timeout=12)
        click(find(window.contentItem(),'playPauseButton'));wait(lambda:not preview.busy)
        delivered=[r['slot'] for r in result['rows'][first:]]
        assert delivered[:16]==list(range(1,12))+list(range(5)),delivered
        result['first_pass']=dict(requested=pc.sequence.requested,delivered=pc.sequence.delivered,late=pc.sequence.late,
                                  identities=delivered,missed=[],note='Submission truth only; sparse native grabs independently assert selected pictures.')
        pc.seek(7);paint(7);pc.stepFrame(1);paint(8);pc.stepFrame(-1);paint(7)
        timeline=find(window.contentItem(),'mediaTimeline')
        point=timeline.mapToScene(QPointF(timeline.width()*3/12,32)).toPoint()
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point);paint(3)
        model.setOperationEnabled('trim',True);assert model.setTrimRange(2,5)
        pc.seek(2);paint(2)
        first=len(result['rows']);pc.togglePlayback();wait(lambda:len(result['rows'])>=first+7,'Trim loop');pc.togglePlayback();wait(lambda:not preview.busy)
        assert all(2<=r['slot']<5 for r in result['rows'][first:]),result['rows'][first:]
        pc.seek(9);paint(9);assert not pc.trimLoopActive
        assert model.setTrimInAtPlayhead(False) is False  # invalid [9,5)
        assert model.setTrimOutAtPlayhead(False) and model.editState['trimOut']==10
        # Channel changes pause and keep the exact current source.
        assert find(window.contentItem(),'imagePreviewQuality') is None
        pc.togglePlayback();assert model.setImageChannel('RGB');wait(lambda:not preview.busy)
        assert not preview.status and not pc.playing
        paint(pc.sequence.slot)
        pc.togglePlayback();wait(lambda:pc.sequence.delivered>18);pc.togglePlayback();wait(lambda:not preview.busy)
        assert model.setImageChannel('RGBA');wait(lambda:not preview.busy)
        # Latest seek coalesces, with no stale intermediate publication.
        pc.seek(1);pc.seek(5);pc.seek(10);paint(10)
        result['checks'].append('JPEG 1080p import/assumed FPS/GUI play/exact step/seek/Trim/loop/channel/latest intent')
        active=model.activeMediaId;serial=pc._image_serial
        gap=out/'gap';gap.mkdir();(gap/'x0001.png').write_bytes((fixtures/'png/кадр_0101.png').read_bytes());(gap/'x0003.png').write_bytes((fixtures/'png/кадр_0101.png').read_bytes())
        assert model.importSequence(QUrl.fromLocalFile(str(gap/'x0001.png')));wait(lambda:not preview.busy)
        assert 'Gap Policy' in preview.status and active==model.activeMediaId and serial==pc._image_serial;paint(10)
        # PNG and transparent TIFF use the same pipeline and publisher.
        for kind in ('png','tiff'):
            sequence(fixtures/kind/f'кадр_0102.{kind}');pc.seek(2);paint(2);pc.stepFrame(-1);paint(1)
        sequence(fixtures/'uhd/кадр_0101.jpg');wait(lambda:not preview.busy)
        source=model._workspace.active.metadata.source
        sample=pc._image_prepared.raster.plan
        assert (source.manifest.members[0].facts.width,source.manifest.members[0].facts.height)==(3840,2160)
        assert (pc.imageWidth,pc.imageHeight)==(sample.width,sample.height)
        assert 0<sample.width<=3840 and 0<sample.height<=2160 and sample.width*sample.height*4<=32*1024**2
        pc.seek(1);paint(1)
        result['checks'].append('PNG/TIFF alpha and UHD JPEG with admitted automatic display sampling')
        # Existing video -> sequence -> video, without recreating the Viewer.
        sink=pc._presentation_sink;viewer=find(window.contentItem(),'viewerVideoOutput')
        assert model.openMedia(QUrl.fromLocalFile(str(video)))
        wait(lambda:model.sourceKind=='video' and pc.framesReliable,'video',timeout=15)
        assert not pc.imageMode and pc._player is not None
        jpeg_item=next(i for i in model._workspace.items if i.metadata.source_path==str(paths[0]))
        assert model.selectMedia(jpeg_item.id);wait(lambda:not preview.busy);paint(jpeg_item.sequence.current_slot)
        assert pc._player is None and pc._staging_sink is None
        assert model.openMedia(QUrl.fromLocalFile(str(video)));wait(lambda:model.sourceKind=='video' and pc.framesReliable,'video restore',timeout=15)
        assert pc._presentation_sink is sink and find(window.contentItem(),'viewerVideoOutput') is viewer
        # Native input F/W and fullscreen reuse the accepted window/controller.
        assert model.selectMedia(jpeg_item.id);wait(lambda:not preview.busy)
        window.requestActivate();find(window.contentItem(),'workspaceKeyboardFocus').forceActiveFocus()
        QTest.keyClick(window,Qt.Key_F);before=model.previewPlan.copy();QTest.keyClick(window,Qt.Key_W);assert model.previewPlan==before
        wait(lambda:model._image_service.resources.last is not None)
        model.windowPresentation.enter(window)
        wait(lambda:pc.fullscreen,'fullscreen admission');paint(pc.sequence.slot)
        model.windowPresentation.exit();wait(lambda:not pc.fullscreen,'fullscreen exit')
        result['checks'].append('video/sequence/video persistent Viewer and zero image-mode concrete players; F/W inert/fullscreen')
        # Hold one real decoder before allocation: seeks/channel/source changes
        # must coalesce without publishing a cancelled member.
        # Force deterministic cold preparations; cache/prefetch reuse is tested
        # separately. Otherwise a warm hit legitimately bypasses this decoder hook.
        prefetch=preview.prefetch;preview.prefetch=lambda *args:None
        wait(lambda:preview._active is None)
        model._image_service.close_cache()
        decode=model._image_service.reader.decode_preview
        entered=threading.Event();release=threading.Event();held_slots=[]
        def held(source,plan,cancel):
            held_slots.append(source.path);entered.set()
            assert release.wait(3),'controlled worker not released'
            return decode(source,plan,cancel)
        model._image_service.reader.decode_preview=held
        first=len(result['rows']);pc.seek(1);wait(entered.is_set,'held decode')
        pc.seek(5);pc.seek(7);release.set();paint(7)
        assert [r['slot'] for r in result['rows'][first:]]==[7]
        assert len(held_slots)==2
        model._image_service.close_cache()
        entered.clear();release.clear();pc.seek(6);wait(entered.is_set)
        assert model.setImageChannel('RGB');assert model.setImageChannel('RGBA')
        release.set();wait(lambda:not preview.busy);assert pc.sequence.slot==7 and preview.channel=='RGBA'
        model._image_service.close_cache()
        entered.clear();release.clear();pc.seek(8);wait(entered.is_set)
        png_item=next(i for i in model._workspace.items if i.metadata.format_name=='png sequence')
        first=len(result['rows']);assert model.selectMedia(png_item.id);release.set();wait(lambda:not preview.busy)
        assert model.activeMediaId==png_item.id and all(r['source']==png_item.metadata.source.manifest_id for r in result['rows'][first:])
        model._image_service.reader.decode_preview=decode
        assert model.selectMedia(jpeg_item.id);wait(lambda:not preview.busy)
        result['checks'].append('held decode cancellation; latest seek/channel/source generation only; bounded active/latest pending')
        # Changed and missing selected members are rejected, last valid picture survives.
        negative=out/'negative';negative.mkdir();negative_paths=[]
        for n in range(3):
            path=negative/f'n{n:04d}.jpg';img=QImage(320,180,QImage.Format_RGB32);img.fill(QColor(50,60,70));assert img.save(str(path))
            expected[str(path)]=(50,60,70);hashes[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest();negative_paths.append(path)
        sequence(negative_paths[0]);paint(0)
        bad=negative_paths[1];before=pc._image_serial;original=bad.read_bytes()
        replacement=QImage(320,180,QImage.Format_RGB32);replacement.fill(QColor(1,2,3));assert replacement.save(str(bad));del replacement
        pc.seek(1);wait(lambda:not preview.busy);assert 'changed' in preview.status and pc._image_serial==before
        bad.write_bytes(original)
        missing=negative_paths[2];renamed=missing.with_suffix('.owned-away');missing.rename(renamed)
        try:pc.seek(2);wait(lambda:not preview.busy);assert preview.status and pc._image_serial==before
        finally:renamed.rename(missing)
        assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items())
        result['source_hashes']=hashes
        assert not warnings,warnings
        result['heartbeat_max_gap_ms']=max((b-a)*1000 for a,b in zip(heartbeat,heartbeat[1:]))
        result['logical_peak']=model._image_service.budget.peak
        result['checks'].append('missing/changed membership refusal, unchanged bytes, source identities')
        model._image_service.close_cache()
        entered.clear();release.clear();model._image_service.reader.decode_preview=held
        pc.seek(0);wait(entered.is_set);threading.Timer(.1,release.set).start()
        model.shutdown()
        # Session identities remain live in the Tray/local test references; their
        # metadata leases remain charged. Decoder/display application leases end.
        known={i.metadata.source.manifest_id:i.metadata.source.manifest.count for i in model._workspace.items
               if hasattr(i.metadata.source,'manifest_id')}
        assert model._image_service.budget.reserved==model._image_service.budget.overhead+sum(known.values())*4096
        result['shutdown_reserved']=model._image_service.budget.reserved
        result['checks'].append('shutdown joins cancelled worker; frame leases end, live session metadata stays charged')
        result['passed']=True
    except BaseException:
        result['passed']=False;result['failure']=traceback.format_exc()
    finally:
        try:model.shutdown();close_native_windows(window);shiboken6.delete(engine);result['shutdown']='clean'
        except BaseException:result['shutdown']=traceback.format_exc();result['passed']=False
        watchdog.cancel();beat.stop()
        (out/'results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2),flush=True)
    assert result['passed'],result.get('failure')


if __name__=='__main__':main(Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]) if len(sys.argv)>3 else None)
