"""Rendered shared welcome; no native desktop/decoder acceptance is implied."""
from pathlib import Path
import pytest

pytestmark=pytest.mark.integration


@pytest.mark.parametrize('width,height',[(1440,900),(1024,768),(800,560)])
def test_empty_tabs_share_one_approved_home_and_hide_all_dub_setup(run_python,width,height):
    result=run_python(f'width={width};height={height}\n'+r'''
import logging,hashlib
from pathlib import Path
from PySide6.QtCore import QUrl,QObject,QPointF
from PySide6.QtGui import QGuiApplication,QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from mediaforge.bootstrap.application import build_media_view_model,build_dub_view_models
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.resources.paths import qml_root
from mediaforge.presentation.bridge.navigation import Navigation
app=QGuiApplication([])
if not QFontDatabase.families():
    # The restricted offscreen process cannot enumerate the desktop font store.
    # Load actual installed font bytes for this renderer only, without copying
    # fonts or changing application/global configuration.
    for filename in ('segoeui.ttf','seguisb.ttf'):
        assert QFontDatabase.addApplicationFontFromData((Path('C:/Windows/Fonts')/filename).read_bytes())>=0
paths=ProfilePaths.from_environment();paths.activate_temporary()
gate=MediaProcessingGate();logger=logging.getLogger('shared-home')
media=build_media_view_model(logger,profile=paths,gate=gate)
dub,models=build_dub_view_models(paths,gate,logger);nav=Navigation(media,dub,models)
engine=QQmlApplicationEngine();warnings=[]
engine.warnings.connect(lambda rows:warnings.extend(r.toString() for r in rows))
for name,value in dict(mediaViewModel=media,dubViewModel=dub,modelsViewModel=models,appNavigation=nav).items():
    engine.rootContext().setContextProperty(name,value)
engine.load(QUrl.fromLocalFile(str(qml_root())));assert engine.rootObjects(),warnings
window=engine.rootObjects()[0];window.setWidth(width);window.setHeight(height)
try:
    QTest.qWait(120)
    home=window.findChild(QObject,'emptyMediaState');assert home and home.property('visible')
    assert not media.hasSelectedSource
    plus=window.findChild(QObject,'homePlusIcon');assert plus and plus.property('width')==84 and plus.property('height')==84
    button=window.findChild(QObject,'openMediaButton');assert button.property('text')=='Choose file'
    assert button.property('primary') and button.property('enabled')
    panel=window.findChild(QObject,'homeOpenMediaSurface');assert panel.property('radius')==30
    assert window.findChild(QObject,'dubPrimary') is None
    names=[o.property('text') for o in home.findChildren(QObject) if o.property('text')]
    assert 'Drop media here' in names and 'or click to browse' in names
    if width==1440 and height==900:
        # Independent SVG control points, including text baselines. This catches
        # a superficially similar replacement panel or a displaced page header.
        assert panel.mapToScene(QPointF(0,0))==QPointF(242,120)
        assert (panel.width(),panel.height())==(1170,520)
        assert plus.mapToScene(QPointF(42,42))==QPointF(827,270)
        assert button.mapToScene(QPointF(0,0))==QPointF(752,414)
        assert (button.width(),button.height())==(150,46)
        expected={'MediaForge':56,'Fast media processing without project overhead.':82,
                  'Drop media here':354,'or click to browse':384}
        for label in home.findChildren(QObject):
            if label.property('text') in expected:
                assert label.mapToScene(QPointF(0,label.property('baselineOffset'))).y()==expected[label.property('text')]
    first=window.grabWindow();assert not first.isNull();assert first.save(str(Path.cwd()/'media-home.png'))
    assert nav.navigate('Dub');QTest.qWait(120)
    assert window.findChild(QObject,'emptyMediaState') is home and home.property('visible')
    for name in ('dubPage','dubPrimary','dubVoice','dubGeneratePreview','dubOutputMode','dubChooseFolder'):
        assert window.findChild(QObject,name) is None,name
    second=window.grabWindow();assert not second.isNull();assert second.save(str(Path.cwd()/'dub-home.png'))
    # The same rendered home region remains byte-identical. Navigation selection
    # is outside this crop and is intentionally different.
    point=home.mapToScene(QPointF(0,0));x,y=round(point.x()),round(point.y())
    dpr=window.devicePixelRatio()
    crop=(round(x*dpr),round(y*dpr),round(home.width()*dpr),round(home.height()*dpr))
    assert first.copy(*crop)==second.copy(*crop)
    assert nav.navigate('Media');QTest.qWait(40)
    assert window.findChild(QObject,'emptyMediaState') is home and not gate.busy
    assert not warnings,warnings
finally:
    models.shutdown();dub.shutdown();media.shutdown();window.close();app.processEvents()
''')
    assert result.returncode==0,result.stdout+result.stderr
