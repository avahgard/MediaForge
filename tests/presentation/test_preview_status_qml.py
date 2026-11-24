"""Actual QML progress debounce; native painted Owner playback is tested separately."""
import pytest


@pytest.mark.integration
def test_latest_request_delay_error_priority_and_play_pause_reset(run_python):
    result=run_python(r'''
import shiboken6
from PySide6.QtCore import QObject,QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from mediaforge.infrastructure.resources.paths import qml_root
app=QGuiApplication([]);engine=QQmlApplicationEngine();warnings=[]
engine.warnings.connect(lambda values:warnings.extend(v.toString() for v in values))
engine.loadData(b'import QtQuick\nimport QtQuick.Controls\nimport "."\nApplicationWindow { visible:true; width:800; height:560; PreviewStatus { objectName:"status" } }',QUrl.fromLocalFile(str(qml_root().parent/'status-test.qml')))
assert engine.rootObjects(),warnings
window=engine.rootObjects()[0];status=window.findChild(QQuickItem,'status');QTest.qWait(30)
assert not status.isVisible()
status.setProperty('details','Preparing image...')
assert not status.isVisible(), 'A human-readable string is not a visibility state'
status.setProperty('requestKey','image:1:paused');status.setProperty('progressActive',True)
QTest.qWait(350);assert not status.isVisible()
QTest.qWait(500);assert status.isVisible() and status.property('text')=='Preparing…'
# New request and Play/Pause changes restart, rather than inheriting old delay.
status.setProperty('requestKey','image:2:playing');assert not status.isVisible()
QTest.qWait(350);assert not status.isVisible()
status.setProperty('requestKey','image:2:paused');QTest.qWait(450);assert not status.isVisible()
status.setProperty('progressActive',False);QTest.qWait(800);assert not status.isVisible()
# Short completion never appears; canceled timer cannot revive a later source.
status.setProperty('requestKey','image:3:paused');status.setProperty('progressActive',True)
QTest.qWait(150);assert not status.isVisible()
status.setProperty('progressActive',False);status.setProperty('requestKey','new-source')
QTest.qWait(800);assert not status.isVisible()
# Failure priority is independent of text (including a progress-looking message).
status.setProperty('details','Preparing failed: invalid source');status.setProperty('errorActive',True)
assert status.isVisible() and status.property('text')=='Preview unavailable'
status.setProperty('errorActive',False);assert not status.isVisible()
# The same debounce supports real video-proxy work, not only images.
status.setProperty('requestKey','video:1:2');status.setProperty('progressActive',True)
QTest.qWait(350);assert not status.isVisible()
QTest.qWait(500);assert status.isVisible()
status.setProperty('progressActive',False);assert not status.isVisible()
assert not warnings,warnings
window.close();shiboken6.delete(engine)
''')
    assert result.returncode==0,result.stdout+result.stderr


def test_video_cancel_and_shutdown_clear_progress_without_waiting_for_worker(run_python):
    result=run_python(r'''
from types import SimpleNamespace
from PySide6.QtCore import QCoreApplication,QObject,Signal
from mediaforge.presentation.bridge.video_preview import VideoPreviewController
app=QCoreApplication([])
class Model(QObject):
    changed=Signal()
model=Model();model.playback=SimpleNamespace(changed=model.changed)
closed=[];canceled=[];service=SimpleNamespace(close=lambda:closed.append(True))
preview=VideoPreviewController(service,model)
preview.control=SimpleNamespace(cancel=lambda:canceled.append(True))
observed=[];preview.changed.connect(lambda:observed.append(preview.status))
preview.status='Preparing video preview…';preview.cancel()
assert preview.status=='' and observed[-1]=='' and canceled==[True]
preview.status='Preparing video preview…';preview.shutdown()
assert preview.status=='' and observed[-1]=='' and closed==[True]
''')
    assert result.returncode==0,result.stdout+result.stderr
