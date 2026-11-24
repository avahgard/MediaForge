from pathlib import Path

import pytest


@pytest.mark.integration
def test_numeric_controls_validation_reset_and_terminal_presentation(run_python):
    result = run_python('''
from dataclasses import replace
from PySide6.QtCore import QCoreApplication
from mediaforge.application.contracts.crop import CropJob
from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.presentation.bridge.crop import CropViewModel
app = QCoreApplication([])
class UseCase:
    busy = False
    job = CropJob()
    calls = []
    def set_source(self, source): return not self.busy
    def snapshot(self): return self.job
    def start(self, *rectangle):
        self.calls.append(rectangle)
        self.busy = True
        self.job = CropJob(state="Running")
        return True
    def cancel(self):
        self.job = CropJob(state="Cancelling")
        return True
    def shutdown(self): self.busy = False
case = UseCase()
model = CropViewModel(case)
assert not model.canCrop and not model.canEdit and not model.canCancel
source = MediaMetadataDto("source.mp4", "C:/source.mp4", "mp4", 2, "h264", 641, 361, 25)
model.set_source(source)
assert (model.xText, model.yText, model.widthText, model.heightText) == ("0", "0", "640", "360")
assert model.canCrop and case.calls == []
for value in ["1", "-2", "NaN", "2.0", "1e2", "999999999999999999999999999999", "", "Infinity"]:
    model.xText = value
    assert not model.canCrop and model.validationMessage and not model.startCrop()
assert case.calls == []
model.xText, model.yText, model.widthText, model.heightText = "100", "40", "320", "240"
assert model.startCrop() and case.calls == [(100, 40, 320, 240)]
assert model.state == "Running" and model.canCancel and not model.canCrop and not model.canEdit
assert not model.startCrop()
model.cancelCrop()
assert model.state == "Cancelling" and not model.canCancel
case.busy = False
case.job = CropJob(state="Cancelled", message="Crop cancelled")
model.refresh()
assert model.canCrop and model.state == "Cancelled"
case.job = CropJob(state="Failed", message="Media processing is busy", error="media_processing_busy")
model.refresh()
assert model.canCrop and "busy" in model.message
case.job = CropJob(state="Completed", result="C:/source_crop.mp4", width=320, height=240)
model.refresh()
assert model.outputPath == "C:/source_crop.mp4" and model.resultDimensions == "320 × 240"
model.set_source(replace(source, width=800, height=600))
assert model.state == "Pending" and model.outputPath == ""
assert (model.xText, model.yText, model.widthText, model.heightText) == ("0", "0", "800", "600")
model.set_source(replace(source, width=15))
assert not model.canCrop and not model.canEdit and model.validationMessage
model.shutdown()
assert not model.canCrop
''')
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
@pytest.mark.parametrize("phase", [0, 1], ids=["source_probe", "capability_probe"])
def test_crop_preflight_only_on_request_and_off_ui_thread(run_python, phase):
    support = str(Path(__file__).resolve().parents[1] / "unit")
    result = run_python(f'''
import sys, threading, time
from pathlib import Path
sys.path.insert(0, {support!r})
from crop_support import Factory, setup, metadata
from PySide6.QtCore import QCoreApplication, QTimer, QUrl
from mediaforge.application.contracts.media import OpenMediaResult
from mediaforge.application.services.crop import CropService
from mediaforge.infrastructure.ffmpeg.crop import FfmpegCropExecutor
from mediaforge.presentation.bridge.media import MediaViewModel
app = QCoreApplication([])
request, pair = setup(Path.cwd())
factory = Factory(block={phase})
ui_thread = threading.get_ident()
threads = []
def wrapped(command, **kwargs):
    threads.append(threading.get_ident())
    return factory(command, **kwargs)
class Metadata:
    def open_media(self, path): return OpenMediaResult(metadata=metadata(request.source))
    def cancel(self): pass
service = CropService(FfmpegCropExecutor(pair, process_factory=wrapped))
model = MediaViewModel(Metadata(), crop_use_case=service)
def wait(condition):
    deadline = time.monotonic() + 3
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.001)
    assert condition()
assert model.openMedia(QUrl.fromLocalFile(str(request.source)))
wait(lambda: model.canOpen)
assert model.state == "loaded" and factory.calls == []
model.crop.xText, model.crop.yText, model.crop.widthText, model.crop.heightText = "100", "40", "320", "240"
assert model.crop.startCrop()
wait(factory.entered.is_set)
assert not model.canOpen and not model.openMedia(QUrl.fromLocalFile(str(request.source)))
ticks = []
QTimer.singleShot(0, lambda: ticks.append(True))
wait(lambda: bool(ticks))
assert threads and all(thread != ui_thread for thread in threads)
model.crop.cancelCrop()
wait(lambda: model.canOpen)
model.crop.refresh()
assert model.crop.state == "Cancelled" and model.crop.canCrop and model.localPath == str(request.source)
model.shutdown()
''')
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
def test_same_pair_same_gate_bootstrap_and_viewer_workspace(run_python):
    result = run_python('''
import logging
from pathlib import Path
from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from mediaforge.bootstrap import application
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegRuntime
from mediaforge.infrastructure.resources.paths import qml_root
app = QGuiApplication([])
pair = FfmpegRuntime(Path.cwd() / "ffmpeg.exe", Path.cwd() / "ffprobe.exe")
application.resolve_ffmpeg_runtime = lambda **kw: pair
model = application.build_media_view_model(logging.getLogger("test"))
assert model.crop._use_case._executor._runtime is pair
assert model.trim._use_case._executor._runtime is pair
assert model.crop._use_case._processing_gate is model.trim._use_case._processing_gate
assert not model.crop._use_case._processing_gate.busy
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty("mediaViewModel", model)
warnings = []
engine.warnings.connect(lambda items: warnings.extend(item.toString() for item in items))
window = application.load_shell(engine, qml_root(), logging.getLogger("test"))
assert window.findChild(QObject, "videoViewer") is not None
assert window.findChild(QObject, "viewerVideoOutput") is not None
assert window.findChild(QObject, "windowedTransport") is not None
assert window.findChild(QObject, "metadataInfoButton") is not None
assert window.findChild(QObject, "cropButton") is None
assert window.findChild(QObject, "trimButton") is None
assert window.findChild(QObject, "resizeButton") is None
assert not warnings, warnings
model.shutdown()
window.close()
del engine
''')
    assert result.returncode == 0, result.stderr
