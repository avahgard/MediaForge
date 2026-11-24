"""Cross-tab ownership controls; native picture/decoder evidence is separate."""
from pathlib import Path
import pytest

pytestmark=pytest.mark.integration


def test_original_selection_shared_without_reimport_or_media_recipe_loss(run_python):
    root=Path(__file__).resolve().parents[2]
    code='import sys\nsys.path[:0]='+repr([str(root/'tests/presentation'),str(root/'tests/unit')])+'\n'+r'''
from pathlib import Path
from types import SimpleNamespace as NS
from dataclasses import replace
import time
from threading import Event
from PySide6.QtCore import QObject,Signal,QCoreApplication,QUrl
from test_media_bridge import RecordingPlayback, ResultUseCase, success_result
from test_dub_contracts import source
from mediaforge.presentation.bridge.media import MediaViewModel
from mediaforge.presentation.bridge.dub import DubViewModel
from mediaforge.presentation.bridge.navigation import Navigation
from mediaforge.application.services.dub import DubService
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.application.services.voice_preview import VoicePreviewState
from mediaforge.application.contracts.media import OpenMediaResult,OpenMediaErrorCode
app=QCoreApplication([])
class Playback(QObject,RecordingPlayback):
    changed=Signal()
    playing=False
    videoPictureAvailable=False
    def __init__(self):QObject.__init__(self);RecordingPlayback.__init__(self)
    def pauseForExternal(self):self.playing=False
class Player(QObject):
    changed=Signal()
    def stop(self):pass
    def shutdown(self):pass
class Preview:
    busy=False
    def snapshot(self):return VoicePreviewState()
    def shutdown(self):pass
first=Path.cwd()/'first Cyrillic source.mp4';first.write_bytes(b'original A')
second=Path.cwd()/'second.mp4';second.write_bytes(b'original B')
use=ResultUseCase(success_result(first),success_result(second),OpenMediaResult(error_code=OpenMediaErrorCode.PROBE_FAILED,error_message='Corrupt candidate'))
gate=MediaProcessingGate();media=MediaViewModel(use,playback_controller=Playback(),processing_gate=gate)
inspected=[];inspection_entered=Event();inspection_release=Event();inspection_release.set()
def inspect(path,control):
    inspected.append(path);inspection_entered.set();assert inspection_release.wait(4);s=source();return replace(s,identity=replace(s.identity,path=path))
sessions=NS(close=lambda c:None,expiry_due=lambda:False,retry_eligible=lambda _:False)
service=DubService(NS(inspect=inspect),NS(sessions=sessions),None,gate,('aiden',))
dub=DubViewModel(service,Preview(),NS(allInstalled=False,busy=False,components=[]),player=Player())
nav=Navigation(media,dub,NS(busy=False))
def until(condition):
    end=time.monotonic()+4
    while time.monotonic()<end:
        app.processEvents();dub.refresh()
        if condition():return
        time.sleep(.005)
    raise AssertionError('Shared-source transition did not settle')
try:
    assert nav.navigate('Dub')
    assert nav.openSource(QUrl.fromLocalFile(str(first)))
    until(lambda:nav.canImport and service.source() is not None)
    first_id=media.activeMediaId
    assert service.source().identity.path==first
    assert nav.navigate('Media')
    media.setOperationEnabled('crop',True);media.selectOperation('crop')
    assert media.setCropRect(20,22,500,400)
    media.setOperationEnabled('trim',True);assert media.setTrimRange(1000,5000)
    assert nav.navigate('Dub')
    assert not media.cropEditAllowed and media.previewPlan['cropX']==0
    assert nav.navigate('Media')
    assert media.editState['cropX']==20 and media.editState['trimIn']==1000
    assert len(use.paths)==1 and len(inspected)==1 and media.activeMediaId==first_id
    inspection_release.clear();inspection_entered.clear()
    assert nav.navigate('Dub') and nav.dropSource([QUrl.fromLocalFile(str(second))])
    until(inspection_entered.is_set)
    assert gate.busy and not nav.canImport
    assert nav.navigate('Media') and nav.navigate('Dub')
    assert not nav.removeSource(first_id) and not nav.selectSource(first_id)
    inspection_release.set()
    until(lambda:nav.canImport and service.source() and service.source().identity.path==second)
    assert len(media.mediaItems)==2
    assert nav.replaceSource(media.activeMediaId,QUrl.fromLocalFile(str(Path.cwd()/'bad.mp4')))
    until(lambda:nav.canImport)
    assert media.original_source().path==str(second) and service.source().identity.path==second
    assert 'Corrupt' in media.errorMessage
    token=gate.acquire();assert token
    assert not nav.openSource(QUrl.fromLocalFile(str(first))) and not nav.clearSource()
    gate.release(token)
    assert len(media.mediaItems)==2
    assert nav.replaceSource(media.activeMediaId,QUrl.fromLocalFile(str(first)))
    until(lambda:nav.canImport and service.source() and service.source().identity.path==first)
    assert len(media.mediaItems)==1
    assert nav.clearSource()
    until(lambda:nav.canImport and service.source() is None)
    assert media.state=='empty' and media.original_source() is None
    assert first.read_bytes()==b'original A' and second.read_bytes()==b'original B'
finally:
    dub.shutdown();media.shutdown();app.processEvents()
'''
    result=run_python(code)
    assert result.returncode==0,result.stdout+result.stderr
