"""Real metadata/decoder ownership and rendered layout; no desktop paint claim."""
import os
import pytest

pytestmark=[pytest.mark.native,pytest.mark.integration]


NATIVE_SETUP = r'''
import os,time,logging,subprocess,hashlib,json
from pathlib import Path
from PySide6.QtCore import QObject,QUrl,QPointF,QMetaObject,Qt
from PySide6.QtGui import QGuiApplication,QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from mediaforge.bootstrap.application import build_media_view_model,build_dub_view_models
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegRuntimeResolver,FfmpegManifest
from mediaforge.infrastructure.resources.paths import qml_root
from mediaforge.presentation.bridge.navigation import Navigation
runtime=FfmpegRuntimeResolver(FfmpegManifest.load()).verify_directory(Path(os.environ['MEDIAFORGE_TEST_RUNTIME_ROOT']))
os.environ['MEDIAFORGE_FFPROBE_PATH']=str(runtime.ffprobe)
app=QGuiApplication([])
if not QFontDatabase.families():
    for name in ('segoeui.ttf','seguisb.ttf'):
        assert QFontDatabase.addApplicationFontFromData((Path('C:/Windows/Fonts')/name).read_bytes())>=0
paths=ProfilePaths.from_environment();paths.activate_temporary()
source=Path.cwd()/'shared original Кириллица.mp4'
subprocess.run([str(runtime.ffmpeg),'-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=96x64:rate=25:duration=2',
    '-f','lavfi','-i','sine=frequency=400:sample_rate=48000:duration=2','-c:v','libx264','-pix_fmt','yuv420p',
    '-threads','1','-c:a','aac',str(source)],check=True,timeout=20,env=paths.child_environment(),cwd=paths.temporary)
original=hashlib.sha256(source.read_bytes()).hexdigest()
gate=MediaProcessingGate();logger=logging.getLogger('shared-layout')
media=build_media_view_model(logger,profile=paths,gate=gate)
dub,models=build_dub_view_models(paths,gate,logger);nav=Navigation(media,dub,models)
engine=QQmlApplicationEngine();warnings=[]
engine.warnings.connect(lambda rows:warnings.extend(x.toString() for x in rows))
for name,value in dict(mediaViewModel=media,dubViewModel=dub,modelsViewModel=models,appNavigation=nav).items():
    engine.rootContext().setContextProperty(name,value)
engine.load(QUrl.fromLocalFile(str(qml_root())));assert engine.rootObjects(),warnings
window=engine.rootObjects()[0]
def find_visual(name):
    def visit(item):
        if item.objectName()==name:return item
        for child in item.childItems():
            found=visit(child)
            if found is not None:return found
    return visit(window.contentItem())
def wait(condition):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        app.processEvents();dub.refresh()
        if condition():return
        QTest.qWait(10)
    raise AssertionError('Real shared-source transition did not settle')
'''

def test_one_loaded_original_and_player_survive_page_and_responsive_layout_changes(run_python):
    if not os.environ.get('MEDIAFORGE_TEST_RUNTIME_ROOT'):
        pytest.skip('Explicit authenticated managed FFmpeg root required.')
    result=run_python(NATIVE_SETUP+r'''
try:
    assert nav.navigate('Dub') and nav.openSource(QUrl.fromLocalFile(str(source)))
    wait(lambda:media.state=='loaded' and nav.canImport and not dub.busy)
    wait(lambda:media.playback.videoPictureAvailable)
    player=media.playback._player;selected=media.activeMediaId
    assert dub.service.source().identity.path==source
    geometry={}
    assert dub.chooseFolder(QUrl.fromLocalFile(str(Path.cwd())))
    expected={
        1440: ((18,18,190,864),(242,24,804,50),(242,92,804,452),(242,556,804,70),(242,646,804,92),(242,756,804,126),(1070,92,342,790)),
        1024: ((18,18,140,732),(180,24,516,50),(180,92,516,330),(180,434,516,70),(180,516,516,88),(180,616,516,134),(714,92,292,658)),
        800: ((18,18,76,524),(112,24,374,50),(112,92,374,210),(112,314,374,64),(112,390,374,62),(112,464,374,78),(502,92,280,450))}
    names=('mediaSidebar','compactMediaHeader','windowedViewerHost','windowedTransport','workspaceContext','mediaTray','sharedInspector')
    def bounds(name):
        item=find_visual(name);assert item is not None,name
        point=item.mapToScene(QPointF(0,0))
        return tuple(round(v,3) for v in (point.x(),point.y(),item.width(),item.height()))
    def font(item):
        style=item.property('font')
        return (style.family(),style.pixelSize(),style.weight(),item.property('color').name())
    def action(name):
        item=find_visual(name)
        return (bounds(name),font(item.property('contentItem')),item.property('background').property('radius'))
    def panel(name):
        from PySide6.QtQml import QQmlEngine,QQmlExpression
        item=find_visual(name)
        context=QQmlEngine.contextForObject(item)
        width,undefined=QQmlExpression(context,item,'border.width').evaluate()
        assert not undefined
        color,undefined=QQmlExpression(context,item,'border.color').evaluate()
        assert not undefined
        return (bounds(name),item.property('radius'),item.property('color').name(),
                width,color.name())
    strip_names=('mediaTrayAdd','mediaTrayList','trayItem0')
    expected_strip={1440: (68,706,238,80), 1024: (68,352,238,88), 800: (48,230,230,58)}
    for width,height in ((1440,900),(1024,768),(800,560),(1440,900)):
        window.setWidth(width);window.setHeight(height);QTest.qWait(180)
        assert window.findChild(QObject,'dubPage') is not None
        shared_tray=window.findChild(QObject,'mediaTray')
        QMetaObject.invokeMethod(shared_tray,'openActiveContext');QTest.qWait(20)
        remove_action=find_visual('contextRemove');assert remove_action is not None and remove_action.property('enabled')
        QTest.keyClick(window,Qt.Key_Escape)
        assert not window.findChild(QObject,'emptyMediaState').property('visible')
        assert len(window.findChildren(QObject,'viewerVideoOutput'))==1
        assert window.grabWindow().save(str(Path.cwd()/f'dub-{width}x{height}.png'))
        actual=tuple(bounds(name) for name in names)
        assert actual==expected[width],(width,actual)
        strip=tuple(bounds(name) for name in strip_names)
        assert tuple(item[2] for item in strip)+(strip[-1][3],)==expected_strip[width],strip
        assert find_visual('mediaTrayList').property('spacing')==10
        dub_action=action('dubPrimary')
        dub_panel=panel('dubPage')
        dub_heading=(bounds('dubInspectorHeading')[:2],font(find_visual('dubInspectorHeading')))
        path, browse=bounds('dubFullDestination'), bounds('dubChooseFolder')
        assert path[0]==browse[0] and path[2]==browse[2] and path[1]+path[3]<browse[1]
        assert browse[1]+browse[3]<=dub_action[0][1]
        geometry[str(width)]=dict(workspace=actual,strip=strip,action=dub_action,
                                  panel=dub_panel,heading=dub_heading,folder=(path,browse),
                                  dub_enabled=find_visual('dubPrimary').isEnabled())
        assert nav.navigate('Media');QTest.qWait(50)
        assert tuple(bounds(name) for name in names)==actual
        assert tuple(bounds(name) for name in strip_names)==strip
        media.setOperationEnabled('trim',True);assert media.setTrimRange(400,1400)
        for operation in ('trim','crop','resize','output'):
            media.selectOperation(operation)
            QTest.qWait(15)
            assert tuple(bounds(name) for name in names)==actual
            assert window.findChild(QObject,'trimInHandle').property('visible')
        media_action=action('exportStart')
        assert media_action[0]==dub_action[0] and media_action[2]==dub_action[2]
        assert media_action[1][:3]==dub_action[1][:3]
        if find_visual('exportStart').isEnabled()==geometry[str(width)]['dub_enabled']:
            assert media_action[1][3]==dub_action[1][3]
        geometry[str(width)]['media_action']=media_action
        assert panel('mediaInspector')==dub_panel
        media_heading=next(child for row in find_visual('inspectorContent').childItems()
                           for child in row.childItems() if child.property('text')=='Output')
        point=media_heading.mapToScene(QPointF())
        assert ((point.x(),point.y()),font(media_heading))==dub_heading,(point,font(media_heading),dub_heading)
        QTest.qWait(220)
        assert window.grabWindow().save(str(Path.cwd()/f'media-{width}x{height}.png'))
        assert media.playback._player is player and media.activeMediaId==selected
        assert window.findChild(QObject,'loadedMediaState').property('visible')
        assert nav.navigate('Dub');QTest.qWait(50)
        assert not window.findChild(QObject,'trimInHandle').property('visible')
        assert dub.service.plan().duration==2
        assert media.playback._player is player and media.activeMediaId==selected
    (Path.cwd()/'geometry.json').write_text(json.dumps(geometry),encoding='utf-8')
    assert nav.clearSource();wait(lambda:media.state=='empty' and not dub.busy and window.findChild(QObject,'dubPrimary') is None)
    assert window.findChild(QObject,'emptyMediaState').property('visible')
    assert window.findChild(QObject,'dubPrimary') is None
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original
    assert not warnings,warnings
finally:
    models.shutdown();dub.shutdown();media.shutdown();window.close();app.processEvents()
assert not gate.busy and not gate.failure
''')
    assert result.returncode==0,result.stdout+result.stderr

def test_media_uses_original_without_a_quality_control_or_proxy_workers(run_python):
    if not os.environ.get('MEDIAFORGE_TEST_RUNTIME_ROOT'):
        pytest.skip('Explicit authenticated managed FFmpeg root required.')
    result = run_python(NATIVE_SETUP + r'''
import sys
from PySide6.QtCore import QPoint
try:
    assert nav.openSource(QUrl.fromLocalFile(str(source)))
    wait(lambda:media.state=='loaded' and nav.canImport and media.playback.videoPictureAvailable)
    renderer=find_visual('viewerVideoOutput')
    player=media.playback._player
    first=media.activeMediaId
    assert renderer is not None and media.playback._source==source
    assert (media.playback._presentation_sink.videoFrame().width(),
            media.playback._presentation_sink.videoFrame().height())==(96,64)
    assert find_visual('imagePreviewQuality') is None
    assert not hasattr(media,'setImageQuality') and not hasattr(media._workspace.active.video,'quality')
    assert nav.navigate('Dub') and nav.navigate('Media')
    fullscreen=find_visual('fullscreenButton')
    point=fullscreen.mapToScene(QPointF(fullscreen.width()/2,fullscreen.height()/2))
    QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,QPoint(round(point.x()),round(point.y())))
    assert media.playback.fullscreen
    QTest.keyClick(window,Qt.Key_Escape)
    assert not media.playback.fullscreen and media.playback._player is player
    wait(lambda:nav.canImport and not dub.busy)
    other=Path.cwd()/'shared second.mp4'
    other.write_bytes(source.read_bytes())
    assert nav.openSource(QUrl.fromLocalFile(str(other)))
    wait(lambda:nav.canImport and media.playback.videoPictureAvailable and media.playback._source==other)
    assert media.selectMedia(first)
    wait(lambda:nav.canImport and media.playback.videoPictureAvailable and media.playback._source==source)
    assert media.original_source().path==str(source)
    assert find_visual('viewerVideoOutput') is renderer
    assert not any(name.endswith('.video_preview') for name in sys.modules)
    assert not (paths.temporary/'video-previews').exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original
    assert not warnings,warnings
finally:
    models.shutdown();media.shutdown();dub.shutdown();window.close();app.processEvents()
assert not gate.busy and not gate.failure
''')
    assert result.returncode == 0, result.stdout + result.stderr


def test_dub_source_rechecks_after_a_portable_log_sharing_conflict(run_python):
    if not os.environ.get('MEDIAFORGE_TEST_RUNTIME_ROOT'):
        pytest.skip('Explicit authenticated managed FFmpeg root required.')
    result = run_python(NATIVE_SETUP + r'''
import ctypes
from ctypes import wintypes
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel.CreateFileW.restype = wintypes.HANDLE
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
log = paths.logs / 'application.log'
log.write_bytes(b'synthetic protected log')
handle = kernel.CreateFileW(str(log), 0x80000000, 0, None, 3, 0, None)
assert handle != ctypes.c_void_p(-1).value
try:
    assert nav.navigate('Dub') and nav.openSource(QUrl.fromLocalFile(str(source)))
    wait(lambda: not dub.busy and dub.state == 'failed')
    assert dub.canRetrySource and not dub.canStart
    assert dub.operation['code'] == 'portable_data_unavailable'
    assert 'portable application logs' in dub.blocker
    assert find_visual('dubPrimary').property('text') == 'Check source again'
    assert nav.navigate('Media') and nav.navigate('Dub')
    assert dub.state == 'failed' and not dub.busy
    assert kernel.CloseHandle(handle)
    handle = None
    assert dub.retrySource()
    wait(lambda: dub.state == 'ready' and not dub.busy)
    assert not dub.canRetrySource and dub.service.plan().duration == 2
    assert dub.service.source().identity.path == source
    assert log.read_bytes() == b'synthetic protected log'
    assert original == hashlib.sha256(source.read_bytes()).hexdigest()
finally:
    if handle is not None:
        assert kernel.CloseHandle(handle)
    models.shutdown();dub.shutdown();media.shutdown();window.close();app.processEvents()
assert not gate.busy and not gate.failure
''')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('page', ['Media', 'Dub'])
def test_shared_sequence_admission_and_loaded_replacement_feedback(run_python, page):
    if not os.environ.get('MEDIAFORGE_TEST_RUNTIME_ROOT'):
        pytest.skip('Explicit authenticated managed FFmpeg root required.')
    from pathlib import Path
    repo=Path(__file__).resolve().parents[2]
    result = run_python(NATIVE_SETUP + f'\npage={page!r}\nimport sys; sys.path.insert(0,{str(repo)!r})\n' + r'''
from PySide6.QtGui import QImage,QColor
from mediaforge.application.services.images import ImageSourceService
from mediaforge.infrastructure.images.reader import QtImageReader
from tests.unit.test_image_preview import Resources
from threading import Event
window.resize(800,560)
def url(path):return QUrl.fromLocalFile(str(path))
def settle():
    wait(lambda:nav.canImport and not media.imagePreview.busy and not dub.busy)
try:
    assert nav.openSource(url(source))
    wait(lambda:media.state=='loaded' and nav.canImport)
    active=media.activeMediaId
    media.setOperationEnabled('trim',True);assert media.setTrimRange(400,1400)
    assert nav.navigate(page);settle()
    bad=Path.cwd()/'Повреждённый.mp4';bad.write_bytes(b'not a valid MP4')
    assert nav.replaceSource(active,url(bad))
    wait(lambda:nav.canImport and bool(media.errorMessage))
    notice=find_visual('loadedSourceError')
    assert notice.isVisible() and 'metadata' in media.errorMessage.lower()
    assert media.activeMediaId==active and media.editState['trimIn']==400 and len(media.mediaItems)==1
    assert window.grabWindow().save(str(Path.cwd()/'loaded-replacement-error.png'))
    assert nav.openSource(url(source));settle()
    assert not media.errorMessage and not notice.isVisible() and media.activeMediaId==active

    # Test-only renderer admission seam for an offscreen fixture. Native
    # source locks, Qt image readers, async controller and QML stay real.
    preview=media.imagePreview
    image_service=ImageSourceService(QtImageReader(),resources=Resources())
    preview.service=media._image_service=image_service
    preview._backend=preview.qualified_backend
    def frames(folder):
        folder.mkdir()
        for number in (1001,1002):
            image=QImage(96,64,QImage.Format_RGB32);image.fill(QColor(number-950,80,90))
            assert image.save(str(folder/f'Кадр_{number}.png'))
        return folder/'Кадр_1002.png'
    second=frames(Path.cwd()/'Кадры A')
    replacement=frames(Path.cwd()/'Кадры B')
    dialog=window.findChild(QObject,'sequenceConfirmationDialog')
    def pending():
        wait(lambda:bool(media.sequenceConfirmation) and dialog.property('visible'))
        assert media.sequenceConfirmation['count']==2
    def confirm():
        assert QMetaObject.invokeMethod(dialog,'accept')
        wait(lambda:not media.sequenceConfirmation and not dialog.property('visible'))
        settle()
        assert media.sourceKind=='sequence' and media.sequenceInfo['fps']=='24',(
            media.sourceKind,media.sequenceInfo,media.errorMessage,preview.status,preview._backend)
    assert nav.openSource(url(second));pending()
    assert media.activeMediaId==active
    token=gate.acquire();assert token is not None
    assert not media.confirmSequence('24') and not nav.selectSource(active)
    assert media.sequenceConfirmation and media.activeMediaId==active
    gate.release(token)
    assert QMetaObject.invokeMethod(dialog,'reject')
    wait(lambda:not media.sequenceConfirmation and not dialog.property('visible'))
    assert media.activeMediaId==active and media.editState['trimIn']==400
    assert nav.openSource(url(second));pending();confirm()
    sequence=media.activeMediaId
    assert sequence!=active and media.localPath==str(second.with_name('Кадр_1001.png'))
    assert nav.selectSource(active);settle();assert media.editState['trimIn']==400
    assert nav.selectSource(sequence);settle();assert media.activeMediaId==sequence
    assert nav.selectSource(active);settle()
    assert nav.replaceSource(active,url(replacement));pending()
    assert media.activeMediaId==active and len(media.mediaItems)==2
    assert QMetaObject.invokeMethod(dialog,'reject')
    wait(lambda:not media.sequenceConfirmation and not dialog.property('visible'))
    assert nav.openSource(url(replacement));pending();confirm()
    assert len(media.mediaItems)==3  # Cancelled replacement cannot delete the old item.
    replacement_id=media.activeMediaId
    assert nav.selectSource(active);settle()
    assert nav.replaceSource(active,url(replacement));pending();confirm()
    assert media.activeMediaId==replacement_id and len(media.mediaItems)==2
    assert not any(row['id']==active for row in media.mediaItems)
    assert not notice.isVisible() and not media.errorMessage
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original
    assert not warnings,warnings
finally:
    models.shutdown();dub.shutdown();media.shutdown();window.close();app.processEvents()
assert not gate.busy and not gate.failure
''')
    assert result.returncode == 0, result.stdout + result.stderr
