import pytest
from tests.presentation.test_export_qml import EXPORT_SETUP
from tests.presentation.test_workspace_qml import CLEANUP


@pytest.mark.integration
def test_current_frame_eligibility_follows_exact_presented_picture_on_revisit_and_seek(run_python):
    result = run_python(EXPORT_SETUP + r'''
from array import array
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoFrame
from mediaforge.application.contracts.source_timeline import SourcePresentationTimeline, SourceSignature
from mediaforge.application.services.image_export import capture as capture_image
from mediaforge.presentation.bridge.playback import AdmittedVideoFrame

first=Path.cwd()/'U01.mp4';second=Path.cwd()/'U02.mp4'
load(first)
first_id=model.activeMediaId
model.selectOperation('output')
assert model.output.setSetting('kind','frame')
assert model.output.setSetting('filename','revisited picture')
QTest.qWait(240)
button=item('exportStart')
assert not button.isEnabled()
clock=SourcePresentationTimeline.from_timestamps(array('q',[0,40000,80000]),40000)

def frame(t):
    picture=QVideoFrame(QImage(64,48,QImage.Format_RGB32))
    picture.setStartTime(t)
    return picture

def indexed(path):
    playback._timeline=clock
    playback._timeline_signature=SourceSignature.capture(path)
    playback._first_presented_start=0
    playback._first_presented_generation=playback._source_generation
    playback._establish_timeline_origin()

indexed(first)
playback._staging_sink.setVideoFrame(frame(0))
assert button.isEnabled()
old=AdmittedVideoFrame(frame(0),playback._source_generation,playback._player_generation,
    playback._player,playback._staging_sink)
load(second)
assert model.selectMedia(first_id)
assert model.output.settings['kind']=='frame' and not button.isEnabled()
playback.admittedVideoFrame.emit(old)
assert not playback.framesReliable and not button.isEnabled()
indexed(first)
playback._staging_sink.setVideoFrame(frame(0))
assert playback.framesReliable and button.isEnabled()

playback._player.durationChanged.emit(120)
notified=[]
model.output.eligibilityChanged.connect(lambda:notified.append(button.isEnabled()))
for _ in range(5):playback.changed.emit()
assert not notified
playback.seek(45)
assert not button.isEnabled()
playback._staging_sink.setVideoFrame(frame(0))  # seek preroll cannot settle frame 2
assert not button.isEnabled()
playback._staging_sink.setVideoFrame(frame(40000))
assert button.isEnabled() and 'Source ordinals [1, 2)' in model.output.summary['message']
playback.stepFrame(1)
assert not button.isEnabled()
playback._staging_sink.setVideoFrame(frame(80000))
assert button.isEnabled() and 'Source ordinals [2, 3)' in model.output.summary['message']
assert notified[0] is False and notified[-1] is True
assert [value for i,value in enumerate(notified) if i==0 or value!=notified[i-1]]==[False,True,False,True],notified

exports.prepare=lambda active,protected,snapshot: capture_image(active,protected,snapshot)
def start(request):
    exports.requests.append(request)
    exports.job=ExportJob(1,'Completed','done','Export completed and validated.',str(request.destination))
    return True
exports.start_request=start
QMetaObject.invokeMethod(button,'clicked')
assert len(exports.requests)==1
request=exports.requests[0]
assert request.source==first and (request.start,request.end)==(2,3)
assert model.output.state=='Completed' and not model.output.actionError
assert model.selectMedia(model._workspace.items[1].id)
assert not model.output.outputPath and not model.output.message
''' + CLEANUP)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_terminal_status_belongs_to_initiating_source_without_viewer_retirement(run_python):
    result = run_python(EXPORT_SETUP + r'''
from types import SimpleNamespace
first_path=Path.cwd()/'first original.mp4'
second_path=Path.cwd()/'second original.mp4'
load(first_path)
first=model._workspace.active
model.selectOperation('output');QTest.qWait(240)
output=model.output
complete='Export completed and validated.'
published=Path.cwd()/'validated.png'
published.write_bytes(b'publication witness')
output._revealer=SimpleNamespace(reveal_output=lambda path:
    SimpleNamespace(succeeded=Path(path)==published,message='Opened'))

def finish(state,message,path='',error=''):
    exports.busy=False
    exports.job=ExportJob(exports.job.job_id,state,'done',message,path,error)
    output.refresh();app.processEvents()

assert output.setSetting('filename','fresh')
assert output.startExport()
backend=model.playback._player
generation=model.playback._source_generation
finish('Completed',complete,str(published))
assert model.playback._player is backend and model.playback._source_generation==generation
assert item('exportStatus').property('text').count(complete)==1
assert not output.actionError and not item('exportActionError').isVisible()
assert item('exportReveal').isVisible() and output.revealOutput()

def blank():
    assert output.state=='Idle' and not output.outputPath and not output.message and not output.actionError
    assert not item('exportStatus').property('text') and not item('exportReveal').isVisible()
    assert not item('exportActionError').isVisible()

load(second_path);model.selectOperation('output');QTest.qWait(240)
blank()
output.refresh()
blank()
assert published.read_bytes()==b'publication witness' and exports.job.state=='Completed'
assert model.selectMedia(first.id)
blank()
assert output.setSetting('filename','normal')
assert output.startExport()
finish('Failed','Insufficient host RAM/commit.',error='insufficient_memory')
assert output.state=='Failed' and item('exportStatus').property('text')=='Insufficient host RAM/commit.'
assert not output.outputPath and not item('exportReveal').isVisible()
assert output.startExport()
finish('Completed',complete,str(published))
assert output.message==complete and not output.actionError
''' + CLEANUP)
    assert result.returncode == 0, result.stdout + result.stderr

@pytest.mark.integration
def test_standard_export_refuses_collision_and_captures_only_new_destination(run_python):
    result=run_python(EXPORT_SETUP+r'''
from mediaforge.application.services.export import ExportService
exports.prepare=lambda source, protected_sources=(): ExportService(None).prepare(source,protected_sources)
load(Path.cwd()/'confirm.mp4');model.selectOperation('output');QTest.qWait(240)
assert model.output.settings['filename']=='confirm'
assert not model.output.startExport() and 'sources are never overwritten' in model.output.actionError
target=Path.cwd()/'occupied.mp4';target.write_bytes(b'old export')
assert model.output.setSetting('filename','occupied')
assert not model.output.startExport()
assert 'Choose a different filename' in model.output.actionError and item('exportActionError').isVisible()
assert not exports.requests and target.read_bytes()==b'old export'
assert model.output.setSetting('h264_quality','23')
assert not model.output.startExport()
assert model.output.setSetting('filename','новый результат.mov')
assert model.output.startExport()
assert len(exports.requests)==1 and exports.requests[0].quality==23
assert exports.requests[0].replacement is None and exports.requests[0].destination.name=='новый результат.mp4'
model.output.cancelExport()
assert model.output.setSetting('filename','confirm.mp4')
assert not model.output.startExport() and 'sources are never overwritten' in model.output.actionError
assert len(exports.requests)==1 and (Path.cwd()/'confirm.mp4').read_bytes()==b'fixture'
'''+CLEANUP)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.integration
def test_output_choices_follow_scrolled_fields_after_resize_and_fullscreen(run_python):
    result=run_python(EXPORT_SETUP+r'''
load(Path.cwd()/'choices.mp4');model.selectOperation('output');QTest.qWait(240)
scroll=item('inspectorScroll').property('contentItem')
def check(name, scrolled=False):
    field=item(name)
    field.forceActiveFocus(Qt.TabFocusReason);QTest.qWait(50)
    if scrolled:
        content=scroll.property('contentItem')
        field_y=field.mapToItem(content,QPointF()).y()
        scroll.setProperty('contentY',min(field_y-80,scroll.property('contentHeight')-scroll.height()))
        QTest.qWait(60)
        assert scroll.property('contentY')>0,(name,field_y,scroll.property('contentHeight'),scroll.height())
    QTest.keyClick(window,Qt.Key_Space);QTest.qWait(90)
    body=item(name+'Choices')
    assert body.isVisible(),name
    anchor=field.mapToScene(QPointF())
    p=body.mapToScene(QPointF())
    assert abs(p.x()-anchor.x()-6)<2,(name,p,anchor)
    assert 8<=p.y() and p.y()+body.height()<=window.height()-8,(name,p,body.height())
    assert min(abs(p.y()-anchor.y()-field.height()-10),abs(p.y()+body.height()-anchor.y()+10))<2,(name,p,anchor)
    QTest.keyClick(window,Qt.Key_Escape);QTest.qWait(60)
    assert not body.isVisible() and field.property('activeFocus')
check('exportKind')
window.resize(800,560);QTest.qWait(150)
assert model.output.setSetting('container','mp4') and model.output.setSetting('codec','h264')
check('exportSpeed',True)
QMetaObject.invokeMethod(item('fullscreenButton'),'clicked');QTest.qWait(180)
playback.exitFullscreen();QTest.qWait(180)
check('exportKind');check('exportContainer')
'''+CLEANUP)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.integration
def test_trim_exact_vfr_readout_matches_numbered_export_range(run_python):
    result=run_python(EXPORT_SETUP+r'''
from array import array
from mediaforge.application.contracts.source_timeline import SourcePresentationTimeline, SourceSignature
from mediaforge.application.services.image_export import capture as capture_sequence
source=Path.cwd()/'trim.mp4';load(source)
model.selectOperation('trim'); model.setOperationEnabled('trim',True)
assert model.setTrimRange(60,260)
playback._indexing=True;playback.changed.emit();QTest.qWait(60)
assert 'Indexing' in item('trimSelectedFrames').property('text')
timeline=SourcePresentationTimeline.from_timestamps(array('q',[0,40000,120000,210000,260000,400000]),40000)
playback._timeline=timeline;playback._timeline_signature=SourceSignature.capture(source)
playback._origin_established=True;playback._origin_generation=playback._source_generation
playback._timeline_offset=0;playback._indexing=False;playback.changed.emit();QTest.qWait(60)
assert item('trimInFrame').property('text')=='Frame 3 included'
assert item('trimOutFrame').property('text')=='After frame 4'
assert item('trimSelectedFrames').property('text')=='2 frames'
assert item('trimSelectedDuration').property('text')=='00:00.200'
assert model.output.setSetting('kind','sequence')
assert model.output.setSetting('filename','результат')
assert model.output.setSetting('sequence_start','0000000001')
request=capture_sequence(model._workspace.active,snapshot=playback.export_picture_snapshot())
assert (request.start,request.end)==(2,4)
assert request.numbering.name(1)=='результат.0000000002.png'
'''+CLEANUP)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.integration
def test_trim_gestures_keep_settled_vfr_frame_and_manual_edit_is_per_boundary(run_python):
    result = run_python(EXPORT_SETUP+r'''
from array import array
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoFrame
from mediaforge.application.contracts.source_timeline import SourcePresentationTimeline, SourceSignature
from mediaforge.application.services.image_export import video_trim_span
source=Path.cwd()/'gestures.mp4';load(source)
model.selectOperation('trim');model.setOperationEnabled('trim',True)
timeline=SourcePresentationTimeline.from_timestamps(array('q',[2000033,2016800,2049733,2066500,2099967]),16767)
playback._timeline=timeline;playback._timeline_signature=SourceSignature.capture(source)
playback._origin_established=True;playback._origin_generation=playback._source_generation
playback._timeline_offset=-2000033;playback._indexing=False
def settled(ordinal, ms):
    timestamp=timeline.timestamps[ordinal]+playback._timeline_offset
    frame=QVideoFrame(QImage(64,48,QImage.Format_RGB32));frame.setStartTime(timestamp)
    playback._presentation_sink.setVideoFrame(frame)
    playback._current_index=ordinal;playback._presented_start=timestamp
    playback._position=ms;playback._request_position=None;playback._seek_waiting=False
    playback.changed.emit()
    assert playback.framesReliable
def bounds():return video_trim_span(model._workspace.active,playback.export_picture_snapshot())
settled(2,57)
QMetaObject.invokeMethod(item('setTrimInButton'),'clicked')
QMetaObject.invokeMethod(item('setTrimOutButton'),'clicked')
assert bounds()==(2,3)
assert model.trimReadout==dict(first=3,last=3,count=1,durationMs=17)
assert item('trimInFrame').property('text')=='Frame 3 included'
assert item('trimOutFrame').property('text')=='After frame 3'
settled(3,74)
QMetaObject.invokeMethod(item('setTrimOutButton'),'clicked')
assert bounds()==(2,4) and model.trimReadout['durationMs']==50
assert model.setTrimBoundary(True,74) and bounds()==(2,4)
assert model._workspace.active.trim.in_picture is not None
assert model._workspace.active.trim.out_picture is None
assert model.setTrimBoundary(False,57) and bounds()==(3,4)
settled(0,4)
assert model.setTrimInAtPlayhead() and bounds()==(0,4)
settled(4,108)
assert model.setTrimOutAtPlayhead() and bounds()==(0,5)
assert model.trimReadout['durationMs']==117
'''+CLEANUP)
    assert result.returncode == 0, result.stdout+result.stderr


@pytest.mark.integration
def test_exact_source_reset_custom_pixel_aspect_and_type_options(run_python):
    code=EXPORT_SETUP.replace('25., 25., 250))','25., 25., 250, sample_aspect_ratio="3:2", audio_streams=("Audio 1", "Audio 2")))')
    result=run_python(code+r'''
load(Path.cwd()/'anamorphic.mp4');model.selectOperation('resize');model.setOperationEnabled('resize',True);QTest.qWait(240)
assert model.editState['pixelAspectText']=='1.0'
QMetaObject.invokeMethod(item('pixelAspectSource'),'clicked')
assert model.output.summary['sar']=='3:2' and model.editState['pixelAspectText']=='3/2'
assert model.setReformat('pixel_aspect','0.75') and model.output.summary['sar']=='3:4'
assert model.editState['standaloneReason']
assert model.setReformat('pixel_aspect','1.0') and not model.editState['standaloneReason']
assert model.setReformat('mode','format') and model.setReformat('preset','Custom')
assert model.setReformat('width','1024') and model.setReformat('height','768')
QMetaObject.invokeMethod(item('pixelAspect169'),'clicked')
assert model.output.summary['sar']=='4:3' and model.previewPlan['displaySar']==4/3
model.setOperationEnabled('resize',False)
assert model.output.summary['sar']=='3:2'
model.selectOperation('output');QTest.qWait(240)
assert item('exportContainer').property('count')==4
assert model.output.setSetting('container','mp4')
assert item('exportCodec').property('count')==1
assert not item('exportH264Quality').isVisible()
assert model.output.setSetting('quality_preset','balanced') and model.output.settings['h264_quality']==21
assert model.output.setSetting('speed','quality') and model.output.setSetting('h264_profile','main')
assert 'slow' in model.output.summary['qualityDescription']
assert model.output.setSetting('container','audio')
assert item('exportAudioFormat').isVisible() and not item('exportCodec').isVisible()
assert item('exportAudioStream').property('count')==2
assert model.output.setSetting('audio_stream','1') and model.output.setSetting('audio_format','mp3')
assert model.output.summary['extension']=='.mp3' and model.output.summary['audioOnly']
assert not exports.requests
'''+CLEANUP)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.integration
def test_pixel_aspect_digit_wheel_and_exact_fraction_paste(run_python):
    result=run_python(EXPORT_SETUP+r'''
from PySide6.QtGui import QWheelEvent
load(Path.cwd()/'digits.mp4');model.selectOperation('resize');model.setOperationEnabled('resize',True);QTest.qWait(240)
field=item('pixelAspectInput')
def wheel(text,caret):
    field.forceActiveFocus();field.setProperty('text',text);field.setProperty('cursorPosition',caret)
    point=field.mapToScene(QPointF(field.width()/2,field.height()/2))
    QTest.mouseMove(window,QPoint(round(point.x()),round(point.y())));QTest.qWait(20)
    event=QWheelEvent(point,point,QPoint(),QPoint(0,120),Qt.NoButton,Qt.NoModifier,Qt.ScrollUpdate,False)
    app.sendEvent(window,event);QTest.qWait(30)
wheel('1.0',0);assert field.property('text')=='2.0' and model.output.summary['sar']=='2:1'
wheel('1.0',2);assert field.property('text')=='1.1' and model.output.summary['sar']=='11:10'
wheel('1.0',3);assert field.property('text')=='1.01' and model.output.summary['sar']=='101:100'
field.forceActiveFocus();field.selectAll();app.clipboard().setText('4/3')
QTest.keyClick(window,Qt.Key_V,Qt.ControlModifier);QTest.keyClick(window,Qt.Key_Return);QTest.qWait(30)
assert model.output.summary['sar']=='4:3'
assert not exports.requests
'''+CLEANUP)
    assert result.returncode==0,result.stdout+result.stderr
