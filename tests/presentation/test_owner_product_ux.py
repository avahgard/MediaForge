"""Owner-directed presentation removal preserves functions and explicit errors."""
from pathlib import Path
import re
import pytest


@pytest.mark.integration
def test_fullscreen_has_only_transport_exit_and_restores_windowed_entry(run_python):
    from tests.presentation.test_workspace_qml import SETUP, CLEANUP
    p=run_python(SETUP + r'''
load(Path.cwd()/'fullscreen.mp4')
for _ in range(3):
    assert item('fullscreenButton').isVisible()
    click(item('fullscreenButton'))
    wait(lambda:playback.fullscreen)
    assert not item('fullscreenButton').isVisible()
    playback.setControlsZone(True);QTest.qWait(80)
    assert item('exitFullscreenButton').isVisible()
    click(item('exitFullscreenButton'))
    wait(lambda:not playback.fullscreen)
    assert item('fullscreenButton').isVisible()
click(item('fullscreenButton'));wait(lambda:playback.fullscreen)
QTest.keyClick(window,Qt.Key_Escape);wait(lambda:not playback.fullscreen)
assert item('fullscreenButton').isVisible()
''' + CLEANUP)
    assert p.returncode==0,p.stdout+p.stderr


def test_media_qml_has_no_hover_popup_and_only_folder_navigation():
    root=Path('src/mediaforge/presentation/qml')
    for path in root.glob('*.qml'):
        code=path.read_text('utf-8')
        assert not re.search(r'\b(?:ToolTip|tooltipEnabled|toolTipText)\b',code),path
    workspace=(root/'Workspace.qml').read_text('utf-8')
    assert all(text not in workspace for text in ['viewerCue','Crop Edit · original source','Reformat preview'])
    output=(root/'OutputInspector.qml').read_text('utf-8')
    assert 'exportOpenResult' not in output and 'Open exported result' not in output
    assert 'text: "Open folder"' in output and 'onClicked: root.output.revealOutput()' in output


@pytest.mark.integration
def test_error_details_are_explicit_and_never_hover_triggered(run_python):
    p=run_python(r'''
import shiboken6
from PySide6.QtCore import QUrl,QPointF,Qt,QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from mediaforge.infrastructure.resources.paths import qml_root
app=QGuiApplication([]);engine=QQmlApplicationEngine();errors=[]
engine.warnings.connect(lambda values:errors.extend(v.toString() for v in values))
engine.loadData(b'import QtQuick\nimport QtQuick.Controls\nimport "."\nApplicationWindow { visible:true; width:800; height:560; PreviewStatus { objectName:"status"; details:"Actionable failure details"; errorActive:true } }',QUrl.fromLocalFile(str(qml_root().parent/'owner-status.qml')))
assert engine.rootObjects(),errors
w=engine.rootObjects()[0];s=w.findChild(QQuickItem,'status');p=w.findChild(QObject,'previewStatusDetails')
QTest.qWait(40);assert s.isVisible() and not p.property('visible')
point=s.mapToScene(QPointF(s.width()/2,s.height()/2)).toPoint();QTest.mouseMove(w,point);QTest.qWait(1000)
assert not p.property('visible')
QTest.mouseClick(w,Qt.LeftButton,Qt.NoModifier,point);QTest.qWait(50);assert p.property('visible')
QTest.keyClick(w,Qt.Key_Escape);QTest.qWait(30);assert not p.property('visible')
QTest.mouseClick(w,Qt.LeftButton,Qt.NoModifier,point);QTest.qWait(30);assert p.property('visible')
s.setProperty('errorActive',False);QTest.qWait(20);assert not p.property('visible')
assert not errors,errors
w.close();shiboken6.delete(engine)
''')
    assert p.returncode==0,p.stdout+p.stderr
