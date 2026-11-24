"""Bounded 90/270 display-matrix Viewer regression, using the persistent renderer."""
import argparse
import hashlib
import json
import logging
from pathlib import Path
import subprocess

import shiboken6
from PySide6.QtCore import QPointF, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
from mediaforge.infrastructure.resources.paths import qml_root
from native_window_policy import load_native_shell, close_native_windows
from native_workspace_regressions import find, wait


def run(root):
    root.mkdir(parents=True,exist_ok=True)
    runtime=resolve_ffmpeg_runtime()
    evidence={'cases':[],'commands':[]}
    def cli(args):
        p=subprocess.run([str(a) for a in args],capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
        evidence['commands'].append({'args':[str(a) for a in args],'exit':p.returncode})
        p.check_returncode();return p.stdout
    source=root/'1024 square pixels.mp4'
    cli([runtime.ffmpeg,'-v','error','-n','-f','lavfi','-i',
         'color=yellow:s=1024x768:r=25:d=0.6,drawbox=x=512:y=0:w=512:h=768:color=red:t=fill',
         '-c:v','libx264','-pix_fmt','yuv420p',source])
    for angle in (90,270):
        cli([runtime.ffmpeg,'-v','error','-n','-display_rotation:v:0',angle,'-i',source,'-c','copy',root/f'rotation {angle}.mp4'])
    app=QGuiApplication([])
    model=build_media_view_model(logging.getLogger('export-orientation'))
    engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('mediaViewModel',model)
    warnings=[];engine.warnings.connect(lambda values:warnings.extend(v.toString() for v in values))
    window=load_native_shell(engine,qml_root(),logging.getLogger('export-orientation'))
    swaps=[0];window.frameSwapped.connect(lambda:swaps.__setitem__(0,swaps[0]+1))
    processing=[]
    for bridge in (model.trim,model.crop,model.resize,model.output):
        processes=bridge._use_case._executor._processes
        original=processes._factory
        def observed(args, _original=original, **kw):
            processing.append(args);return _original(args,**kw)
        processes._factory=observed
    def painted(angle,label,aspect):
        QTest.qWait(240);before=swaps[0];window.update()
        wait(app,lambda:swaps[0]>before,'paint')
        canvas=find(window,'videoViewport');video=find(window,'viewerVideoOutput')
        actual=canvas.width()/canvas.height()
        image=window.grabWindow();assert not image.isNull()
        name=f'{angle}-{label}.png';path=root/name;assert image.save(str(path))
        row={'angle':angle,'label':label,'canvas':[canvas.width(),canvas.height()],'aspect':actual,
             'native_content_rect':[video.property('contentRect').width(),video.property('contentRect').height()],
             'plan':model.previewPlan,'png_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        evidence['cases'].append(row)
        assert abs(actual-aspect)<.0001,row
        if label=='original':
            colors=[];dpr=image.width()/window.width()
            for y in (.25,.75):
                point=canvas.mapToScene(QPointF(canvas.width()*.65,canvas.height()*y))
                c=image.pixelColor(round(point.x()*dpr),round(point.y()*dpr));colors.append([c.red(),c.green(),c.blue()])
            row['colors']=colors
            assert all(c[0]>180 and c[2]<80 for c in colors)
            assert sorted(c[1]>180 for c in colors)==[False,True]
        return canvas
    try:
        for angle in (90,270):
            path=root/f'rotation {angle}.mp4'
            meta=json.loads(cli([runtime.ffprobe,'-v','error','-show_streams','-of','json',path]))
            assert abs(meta['streams'][0]['side_data_list'][0]['rotation'])==90
            assert model.openMedia(QUrl.fromLocalFile(str(path)))
            wait(app,lambda:model.state=='loaded' and model.canOpen,'source')
            sink=find(window,'viewerVideoOutput').property('videoSink')
            wait(app,lambda:sink.videoFrame().isValid() and model.playback.framesReliable,'picture',timeout=20)
            model.selectOperation('output')
            player,video=model.playback._player,find(window,'viewerVideoOutput')
            evidence.setdefault('source_metadata',[]).append(meta)
            painted(angle,'original',3/4)
            model.setOperationEnabled('resize',True);assert model.setReformat('pixel_aspect','16:9')
            model.setOperationEnabled('crop',True);model.selectOperation('crop')
            canvas=painted(angle,'crop-edit',3/4)
            rect=find(window,'cropOverlayRect')
            assert abs(rect.width()-canvas.width())<.001 and abs(rect.height()-canvas.height())<.001
            values=model.editState.copy()
            model.viewerViewport.wheel(canvas.width()*.5,canvas.height()*.5,3)
            model.viewerViewport.pan(-20,10)
            painted(angle,'zoom-pan',3/4)
            assert model.editState==values
            model.viewerViewport.fit();model.playback.enterFullscreen()
            painted(angle,'fullscreen',3/4)
            model.playback.exitFullscreen();QTest.qWait(200)
            model.setCropPreview(True)
            painted(angle,'downstream',16/9)
            assert (model.playback._player,find(window,'viewerVideoOutput'))==(player,video)
            assert model.editState==dict(values,cropPreview=True)
        assert not processing,processing  # Preview never starts any processing adapter.
        assert not warnings,warnings
        evidence.update(status='PASS',processing_calls=processing,
                        screen={'name':window.screen().name(),'dpr':window.devicePixelRatio()})
        print('90/270 native PASS: original/Crop Edit 3:4; zoom/pan/fullscreen; downstream SAR; persistent renderer; no processing')
    except BaseException as error:
        evidence.update(status='FAILED',failure=repr(error));raise
    finally:
        (root/'orientation-proof.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False),encoding='utf-8')
        model.shutdown();close_native_windows(window);shiboken6.delete(engine)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output.resolve())
