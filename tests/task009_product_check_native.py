"""Small Windows Product Check: one public 4:3 clip, no processing matrix."""
import argparse
import faulthandler
from fractions import Fraction
import hashlib
import json
import logging
from pathlib import Path
import subprocess
from types import SimpleNamespace

import shiboken6
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QGuiApplication, QWheelEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
from mediaforge.infrastructure.resources.paths import qml_root
from native_window_policy import load_native_shell, close_native_windows, resize_native_window
from native_workspace_regressions import find, open_source, wait, activate_native_input
from native_input_clock import NativeInputClock


def run(out):
    out.mkdir(parents=True, exist_ok=True)
    source = out / 'Source 4x3 Кириллица.mp4'
    runtime = resolve_ffmpeg_runtime()
    args = [str(runtime.ffmpeg), '-v', 'error', '-f', 'lavfi', '-i',
            'color=black:s=1024x768:r=25:d=2,drawbox=x=128:y=0:w=768:h=768:color=yellow:t=fill,drawbox=x=400:y=180:w=224:h=360:color=red:t=fill',
            '-f', 'lavfi', '-i', 'sine=frequency=660:duration=2', '-c:v', 'libx264',
            '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(source)]
    generated = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert generated.returncode == 0, generated.stderr
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    evidence = {'fixture_command': args, 'source_sha256': original, 'captures': []}
    app = QGuiApplication([])
    model = build_media_view_model(logging.getLogger('task009-pc-ui'))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('mediaViewModel', model)
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(v.toString() for v in values))
    window = load_native_shell(engine, qml_root(), logging.getLogger('task009-pc-ui'))
    window._native_input_clock = NativeInputClock(window)
    settlement = None
    swaps = [0]
    window.frameSwapped.connect(lambda: swaps.__setitem__(0, swaps[0] + 1))
    def shot(name):
        content = find(window, 'inspectorContent')
        wait(app, lambda: content.opacity() == 1 and content.x() == 0, 'Inspector settled')
        count = swaps[0]; window.update()
        wait(app, lambda: swaps[0] > count, 'fresh scene ' + name)
        image = window.grabWindow()
        assert not image.isNull() and image.save(str(out / (name + '.png')))
        evidence['captures'].append(name + '.png')
        return image
    def rgb(image, nx, ny):
        canvas = find(window, 'videoViewport')
        point = canvas.mapToScene(QPointF(canvas.width()*nx, canvas.height()*ny))
        c = image.pixelColor(round(point.x()*window.devicePixelRatio()), round(point.y()*window.devicePixelRatio()))
        return c.red(), c.green(), c.blue()
    def wheel(steps):
        area = find(window, 'viewerZoomInput')
        point = area.mapToScene(QPointF(area.width()*.7, area.height()*.5))
        event = QWheelEvent(point, point, QPointF().toPoint(), QPointF(0,120*steps).toPoint(),
                            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        window._native_input_clock.stamp(event)
        app.sendEvent(window, event); app.processEvents()
    def click(item):
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint())
    def enter(text):
        item = find(window,'reformatScaleInput'); click(item)
        QTest.keyClick(window,Qt.Key_A,Qt.ControlModifier)
        for char in text: QTest.keyClick(window,Qt.Key(ord(char.upper())))
        QTest.keyClick(window,Qt.Key_Return); app.processEvents()
    try:
        resize_native_window(window,1440,900)
        settlement = open_source(app,window,model,source,rate=Fraction(25))
        p = model.playback
        identities = p._player, p._audio, find(window,'viewerVideoOutput')
        def forbidden(*args, **kwargs): raise AssertionError('Processing spawned by UI edit')
        for bridge in (model.trim,model.crop,model.resize):
            bridge._use_case._executor._processes._factory = forbidden
        model.selectOperation('resize'); model.setOperationEnabled('resize',True)
        shot('scale-default')
        activate_native_input(window)
        enter('0.6')
        assert (model.editState['reformatOutputWidth'],model.editState['reformatOutputHeight']) == (614,460)
        slider = find(window,'reformatScaleSlider'); slider.forceActiveFocus()
        for _ in range(10): QTest.keyClick(window,Qt.Key_Right)
        assert float(model.editState['reformatScale']) == .7 and p.position == 0
        assert find(window,'reformatScaleInput').property('text') == model.editState['reformatScale']
        assert (model.previewPlan['width'],model.previewPlan['height']) == (716,538)
        shot('scale-070')
        model.setReformat('mode','format'); model.setReformat('preset','FHD'); model.setReformat('placement','stretch')
        image = shot('fhd-stretch')
        canvas = find(window,'videoViewport')
        assert abs(canvas.width()/canvas.height()-16/9) < .0001
        assert max(rgb(image,.05,.75)) < 35  # Embedded source bars remain in Stretch alone.
        model.setReformat('preset','Source'); image = shot('source-stretch')
        assert abs(canvas.width()/canvas.height()-4/3) < .0001
        assert find(window,'reformatPresetInput').property('displayText') == 'Source — 1024×768'
        for mode in ('fit','fill'):
            model.setReformat('preset','FHD'); model.setReformat('placement',mode)
            shot('fhd-'+mode)
            video = identities[2]
            assert abs(video.property('sx') / video.property('sy') - 1) < .0001
        assert model.setCropRect(128,0,768,768)
        model.setOperationEnabled('crop',True); model.setReformat('placement','stretch')
        image = shot('crop-stretch')
        sides = [rgb(image,x,.75) for x in (.05,.95)]
        assert all(r>150 and g>150 and b<70 for r,g,b in sides)
        model.setReformat('preset','Source'); shot('source-with-crop')
        assert (model.previewPlan['width'],model.previewPlan['height']) == (1024,768)
        assert (model.previewPlan['inputWidth'],model.previewPlan['inputHeight']) == (768,768)
        model.setReformat('preset','FHD')
        model.selectOperation('crop')
        shot('crop-edit')
        before = model.editState.copy()
        wheel(4)
        area = find(window,'viewerZoomInput')
        center = area.mapToScene(QPointF(area.width()/2,area.height()/2)).toPoint()
        QTest.mousePress(window,Qt.MiddleButton,Qt.NoModifier,center)
        QTest.mouseMove(window,center+QPointF(-25,15).toPoint())
        QTest.mouseRelease(window,Qt.MiddleButton,Qt.NoModifier,center+QPointF(-25,15).toPoint())
        assert model.editState == before
        overlay, canvas = find(window,'cropOverlayRect'),find(window,'videoViewport')
        assert abs(overlay.width()/canvas.width()-.75) < .0001
        center = overlay.mapToScene(QPointF(overlay.width()/2,overlay.height()/2)).toPoint()
        QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,center)
        QTest.mouseMove(window,center+QPointF(14,0).toPoint())
        QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,center+QPointF(14,0).toPoint())
        assert model.editState['cropWidth'] == 768 and model.editState['cropX'] != 128
        assert model.setCropRect(128,0,768,768)
        p.enterFullscreen(); shot('fullscreen-crop-zoom'); p.exitFullscreen()
        wheel(-100)
        assert model.viewerViewport.zoomText == '100.0%' and model.viewerViewport.panX == model.viewerViewport.panY == 0
        model.selectOperation('resize'); model.setReformat('mode','scale')
        for size in ((800,560),(1440,900)):
            resize_native_window(window,*size); shot('layout-'+str(size[0]))
            fit, percent = find(window,'viewerFitButton'),find(window,'viewerZoomReadout')
            assert fit.height() == percent.height() == 32
            assert fit.mapToScene(QPointF()).y() == percent.mapToScene(QPointF()).y()
            assert find(window,'reformatScaleSlider').isVisible()
        p.pointerActivity(); activate_native_input(window)
        revision = settlement.revision; p.stepFrame(1)
        settlement.wait_for('actual adjacent frame',lambda f:p._step_pending is None,after_revision=revision)
        assert p.frameText == 'Frame 2 / 50'
        revealed = []
        model._source_revealer = SimpleNamespace(reveal=lambda path: (revealed.append(path) or SimpleNamespace(succeeded=True,message='')))
        click(find(window,'metadataInfoButton'))
        wait(app,lambda:find(window,'metadataInfoPopup').property('opacity') == 1,
             'Media Info enter transition complete')
        shot('media-info')
        folder = find(window,'revealSourceButton')
        assert folder.width() == folder.height() == 36
        folder.forceActiveFocus(); QTest.keyClick(window,Qt.Key_Space)
        assert len(revealed) == 1 and Path(revealed[0]) == source
        assert (p._player,p._audio,find(window,'viewerVideoOutput')) == identities
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original
        assert not warnings, warnings
        evidence.update(verdict='PASS',screen=window.screen().name(),dpr=window.devicePixelRatio(),
                        crop_stretch_sides=sides,frame_label=p.frameText)
    except BaseException as error:
        evidence['failure'] = repr(error)
        raise
    finally:
        if settlement: settlement.close()
        model.shutdown(); close_native_windows(window); shiboken6.delete(engine)
        (out/'evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True)
    faulthandler.dump_traceback_later(15,repeat=True)
    try: run(parser.parse_args().output.resolve())
    finally: faulthandler.cancel_dump_traceback_later()
