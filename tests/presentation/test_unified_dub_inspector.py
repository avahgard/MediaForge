"""Rendered Inspector bindings with explicit synthetic state fixtures (not native Dub)."""
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]
pytestmark=pytest.mark.integration


def test_compact_inspector_consent_popup_and_recovery(run_python):
    runner=run_python
    def run_python(code):
        import subprocess
        try:return runner(code)
        except subprocess.TimeoutExpired as error:raise AssertionError(repr(error.stdout)+repr(error.stderr)) from error
    result=run_python('import sys\nsys.path.insert(0,'+repr(str(ROOT/'tests/unit'))+')\n'+r'''
from pathlib import Path
from types import SimpleNamespace as NS
from dataclasses import replace
from fractions import Fraction as F
import time
from PySide6.QtCore import QObject,QUrl,QPointF,QPoint,Qt,QMetaObject
from PySide6.QtGui import QGuiApplication,QFontDatabase
from PySide6.QtQuick import QQuickItem
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from mediaforge.application.services.dub import DubService,DubState
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.application.services.voice_preview import VoicePreviewState
from mediaforge.features.dub.contracts import Reason, Refusal
from mediaforge.presentation.bridge.dub import DubViewModel
from mediaforge.infrastructure.resources.paths import qml_root
from test_dub_contracts import source
app=QGuiApplication([])
if not QFontDatabase.families():
    for n in ('segoeui.ttf','seguisb.ttf'):QFontDatabase.addApplicationFontFromData((Path('C:/Windows/Fonts')/n).read_bytes())
gate=MediaProcessingGate()
sessions=NS(close=lambda c:None,expiry_due=lambda:False,retry_eligible=lambda n:True)
service=DubService(None,NS(sessions=sessions),None,gate,('aiden',),destination_planner=lambda identity,route,folder,filename:folder/filename)
s=source();track=s.audio_tracks[0]
service._source=replace(s,audio_tracks=(replace(track,clock=replace(track.clock,first=F(-1,8),end=F(7,4),samples=90000)),))
service._state=DubState(1,'ready')
class Preview:
    busy=False
    value=VoicePreviewState()
    sample=None
    def snapshot(self):return self.value
    def fragment(self):return self.sample
    def elapsed(self):return 12.
    def shutdown(self):pass
models=NS(allInstalled=True,components=[dict(id=k,state='installed_unqualified',status='Installed, unqualified fixture') for k in ('dub-cpu-runtime','whisper-base','argos-en-ru','qwen-runtime-cu128','qwen3-tts-06b-customvoice','qwen3-tts-tokenizer-12hz')])
model=DubViewModel(service,Preview(),models);model._timer.stop();model._mode='same_as_source';model._folder=str(Path.cwd());model._source_generation=1;model.refresh()
engine=QQmlApplicationEngine();warnings=[];engine.warnings.connect(lambda rows:warnings.extend(r.toString() for r in rows))
engine.rootContext().setContextProperty('dubViewModel',model)
qml='import QtQuick\nimport QtQuick.Controls\nimport "'+qml_root().parent.as_uri()+'"\nApplicationWindow { visible: true; width: 280; height: 450; DubPage { anchors.fill: parent; model: dubViewModel; workspace: ({}) } }'
engine.loadData(qml.encode());assert engine.rootObjects(),warnings
window=engine.rootObjects()[0]
def item(name):
    value=window.findChild(QQuickItem,name);assert value is not None,name;return value
def click(target):
    point=target.mapToScene(QPointF(target.width()/2,target.height()/2))
    QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,QPoint(round(point.x()),round(point.y())));QTest.qWait(30)
from dataclasses import asdict
from PySide6.QtGui import QWheelEvent
from mediaforge.infrastructure.filesystem.dub_source import destination_plan

starts=[]
service.start=lambda *args,**options: starts.append((args,options)) or True
page=item('dubPage')
body=item('dubInspectorScroll')
primary=item('dubPrimary')
status=item('dubFooterStatus')
folder=item('dubChooseFolder')
mode=item('dubOutputMode')
popup=window.findChild(QObject,'dubOutputModePopup')
assert popup is not None
dialog=window.findChild(QObject,'dubSpanConsent')
flick=body.property('contentItem')
qualified=service._source

def ready(descriptor):
    service._source=descriptor
    service._state=DubState(service._state.generation+1,'ready')
    model.refresh();QTest.qWait(30)

def inside(child, parent):
    point=child.mapToItem(parent,QPointF())
    return point.y()>=0 and point.y()+child.height()<=parent.height()

def open_popup():
    mode.forceActiveFocus();QTest.qWait(20)
    QTest.keyClick(window,Qt.Key_Space)
    QTest.qWait(50)
    assert popup.property('visible')
    control_origin=mode.mapToScene(QPointF())
    popup_item=popup.property('contentItem').parentItem()
    origin=popup_item.mapToScene(QPointF())
    assert abs(origin.x()-control_origin.x())<1
    assert popup.property('width')==mode.width()
    assert origin.y()>=0 and origin.y()+popup_item.height()<=window.height()

try:
    QTest.qWait(60)
    assert item('dubVoice').property('text')=='Aiden · Qwen3-TTS'
    assert window.findChild(QObject,'dubGeneratePreview') is None
    assert window.findChild(QObject,'dubSpanAcknowledgment') is None
    assert model.canStart and primary.isEnabled() and not status.isVisible()
    assert inside(folder,body) and inside(primary,page)
    assert body.y()+body.height()<=item('dubActionFooter').y()
    path_field=item('dubFullDestination')
    filename_field=item('dubFilename')
    widths=(path_field.width(),filename_field.width(),folder.width())
    long_folder=Path.cwd()/('Папка для результатов '*4).strip()
    long_folder.mkdir()
    assert model.chooseFolder(QUrl.fromLocalFile(str(long_folder)))
    assert model.setFilename(('Длинное имя '*8).strip())
    QTest.qWait(30)
    assert (path_field.width(),filename_field.width(),folder.width())==widths and inside(folder,body)
    assert model.setFilename('') and model.chooseFolder(QUrl.fromLocalFile(str(Path.cwd())))
    click(primary)
    assert dialog.property('visible') and '0.125 seconds at the start' in model.spanConsentText
    assert '0.75 seconds at the end' in model.spanConsentText and not starts
    QMetaObject.invokeMethod(dialog,'reject');QTest.qWait(30)
    assert not dialog.property('visible') and model.canStart and not starts
    click(primary);model.setMode('compatible_mp4');QTest.qWait(30)
    assert not dialog.property('visible') and not model.confirmSpanConsent() and not starts
    click(primary);ready(qualified)
    assert not dialog.property('visible') and not model.confirmSpanConsent() and not starts
    click(primary);QMetaObject.invokeMethod(dialog,'accept');QTest.qWait(30)
    assert len(starts)==1 and starts[-1][1]['acknowledge'] is True
    assert not model.confirmSpanConsent() and len(starts)==1

    uncut=replace(qualified,audio_tracks=(replace(track,clock=replace(track.clock,end=F(1))),))
    ready(uncut);click(primary)
    assert len(starts)==2 and starts[-1][1]['acknowledge'] is False and not dialog.property('visible')
    open_popup()
    QTest.keyClick(window,Qt.Key_Down);QTest.keyClick(window,Qt.Key_Return);QTest.qWait(30)
    assert model.outputMode=='compatible_mp4' and not popup.property('visible')
    open_popup()
    window.resize(280,360);QTest.qWait(50)
    assert not popup.property('visible') and inside(primary,page)
    model.setMode('video_audio_only');QTest.qWait(30);open_popup()
    point=QPointF(body.x()+body.width()-2,body.y()+body.height()-2)
    wheel=QWheelEvent(point,point,QPoint(0,0),QPoint(0,-120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
    QGuiApplication.sendEvent(window,wheel);QTest.qWait(60)
    if flick.property('contentY') == 0:
        # Exercise the same contentY change from the scrollbar/keyboard if the
        # popup itself consumes wheel on this platform.
        flick.setProperty('contentY',min(20,flick.property('contentHeight')-body.height()))
        QTest.qWait(30)
    assert flick.property('contentY') > 0 and not popup.property('visible')
    open_popup()
    ready(replace(uncut,identity=replace(uncut.identity,path=Path('D:/replacement.mp4'))))
    assert not popup.property('visible')
    open_popup();page.setVisible(False);QTest.qWait(30)
    assert not popup.property('visible')
    page.setVisible(True);window.resize(280,450);QTest.qWait(30)

    models.allInstalled=False;model.refresh();QTest.qWait(30)
    assert 'Models unavailable' in status.property('text') and not primary.isEnabled()
    models.allInstalled=True;model.refresh()
    refuse=Refusal(Reason.INVALID_TIMING,"Dub can't use this audio track because its decoded timestamps overlap.")
    refused=replace(track,index=4,clock=None,refusal=refuse)
    ready(replace(uncut,audio_tracks=(track,refused)))
    assert item('dubAudioTrackRefusals').property('text')=='Track 4 · '+refuse.message
    assert not primary.isEnabled()
    ready(replace(uncut,audio_tracks=(refused,)))
    assert status.property('text')==refuse.message and not primary.isEnabled()
    ready(uncut)
    service._state=DubState(20,'running','recognize',completed=1,total=2)
    class Pending:
        def is_alive(self): return True
    service._thread=Pending();model.refresh();QTest.qWait(30)
    assert item('dubProgress').isVisible() and not folder.isVisible()
    service._thread=None
    service._state=DubState(20,'failed','recognize',message='Recognition fixture failure.')
    service._retry='owned-ticket'
    model.refresh();QTest.qWait(30)
    assert primary.property('text')=='Retry' and item('dubResultMessage').property('text')=='Recognition fixture failure.'
    click(item('dubEditSetup'));assert folder.isVisible()
    model.setFilename('updated.mp4')
    assert not service.can_retry() and model.state=='ready'
    service._state=DubState(21,'completed',destination=str(Path.cwd()/'result.mp4'),message='Validated output saved.')
    model.refresh();QTest.qWait(30)
    assert primary.property('text')=='Open folder' and primary.isEnabled()
    assert not status.isVisible() and item('dubResultMessage').isVisible()
    assert not warnings,warnings
finally:
    service._thread=None
    window.close();app.processEvents();model.shutdown()
''')
    assert result.returncode==0,result.stdout+result.stderr
