from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_complete_shell_missing_models_navigation_is_lazy(run_python):
    result=run_python('''
import logging
from PySide6.QtCore import QUrl,QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from mediaforge.bootstrap.application import build_media_view_model,build_dub_view_models
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.resources.paths import qml_root
from mediaforge.presentation.bridge.navigation import Navigation
from mediaforge.presentation.bridge.settings import SettingsViewModel
from mediaforge.infrastructure.settings.preferences import ThemePreference
from mediaforge.infrastructure.settings.build_identity import BuildIdentity
app=QGuiApplication([])
paths=ProfilePaths.from_environment();paths.activate_temporary()
gate=MediaProcessingGate();logger=logging.getLogger('test')
media=build_media_view_model(logger,profile=paths,gate=gate)
dub,models=build_dub_view_models(paths,gate,logger)
nav=Navigation(media,dub,models)
settings=SettingsViewModel(paths,ThemePreference(paths),BuildIdentity(),'0.1.0')
assert not gate.busy and not models.allInstalled and not dub.canStart
assert list(paths.downloads.iterdir())==[] and list(paths.models.iterdir())==[]
engine=QQmlApplicationEngine();warnings=[]
engine.warnings.connect(lambda rows:warnings.extend(r.toString() for r in rows))
for key,value in dict(mediaViewModel=media,dubViewModel=dub,modelsViewModel=models,appNavigation=nav,
                      settingsViewModel=settings).items():
    engine.rootContext().setContextProperty(key,value)
engine.load(QUrl.fromLocalFile(str(qml_root())))
assert engine.rootObjects(),warnings
window=engine.rootObjects()[0]
assert nav.navigate('Dub');app.processEvents()
assert window.findChild(QObject,'dubPage') is None
assert window.findChild(QObject,'emptyMediaState').property('visible')
assert not media.canOpen and not dub.generatePreview() and not dub.start()
assert nav.navigate('Settings');app.processEvents()
assert window.findChild(QObject,'modelsPage') is not None
assert nav.navigate('Media');app.processEvents()
assert media.canOpen and not gate.busy
assert list(paths.downloads.iterdir())==[] and list(paths.models.iterdir())==[]
assert warnings==[],warnings
models.shutdown();dub.shutdown();media.shutdown();window.close();app.processEvents()
''')
    assert result.returncode==0,result.stdout+result.stderr
