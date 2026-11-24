from pathlib import Path

import pytest


@pytest.mark.integration
def test_bootstrap_loads_visible_window_and_closes_cleanly(run_python, isolated_environment):
    legacy = Path(isolated_environment["LOCALAPPDATA"]) / "MediaForge" / "settings.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy sentinel, not valid JSON")
    result = run_python('''
from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QGuiApplication
from mediaforge.bootstrap import application
calls = []
resolve = application.resolve_ffmpeg_runtime
def counted(**kwargs):
    calls.append(1)
    return resolve(**kwargs)
application.resolve_ffmpeg_runtime = counted
original_load = application.load_shell
def checked_load(engine, source, logger):
    window = original_load(engine, source, logger)
    assert window.isVisible()
    assert window.objectName() == "mediaforgeWindow"
    assert window.findChild(QObject, "openMediaButton") is not None
    assert window.findChild(QObject, "mediaFileDialog") is not None
    QTimer.singleShot(100, window.close)
    return window
application.load_shell = checked_load
exit_code = application.main(["mediaforge"])
assert calls == [1], calls
raise SystemExit(exit_code)
''')
    assert result.returncode == 0, result.stderr
    root = Path(isolated_environment['MEDIAFORGE_DATA_ROOT'])
    assert {p.name for p in root.iterdir()} == {'config','data','cache','logs','models',
                                               'manifests','downloads','sessions','tmp'}
    log = (root / "logs" / "application.log").read_text(encoding="utf-8")
    assert "QML shell loaded" in log
    assert "GUI event loop serviced=" in log
    assert "Application shutdown: 0" in log
    assert "QML:" not in log
    assert legacy.read_bytes() == b"legacy sentinel, not valid JSON"
    assert not Path(isolated_environment["TEMP"]).exists()


@pytest.mark.integration
@pytest.mark.parametrize("content", [None, "import QtQuick\nINVALID {", "import QtQuick\nQtObject {}"])
def test_fatal_qml_is_logged_and_returns_failure(content, tmp_path, run_python, isolated_environment):
    source = tmp_path / "Broken root Ошибка.qml"
    if content is not None:
        source.write_text(content, encoding="utf-8")
    result = run_python(f'''
from pathlib import Path
from mediaforge.bootstrap import application
application.qml_root = lambda: Path({str(source)!r})
raise SystemExit(application.main(["mediaforge"]))
''')
    assert result.returncode == 1, result.stderr
    log = Path(isolated_environment['MEDIAFORGE_DATA_ROOT']) / 'logs/application.log'
    assert "Fatal startup error" in log.read_text(encoding="utf-8")
