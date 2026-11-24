import pytest
from tests.presentation.test_workspace_qml import SETUP, CLEANUP


EXPORT_SETUP = SETUP.replace("model = MediaViewModel(use,", r'''
from mediaforge.application.contracts.export import ExportJob
from mediaforge.application.services.export import capture
class Exports:
    busy = False
    def __init__(self): self.requests = []; self.job = ExportJob()
    def start(self, item):
        return self.start_request(capture(item))
    def prepare(self, item, protected_sources=()): return capture(item,protected_sources=protected_sources)
    def start_request(self, request):
        self.requests.append(request); self.busy = True
        self.job = ExportJob(len(self.requests), 'Running', 'processing', 'Indeterminate')
        return True
    def snapshot(self): return self.job
    def cancel(self):
        self.busy = False; self.job = ExportJob(self.job.job_id, 'Cancelled', 'done', 'Cancelled')
    def shutdown(self): self.cancel()
exports = Exports()
model = MediaViewModel(use, export_use_case=exports,''')


@pytest.mark.integration
def test_output_explicit_action_state_matrix_and_per_item(run_python):
    result = run_python(EXPORT_SETUP + r'''
load(Path.cwd() / 'first.mp4')
first = model.activeMediaId
model.selectOperation('output'); QTest.qWait(230)
assert item('exportContainer').isVisible() and item('exportStart').isEnabled()
assert not item('activeOperationToggle').isVisible()
assert model.output.setSetting('container','mov')
assert model.output.setSetting('codec','prores')
assert model.output.summary['extension'] == '.mov'
assert model.output.setSetting('container','mp4')
assert not model.output.summary['valid'] and not item('exportStart').isEnabled()
assert 'MOV' in model.output.summary['message']
assert model.output.setSetting('codec','h264')
assert model.output.setSetting('h264_quality','23')
assert not model.output.setSetting('h264_quality','52')
assert model.output.setSetting('filename','мой экспорт')
assert model.setReformat('pixel_aspect','16:9')
assert model.output.summary['sar'] == '1:1'  # disabled Reformat is inert
model.setOperationEnabled('resize',True)
saved = model.editState.copy()
assert not exports.requests
QMetaObject.invokeMethod(item('exportStart'),'clicked')
assert len(exports.requests) == 1 and model.output.busy and not model.canOpen
assert not item('exportStart').isEnabled() and item('exportCancel').isEnabled()
assert item('exportProgress').isVisible()
assert not model.selectMedia(first)
assert not model.output.setSetting('filename','changed')
QMetaObject.invokeMethod(item('exportCancel'),'clicked')
assert model.canOpen and model.output.state == 'Cancelled'
assert model.editState == saved
assert window.findChild(QObject,'exportButton') is None
QMetaObject.invokeMethod(item('exportStart'),'clicked')
assert len(exports.requests) == 2 and model.output.busy and model.selectedOperation == 'output'
model.output.cancelExport()
assert model.editState == saved
load(Path.cwd() / 'second.mp4')
assert model.output.settings['filename'] == 'second'
model.output.setSetting('filename','second_changed')
assert model.selectMedia(first)
assert model.output.settings['filename'] == 'мой экспорт'
assert model.output.settings['h264_quality'] == 23 and model.editState == saved
''' + CLEANUP)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
@pytest.mark.parametrize("dpi", ["1","1.5","2"])
def test_pixel_aspect_preview_uses_same_canvas_and_one_renderer(run_python, isolated_environment, dpi):
    isolated_environment['QT_SCALE_FACTOR'] = dpi
    code = EXPORT_SETUP.replace("1920, 1080, 25.", "1024, 768, 25.")
    result = run_python(code + r'''
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoFrame
load(Path.cwd()/'game.mp4')
frame = QVideoFrame(QImage(1024,768,QImage.Format_RGB32)); frame.setStartTime(0)
playback._staging_sink.setVideoFrame(frame)
player, video = playback._player, item('viewerVideoOutput')
model.selectOperation('resize'); model.setOperationEnabled('resize',True)
click(item('pixelAspect169'))
assert model.editState['pixelAspect'] == '16:9'
assert model.output.summary['sar'] == '4:3' and model.output.summary['dar'] == '16:9'
assert model.previewPlan['width'] == 1024 and model.previewPlan['height'] == 768
assert model.previewPlan['displaySar'] == 4/3
app.processEvents()
canvas = item('videoViewport')
assert abs(canvas.width()/canvas.height()-16/9) < .00001
model.selectOperation('crop'); model.setOperationEnabled('crop',True)
app.processEvents()
assert model.previewPlan['editing'] and not model.previewPlan['reformatted']
assert 'displaySar' not in model.previewPlan  # Qt owns upstream display shape.
assert abs(canvas.width()/canvas.height()-4/3) < .00001
model.setCropPreview(True); app.processEvents()
assert abs(canvas.width()/canvas.height()-16/9) < .00001
assert (playback._player,item('viewerVideoOutput')) == (player,video)
assert not exports.requests
''' + CLEANUP)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_output_export_keeps_cancel_available_after_switching_tools(run_python):
    result=run_python(EXPORT_SETUP+r'''
load(Path.cwd()/'output-export.mp4')
assert model.output.setSetting('filename','fresh-output')
model.selectOperation('crop');QTest.qWait(230)
assert not item('exportStart').isVisible()
click(item('selectOutput'));QTest.qWait(230)
click(item('exportStart'));QTest.qWait(50)
assert model.output.busy and model.selectedOperation=='output'
assert item('exportCancel').isVisible()
model.selectOperation('trim');QTest.qWait(50)
assert item('exportCancel').isVisible() and item('outputActionArea').isVisible()
click(item('exportCancel'));assert not model.output.busy and model.canOpen
'''+CLEANUP)
    assert result.returncode==0,result.stdout+result.stderr
