"""Native keyboard/menu correctness probe; launched only by the external watchdog."""
import sys,os,time,json,faulthandler,logging
from pathlib import Path
root=Path(sys.argv[1]);(root/'ready.json').write_text(json.dumps(dict(pid=os.getpid())))
deadline=time.monotonic()+8
while not (root/'go').exists() and time.monotonic()<deadline:time.sleep(.01)
assert (root/'go').exists(),'External watchdog did not assign ownership'
faulthandler.enable();faulthandler.dump_traceback_later(15,repeat=True)
from PySide6.QtCore import QObject,QUrl,Qt,QSize,QPointF,QEventLoop,QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine,QQmlExpression
from PySide6.QtTest import QTest
import shiboken6
from mediaforge.bootstrap.application import build_media_view_model,load_shell
from mediaforge.infrastructure.resources.paths import qml_root
from native_window_policy import policy,close_native_windows
app=QGuiApplication([]);model=build_media_view_model(logging.getLogger('review-corrections'));engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('mediaViewModel',model)
engine.setInitialProperties({'visible':False,'width':800,'height':560});window=load_shell(engine,qml_root(),logging.getLogger('review-corrections'));policy().place(window,QSize(800,560));window.show()
rows=[]
def phase(name):print(name,flush=True)
def wait(test):
    if test():return
    loop=QEventLoop();poll=QTimer();poll.setInterval(5);limit=QTimer();limit.setSingleShot(True);expired=[]
    poll.timeout.connect(lambda:loop.quit() if test() else None);limit.timeout.connect(lambda:(expired.append(True),loop.quit()));poll.start();limit.start(8000);loop.exec();poll.stop();limit.stop()
    assert not expired and test(),model.imagePreview.status
def find(name):
    def visit(item):
        if item.objectName()==name:return item
        for c in item.childItems():
            value=visit(c)
            if value is not None:return value
    return visit(window.contentItem())
def click(item):QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint())
try:
    phase('wait backend using QEventLoop');wait(lambda:model.imagePreview._backend is not None)
    assert model.openMedia(QUrl.fromLocalFile(str(Path(sys.argv[2]).absolute())));wait(lambda:bool(model.sequenceConfirmation))
    facts=model.sequenceConfirmation;assert facts['directory'] and facts['pixelClass'] and facts['fps']=='24' and facts['exactFps']=='24' and 'ASSUMED' in facts['authority']
    dialog=window.findChild(QObject,'sequenceConfirmationDialog');editor=dialog.findChild(QObject,'sequenceFpsConfirmation')
    for text in ('24/0','1000000/999999'):
        phase('keyboard '+text);window.requestActivate();editor.property('contentItem').forceActiveFocus()
        QTest.keyClick(window,Qt.Key_A,Qt.ControlModifier)
        for c in text:QTest.keyClick(window,Qt.Key_Slash if c=='/' else Qt.Key(int(Qt.Key_0)+int(c)))
        assert editor.property('editText')==text,editor.property('editText')
        click(find('confirmSequenceButton'))
        if text=='24/0':wait(lambda:bool(model.sequenceConfirmation.get('error')) and dialog.property('visible'))
        else:wait(lambda:model.sourceKind=='sequence' and not model.imagePreview.busy)
        rows.append(dict(typed=text,confirmed=model.sequenceInfo.get('fps'),error=model.sequenceConfirmation.get('error')))
    assert model.sequenceInfo['fps']=='1000000/999999'
    phase('channel menu timing without readback');channel=find('viewerChannel')
    assert find('imagePreviewQuality') is None
    wait(lambda:model.playback._image_ready and channel.isVisible() and channel.isEnabled())
    visible=QQmlExpression(engine.rootContext(),channel,'popup.visible')
    for n in range(2):
        start=time.perf_counter();click(channel);returned=time.perf_counter();wait(lambda:visible.evaluate()[0])
        rows.append(dict(menu=n,click_ms=(returned-start)*1000,popup_type=QQmlExpression(engine.rootContext(),channel,'popup.popupType').evaluate()[0]))
        QTest.keyClick(window,Qt.Key_Escape);wait(lambda:not visible.evaluate()[0])
    phase('capture after timing');assert window.grabWindow().scaledToWidth(640).save(str(root/'viewer.png'))
    assert find('viewerCompareSource') is None and find('viewerCompareOutput') is None
    data=dict(passed=True,dpr=window.devicePixelRatio(),rows=rows,source=facts)
finally:
    phase('shutdown');model.shutdown();close_native_windows(window);shiboken6.delete(engine);faulthandler.cancel_dump_traceback_later()
    (root/'results.json').write_text(json.dumps(locals().get('data',dict(passed=False,rows=rows)),indent=2))
