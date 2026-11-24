import pytest


@pytest.mark.integration
def test_home_loaded_info_and_fullscreen_qml_states(run_python):
    result = run_python(r'''
import time
from pathlib import Path
from PySide6.QtCore import QObject, QUrl, Signal, QMetaObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtQml import QQmlApplicationEngine
from mediaforge.application.contracts.media import MediaMetadataDto, OpenMediaResult
from mediaforge.bootstrap.application import load_shell
from mediaforge.infrastructure.resources.paths import qml_root
from mediaforge.presentation.bridge.media import MediaViewModel
from mediaforge.presentation.bridge.playback import PlaybackController
import logging

class Player(QObject):
    playbackStateChanged = Signal(object); mediaStatusChanged = Signal(object)
    positionChanged = Signal(int); durationChanged = Signal(int); errorOccurred = Signal(object, str)
    def setAudioOutput(self, value): self.audio = value
    def stop(self): self.playbackStateChanged.emit(QMediaPlayer.PlaybackState.StoppedState)
    def setSource(self, value): self.source = value
    def setPosition(self, value): self.positionChanged.emit(value)
    def play(self): self.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
    def pause(self): self.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PausedState)
    def setVideoSink(self, value): self.sink = value
    def setVideoOutput(self, value): self.output = value

class Audio(QObject):
    volumeChanged = Signal(float); mutedChanged = Signal(bool)
    def __init__(self): super().__init__(); self.v = 0; self.m = False
    def setVolume(self, value): self.v = value; self.volumeChanged.emit(value)
    def volume(self): return self.v
    def setMuted(self, value): self.m = value; self.mutedChanged.emit(value)
    def isMuted(self): return self.m

class Open:
    def __init__(self): self.calls = []
    def open_media(self, path):
        self.calls.append(path)
        return OpenMediaResult(metadata=MediaMetadataDto(
            "viewer.mp4", path, "mov,mp4", 3.0, "h264", 320, 180, 25.0))
    def cancel(self): pass

app = QGuiApplication([])
use = Open()
playback = PlaybackController(Player(), Audio())
model = MediaViewModel(use, playback_controller=playback)
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty("mediaViewModel", model)
warnings = []
engine.warnings.connect(lambda values: warnings.extend(value.toString() for value in values))
window = load_shell(engine, qml_root(), logging.getLogger("test"))

home = window.findChild(QObject, "emptyMediaState")
loaded = window.findChild(QObject, "loadedMediaState")
transport = window.findChild(QObject, "windowedTransport")
chrome = window.findChild(QObject, "normalWorkspaceChrome")
fullscreen = window.findChild(QObject, "viewerFullscreenLayer")
assert home.property("visible") and not loaded.property("visible")
assert not transport.property("visible")
assert window.findChild(QObject, "openMediaButton") is not None
assert window.findChild(QObject, "trimButton") is None
assert window.findChild(QObject, "cropButton") is None
assert window.findChild(QObject, "resizeButton") is None

source = Path.cwd() / "viewer source.mp4"
source.write_bytes(b"presentation fixture")
assert model.openMedia(QUrl.fromLocalFile(str(source)))
deadline = time.monotonic() + 2
while model.state != "loaded" and time.monotonic() < deadline:
    app.processEvents(); time.sleep(.005)
assert model.state == "loaded" and [Path(value) for value in use.calls] == [source], (model.state, use.calls, model.errorMessage)
app.processEvents()
assert loaded.property("visible") and transport.property("visible"), (loaded.property("visible"), transport.property("visible"))
assert window.findChild(QObject, "compactMediaHeader") is not None
assert window.findChild(QObject, "videoViewer") is not None
video_output = window.findChild(QObject, "viewerVideoOutput")
assert video_output is not None
assert playback._player.sink is playback._staging_sink
assert playback._staging_sink is not video_output.property("videoSink")
assert playback._presentation_sink is video_output.property("videoSink")
assert "fillMode: root.workspace.playback.imageMode ? VideoOutput.Stretch : VideoOutput.PreserveAspectFit" in (qml_root().parent / "UnifiedPreview.qml").read_text(encoding="utf-8")

info = window.findChild(QObject, "metadataInfoPopup")
assert QMetaObject.invokeMethod(info, "open", Qt.ConnectionType.DirectConnection)
app.processEvents()
assert info.property("visible") and [Path(value) for value in use.calls] == [source], (info.property("visible"), use.calls)
assert QMetaObject.invokeMethod(info, "close", Qt.ConnectionType.DirectConnection)

identity = id(playback._player)
playback.enterFullscreen(); app.processEvents()
assert playback.fullscreen and fullscreen.property("visible") and not chrome.property("visible"), (playback.fullscreen, fullscreen.property("visible"), chrome.property("visible"))
assert id(playback._player) == identity
playback.exitFullscreen(); app.processEvents()
assert not fullscreen.property("visible") and chrome.property("visible")
assert id(playback._player) == identity
assert not warnings, warnings
model.shutdown(); window.close(); del engine
''')
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
def test_info_popup_is_bounded_scrollable_and_compact_at_minimum_size(run_python):
    result = run_python(r'''
import logging
import time
from pathlib import Path
from PySide6.QtCore import QObject, QMetaObject, Qt, QUrl, Signal, QPointF
from PySide6.QtQuick import QQuickItem  # Register native item converters for scene bounds.
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtQml import QQmlApplicationEngine
from mediaforge.application.contracts.media import MediaMetadataDto, OpenMediaResult
from mediaforge.bootstrap.application import load_shell
from mediaforge.infrastructure.resources.paths import qml_root
from mediaforge.presentation.bridge.media import MediaViewModel
from mediaforge.presentation.bridge.playback import PlaybackController

class Player(QObject):
    playbackStateChanged = Signal(object); mediaStatusChanged = Signal(object)
    positionChanged = Signal(int); durationChanged = Signal(int); errorOccurred = Signal(object, str)
    def setAudioOutput(self, value): self.audio = value
    def stop(self): self.playbackStateChanged.emit(QMediaPlayer.PlaybackState.StoppedState)
    def setSource(self, value): self.source = value
    def setPosition(self, value): self.positionChanged.emit(value)
    def play(self): self.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
    def pause(self): self.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PausedState)
    def setVideoSink(self, value): self.sink = value
    def setVideoOutput(self, value): self.output = value

class Audio(QObject):
    volumeChanged = Signal(float); mutedChanged = Signal(bool)
    def __init__(self): super().__init__(); self.v = 0; self.m = False
    def setVolume(self, value): self.v = value; self.volumeChanged.emit(value)
    def volume(self): return self.v
    def setMuted(self, value): self.m = value; self.mutedChanged.emit(value)
    def isMuted(self): return self.m

class Open:
    def __init__(self, name, path): self.name = name; self.path = path; self.calls = 0
    def open_media(self, _path):
        self.calls += 1
        return OpenMediaResult(metadata=MediaMetadataDto(
            self.name, self.path, "mov,mp4,m4a,3gp,3g2,mj2", 65.25,
            "h264", 3840, 2160, 59.94))
    def cancel(self): pass

def wait(app, predicate):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate(): return
        time.sleep(.002)
    raise AssertionError("timeout")

def inspect(name, path, expect_scroll):
    backend = Open(name, path)
    playback = PlaybackController(Player(), Audio())
    model = MediaViewModel(backend, playback_controller=playback)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("mediaViewModel", model)
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(value.toString() for value in values))
    window = load_shell(engine, qml_root(), logging.getLogger("info-regression"))
    window.resize(800, 560)
    source = Path.cwd() / ("источник с пробелами.mp4" if expect_scroll else "short.mp4")
    source.write_bytes(b"fixture")
    assert model.openMedia(QUrl.fromLocalFile(str(source)))
    wait(app, lambda: model.state == "loaded")
    popup = window.findChild(QObject, "metadataInfoPopup")
    scroll = window.findChild(QObject, "metadataInfoScroll")
    assert QMetaObject.invokeMethod(popup, "open", Qt.ConnectionType.DirectConnection)
    wait(app, lambda: popup.property("opened"))
    wait(app, lambda: float(scroll.property("contentHeight")) > 0)
    if expect_scroll:
        wait(app, lambda: float(scroll.property("maximumScrollOffset")) > 0)
    background = popup.property("background")
    origin = background.mapToScene(QPointF())
    assert origin.x() >= 0 and origin.y() >= 0, "negative popup scene origin"
    assert origin.x() + background.width() <= window.width() + 1, "horizontal overflow"
    assert origin.y() + background.height() <= window.height() + 1, "vertical overflow"
    fields = window.findChild(QObject, "metadataInfoFields")
    assert fields is not None and fields.property("count") == 12, "missing professional metadata field"
    assert [r['label'] for r in model.mediaInfoRows] == ['File','Path','Container','Video','Codec','Pixel format','Resolution','Frame rate','Frames','Duration','Audio','None']
    maximum = float(scroll.property("maximumScrollOffset"))
    if expect_scroll:
        assert maximum > 0, (
            maximum, popup.property("height"), popup.property("maximumHeight"),
            scroll.property("contentHeight"), scroll.property("availableHeight"),
            len(model.fileName), len(model.localPath), window.width(), window.height())
        assert QMetaObject.invokeMethod(scroll, "scrollToEnd", Qt.ConnectionType.DirectConnection), "scroll invoke failed"
        app.processEvents()
        assert abs(float(scroll.property("scrollOffset")) - maximum) < 1.0, (
            scroll.property("scrollOffset"), maximum, scroll.property("contentHeight"),
            scroll.property("height"), scroll.property("availableHeight"))
        assert float(scroll.property("scrollOffset")) + float(scroll.property("availableHeight")) >= (
            float(scroll.property("contentHeight")) - 1)
    else:
        assert maximum <= 0.5, maximum
        assert popup.property("height") < popup.property("maximumHeight")
    assert backend.calls == 1
    assert playback.state == "Ready"
    assert not warnings, warnings
    model.shutdown(); window.close(); del engine

app = QGuiApplication([])
long_name = ("ОченьДлинноеИмяМедиаФайла" * 6) + ".mp4"
long_path = str(Path.cwd() / ("Папка с пробелами " * 8) / long_name)
inspect(long_name, long_path, True)
inspect("clip.mp4", "C:/Media/clip.mp4", True)
''')
    assert result.returncode == 0, result.stderr
