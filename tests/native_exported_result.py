"""Finite real-window result activation through production import/export contracts."""
import sys,os,time,json,logging,hashlib,traceback
sys.dont_write_bytecode=True
from pathlib import Path
root=Path(sys.argv[1]);(root/'ready.json').write_text(json.dumps(dict(pid=os.getpid())))
deadline=time.monotonic()+8
while not (root/'go').exists() and time.monotonic()<deadline:time.sleep(.01)
assert (root/'go').exists(),'External watchdog did not assign ownership'
from PySide6.QtCore import QUrl,QTimer,QEventLoop,QPointF,Qt,QSize,QObject,QMetaObject
from PySide6.QtGui import QGuiApplication,QImage,QColor
from PySide6.QtQml import QQmlApplicationEngine,QQmlExpression
from PySide6.QtTest import QTest
import shiboken6
from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.resources.paths import qml_root
from native_window_policy import load_native_shell,resize_native_window,close_native_windows
app=QGuiApplication([]);model=build_media_view_model(logging.getLogger('exported-result-native'))
engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('mediaViewModel',model)
window=load_native_shell(engine,qml_root(),logging.getLogger('exported-result-native'));resize_native_window(window,800,560)
result={'checks':[],'dpr':window.devicePixelRatio()};source=root/'source';source.mkdir();dest=root/'output';dest.mkdir()
for i in range(3):
    image=QImage(128,96,QImage.Format_RGB32);image.fill(QColor(200,40+i*30,80));assert image.save(str(source/f'picture_{101+i:04d}.png'))
image.save(str(source/'ordinary.png'));del image
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
def wait(test,label='condition',seconds=10):
    if test():return
    loop=QEventLoop();poll=QTimer();poll.setInterval(5);limit=QTimer();limit.setSingleShot(True);expired=[]
    poll.timeout.connect(lambda:loop.quit() if test() else None);limit.timeout.connect(lambda:(expired.append(True),loop.quit()))
    poll.start();limit.start(int(seconds*1000));loop.exec();poll.stop();limit.stop()
    assert not expired and test(),(label,model.output.message,model.imagePreview.status)
def find(name):
    def visit(item):
        if item.objectName()==name:return item
        for child in item.childItems():
            answer=visit(child)
            if answer is not None:return answer
    return visit(window.contentItem())
def click(item):
    assert item is not None and item.isVisible() and item.isEnabled()
    QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint())
def open_result():
    # Owner removed this navigation button; the validated public API and parity
    # contract remain exercised, never replaced by folder reveal or auto-import.
    wait(lambda:model.output.canOpenResult,'Validated result API eligibility')
    assert find('exportOpenResult') is None
    assert model.output.openExportedResult()
def convert(kind,container,name):
    model.selectOperation('output')
    for k,v in [('kind',kind),('container',container),('folder',str(dest)),('filename',name)]:assert model.output.setSetting(k,v),(k,v)
    assert model.output.canExport,model.output.summary
    # Start sits in the existing pinned footer; use its real pointer action.
    job=model.output._job.job_id
    click(find('exportStart'));wait(lambda:model.output._job.job_id!=job,'new export admitted')
    wait(lambda:not model.output.busy,'export',20);model.output.refresh()
    assert model.output.state=='Completed',model.output.message
    return Path(model.output.outputPath)
try:
    wait(lambda:model.imagePreview._backend is not None,'backend');result['backend']=model.imagePreview._backend
    assert model.openMedia(QUrl.fromLocalFile(str(source/'ordinary.png')))
    wait(lambda:model.sourceKind=='still' and not model.imagePreview.busy,'original still')
    sink=model.playback._presentation_sink;viewer=find('viewerVideoOutput')
    initial=len(model.mediaItems);active=model.activeMediaId;path=convert('still','png','numbered_0001')
    assert model.activeMediaId==active
    open_result();wait(lambda:model._workspace.active.metadata.source_path==str(path) and not model.imagePreview.busy,'result still')
    assert model.sourceKind=='still' and len(model.mediaItems)==initial+1 and not model.sequenceConfirmation
    assert model.playback._player is None and model.playback._staging_sink is None
    wait(lambda:model.playback._image_ready,'paint ready')
    grab=window.grabWindow();point=viewer.mapToScene(QPointF(viewer.width()/2,viewer.height()/2));color=grab.pixelColor(round(point.x()*window.devicePixelRatio()),round(point.y()*window.devicePixelRatio()))
    assert max(abs(a-b) for a,b in zip(color.getRgb()[:3],(200,100,80)))<=3,color.getRgb()
    assert grab.scaledToWidth(640).save(str(root/'result-viewer.png'))
    result['checks'].append('single numbered exported PNG opens as one still; native painted color; no second player')
    assert model.openMedia(QUrl.fromLocalFile(str(source/'picture_0101.png')));wait(lambda:bool(model.sequenceConfirmation))
    dialog=window.findChild(QObject,'sequenceConfirmationDialog');editor=dialog.findChild(QObject,'sequenceFpsConfirmation')
    editor.setProperty('editText','2997/100');click(find('confirmSequenceButton'))
    wait(lambda:model.sourceKind=='sequence' and not model.imagePreview.busy and not dialog.property('visible'))
    assert model.output.setSetting('sequence_start','7')
    directory=convert('sequence','png','sequence-result');before=model.activeMediaId;count=len(model.mediaItems)
    open_result();wait(lambda:bool(model.sequenceConfirmation),'result manifest')
    assert model.activeMediaId==before and len(model.mediaItems)==count
    assert model.sequenceConfirmation['fps']=='29.97' and 'Confirmed export FPS' in model.sequenceConfirmation['authority']
    QMetaObject.invokeMethod(dialog,'reject');assert model.activeMediaId==before and not model.sequenceConfirmation
    open_result();wait(lambda:bool(model.sequenceConfirmation));editor=dialog.findChild(QObject,'sequenceFpsConfirmation')
    assert editor.property('editText')=='29.97'
    click(find('confirmSequenceButton'));wait(lambda:model.activeMediaId!=before and not model.imagePreview.busy,'result sequence activation')
    item=model._workspace.active;assert item.metadata.source.manifest.numbers==(7,8,9) and str(item.sequence.rate)=='2997/100'
    assert len(model.mediaItems)==count+1
    result['checks'].append('TxF sequence result one item; frozen names; full manifest; confirmed rational FPS; Cancel preserves source')
    movie=convert('media','mp4','movie');open_result()
    wait(lambda:model.sourceKind=='video' and model._workspace.active.metadata.source_path==str(movie) and model.playback.framesReliable,'movie result')
    assert model.playback._presentation_sink is sink and find('viewerVideoOutput') is viewer
    result['checks'].append('MOV/MP4 result uses fresh accepted metadata/timeline and same Viewer')
    picture=convert('frame','png','selected_0001');open_result()
    wait(lambda:model.sourceKind=='still' and model._workspace.active.metadata.source_path==str(picture) and not model.imagePreview.busy,'video frame result')
    before=model.activeMediaId;frame=model.playback._image_prepared
    picture.write_bytes(b'corrupted after commit')
    open_result();wait(lambda:not model.imagePreview.busy,'corrupt result refusal')
    assert model.activeMediaId==before and model.playback._image_prepared is frame and model.imagePreview.status
    # Missing exported sequence member must not activate or add anything.
    assert model.selectMedia(item.id);wait(lambda:not model.imagePreview.busy)
    missing=convert('sequence','png','changed-sequence');plan=model.output._use_case.completed_result()
    (missing/plan.numbering.name(plan.numbering.count-1)).unlink();before=model.activeMediaId;count=len(model.mediaItems)
    open_result();wait(lambda:not model.imagePreview.busy,'missing last member refusal')
    assert model.activeMediaId==before and len(model.mediaItems)==count and not model.sequenceConfirmation
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items())
    result['checks'].append('corrupt still/missing sequence member fail before activation; current picture and source bytes preserved')
    result['passed']=True;result['logical_peak']=model._image_service.budget.peak
except BaseException:
    result['passed']=False;result['failure']=traceback.format_exc();raise
finally:
    model.shutdown();close_native_windows(window);shiboken6.delete(engine);result['shutdown']='clean'
    (root/'results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
