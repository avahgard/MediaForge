"""Small real painted Viewer proof for original vs exported non-square pixel media."""
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
from mediaforge.infrastructure.resources.paths import qml_root
from native_window_policy import load_native_shell, close_native_windows
from native_workspace_regressions import find, wait


def run(root, fixtures):
    root.mkdir(parents=True,exist_ok=True)
    app=QGuiApplication([])
    model=build_media_view_model(logging.getLogger('task010-native'))
    engine=QQmlApplicationEngine()
    engine.rootContext().setContextProperty('mediaViewModel',model)
    warnings=[]; engine.warnings.connect(lambda values:warnings.extend(v.toString() for v in values))
    window=load_native_shell(engine,qml_root(),logging.getLogger('task010-native'))
    evidence={'cases':[]}
    p=model.playback
    swaps=[0]; window.frameSwapped.connect(lambda:swaps.__setitem__(0,swaps[0]+1))
    def open_source(path):
        assert model.openMedia(QUrl.fromLocalFile(str(path)))
        wait(app,lambda:model.state=='loaded' and model.canOpen,'open')
        sink=find(window,'viewerVideoOutput').property('videoSink')
        wait(app,lambda:sink.videoFrame().isValid() and sink.videoFrame().startTime()>=0
             and p.framesReliable,'admitted indexed picture',timeout=20)
    def capture(name, aspect):
        app.processEvents(); QTest.qWait(230)
        before=swaps[0]; window.update()
        wait(app,lambda:swaps[0]>before,'paint')
        canvas=find(window,'videoViewport')
        assert abs(canvas.width()/canvas.height()-aspect)<.0001
        image=window.grabWindow(); assert not image.isNull()
        dpr=image.width()/window.width()
        # Actual picture color samples must fill the effective displayed canvas.
        colors=[]
        for x in (.15,.85):
            pos=canvas.mapToScene(QPointF(canvas.width()*x,canvas.height()*.65))
            color=image.pixelColor(round(pos.x()*dpr),round(pos.y()*dpr))
            colors.append([color.red(),color.green(),color.blue()])
        assert colors[0][0]>180 and colors[0][1]>180 and colors[0][2]<80,colors
        assert colors[1][0]>180 and colors[1][1]<80 and colors[1][2]<80,colors
        path=root/(name+'.png'); assert image.save(str(path))
        evidence['cases'].append({'name':name,'canvas':[canvas.width(),canvas.height()],
                                  'plan':model.previewPlan,'colors':colors,'png_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    try:
        source=fixtures/'game 1024 Кириллица.mp4'
        open_source(source)
        player,video=p._player,find(window,'viewerVideoOutput')
        model.selectOperation('output')
        capture('source-square',4/3)
        model.setOperationEnabled('resize',True)
        assert model.setReformat('pixel_aspect','16:9')
        capture('preview-16x9',16/9)
        assert (p._player,find(window,'viewerVideoOutput'))==(player,video)
        model.output.setSetting('folder',str(root.resolve()))
        model.output.setSetting('filename','native exported')
        assert model.output.startExport()
        wait(app,lambda:not model.output.busy,'export',timeout=30)
        model.output.refresh()
        assert model.output.state=='Completed',model.output.message
        output=Path(model.output.outputPath)
        runtime=model.output._use_case._executor._runtime
        probe=subprocess.run([str(runtime.ffprobe),'-v','error','-show_streams','-of','json',str(output)],capture_output=True,text=True,check=True,timeout=15)
        v=json.loads(probe.stdout)['streams'][0]
        assert (v['width'],v['height'],v['sample_aspect_ratio'],v['display_aspect_ratio'])==(1024,768,'4:3','16:9')
        evidence['ffprobe']=v
        evidence['shared_gate']=model.output._use_case._processing_gate is model.trim._use_case._processing_gate is model.crop._use_case._processing_gate is model.resize._use_case._processing_gate
        assert evidence['shared_gate']
        open_source(output)
        capture('encoded-16x9',16/9)
        assert model.editState['resizeEnabled'] is False
        assert not warnings,warnings
        evidence['screen']={'name':window.screen().name(),'dpr':window.devicePixelRatio()}
        evidence['status']='PASS'
        print('TASK-010 native PASS: painted 4:3 source, 16:9 preview and encoded 16:9; shared gate; no QML warnings')
    except BaseException as error:
        evidence['status']='FAILED'; evidence['failure']=repr(error)
        raise
    finally:
        (root/'native-results.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False),encoding='utf-8')
        model.shutdown(); close_native_windows(window); shiboken6.delete(engine)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True); parser.add_argument('--fixtures',type=Path,required=True)
    args=parser.parse_args(); run(args.output.resolve(),args.fixtures.resolve())
