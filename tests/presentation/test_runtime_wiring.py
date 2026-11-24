import pytest


@pytest.mark.integration
@pytest.mark.parametrize("state", ["missing", "corrupt", "manifest-invalid"])
def test_shell_starts_and_metadata_fails_controlled_without_download(run_python, state):
    result = run_python(f'''
import os
from pathlib import Path
import urllib.request
from PySide6.QtCore import QTimer, QUrl
from mediaforge.bootstrap import application
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
os.environ.pop("MEDIAFORGE_FFPROBE_PATH", None)
def forbidden(*args, **kwargs):
    raise AssertionError("Startup must never download")
urllib.request.urlopen = forbidden
state = {state!r}
if state == "corrupt":
    manifest = FfmpegManifest.load()
    root = manifest.location(ProfilePaths.from_environment())
    for name in (manifest.ffmpeg_path, manifest.ffprobe_path):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not an executable")
elif state == "manifest-invalid":
    import mediaforge.infrastructure.ffmpeg.runtime as runtime
    runtime.manifest_path = lambda: Path.cwd() / "missing-manifest.json"
source = Path.cwd() / "source.mp4"
source.write_bytes(b"source")
original_load = application.load_shell
checks = []
def checked_load(engine, source_qml, logger):
    window = original_load(engine, source_qml, logger)
    model = engine.rootContext().contextProperty("mediaViewModel")
    assert window.isVisible()
    assert model.openMedia(QUrl.fromLocalFile(str(source)))
    def check_result():
        if model.state == "loading":
            QTimer.singleShot(10, check_result)
            return
        checks.append(model.state == "error" and "FFprobe" in model.errorMessage)
        window.close()
    QTimer.singleShot(10, check_result)
    return window
application.load_shell = checked_load
assert application.main(["mediaforge"]) == 0
assert checks == [True]
''')
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
def test_composition_root_passes_validated_path_to_metadata_adapter(run_python):
    result = run_python('''
import logging
from pathlib import Path
from PySide6.QtCore import QCoreApplication
from mediaforge.bootstrap import application
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegRuntime
app = QCoreApplication([])
expected = Path.cwd() / "validated/ffprobe.exe"
application.resolve_ffmpeg_runtime = lambda **kw: FfmpegRuntime(expected.with_name("ffmpeg.exe"), expected)
received = []
real_probe = application.FfprobeMediaMetadataProbe
def probe(path, **kwargs):
    received.append(path)
    return real_probe(path, **kwargs)
application.FfprobeMediaMetadataProbe = probe
model = application.build_media_view_model(logging.getLogger("test"))
assert received == [expected]
runtime = model.resize._use_case._executor._runtime
assert runtime.ffmpeg == expected.with_name("ffmpeg.exe") and runtime.ffprobe == expected
assert model.trim._use_case._executor._runtime is runtime
assert model.crop._use_case._executor._runtime is runtime
assert model.trim._use_case._processing_gate is model.crop._use_case._processing_gate
assert model.crop._use_case._processing_gate is model.resize._use_case._processing_gate
assert type(model.playback._player).__name__ == "QMediaPlayer"
assert type(model.playback._audio).__name__ == "QAudioOutput"
assert model.playback._player.audioOutput() is model.playback._audio
model.shutdown()
''')
    assert result.returncode == 0, result.stderr
