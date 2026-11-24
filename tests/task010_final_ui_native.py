"""Bounded final Inspector paint/input check; no processing is allowed."""
import argparse,hashlib,json,logging
from pathlib import Path
import shiboken6
from PySide6.QtCore import QPoint,QPointF,QUrl,Qt
from PySide6.QtGui import QGuiApplication,QWheelEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.resources.paths import qml_root
from native_window_policy import load_native_shell,resize_native_window,close_native_windows
from native_workspace_regressions import find,wait


def run(root,source):
    root.mkdir(parents=True,exist_ok=False);original=hashlib.sha256(source.read_bytes()).hexdigest()
    app=QGuiApplication([]);model=build_media_view_model(logging.getLogger('final-ui'))
    engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('mediaViewModel',model)
    warnings=[];engine.warnings.connect(lambda errors:warnings.extend(e.toString() for e in errors))
    window=load_native_shell(engine,qml_root(),logging.getLogger('final-ui'));rows=[];children=[]
    for bridge in (model.trim,model.crop,model.resize,model.output):
        def forbidden(args,**_):children.append(args);raise AssertionError('Preview launched processing')
        bridge._use_case._executor._processes._factory=forbidden
    def wheel(field,delta):
        point=field.mapToScene(QPointF(field.width()/2,field.height()/2));local=QPoint(round(point.x()),round(point.y()))
        QTest.mouseMove(window,local);QTest.qWait(20)
        event=QWheelEvent(point,QPointF(window.mapToGlobal(local)),QPoint(),QPoint(0,delta),Qt.NoButton,Qt.NoModifier,Qt.ScrollUpdate,False)
        app.sendEvent(window,event);QTest.qWait(100)
    try:
        assert model.openMedia(QUrl.fromLocalFile(str(source)))
        wait(app,lambda:model.state=='loaded' and model.canOpen,'media')
        wait(app,lambda:model.playback.framesReliable,'native admitted source frame',timeout=20)
        player=model.playback._player;renderer=find(window,'viewerVideoOutput')
        for size in ((800,560),(1280,800)):
            resize_native_window(window,*size)
            for operation in ('trim','crop','resize','output'):
                model.selectOperation(operation)
                if operation!='output':model.setOperationEnabled(operation,True)
                QTest.qWait(240)
                scroll=find(window,'inspectorScroll');view=scroll.property('contentItem');bar=find(window,'inspectorVerticalBar');content=find(window,'inspectorContent')
                window.update()
                wait(app,lambda:content.opacity()>.999,'Inspector reveal paint')
                assert isinstance(view,QQuickItem)
                edge=content.mapToScene(QPointF(content.width(),0)).x();bar_x=bar.mapToScene(QPointF()).x()
                assert edge+5<=bar_x and view.property('contentWidth')<=view.width()+.1, (size,operation,edge,bar_x,content.width(),view.width(),view.property('contentWidth'),bar.width(),bar.property('implicitWidth'),scroll.property('rightPadding'))
                names={'trim':['trimInInput','trimOutInput'], 'crop':['cropXInput','cropYInput','cropWInput','cropHInput'],
                       'resize':['reformatScaleInput','pixelAspectInput','pixelAspect169','pixelAspectSource'],
                       'output':['exportContainer','exportCodec','exportQualityPreset','exportSpeed','exportFolder','exportFilename','exportStart']}[operation]
                for name in names:
                    control=find(window,name)
                    if control.isVisible():
                        assert control.mapToScene(QPointF(control.width(),0)).x()<=edge+.1,(size,name,control.width(),edge)
                image=window.grabWindow();assert not image.isNull();name=f'{size[0]}-{operation}.png'
                assert image.save(str(root/name))
                rows.append(dict(size=size,operation=operation,content_right=edge,scrollbar_left=bar_x,
                                 dpr=window.devicePixelRatio(),png=name))
            model.selectOperation('crop');QTest.qWait(240)
            field=find(window,'cropWInput');text=field.property('text');find(window,'workspaceKeyboardFocus').forceActiveFocus()
            before=view.property('contentY');wheel(field,-120)
            if view.property('contentHeight')>view.height():
                assert view.property('contentY')>before,(size,before,view.property('contentY'))
            else:
                assert view.property('contentY')==before  # Entire Inspector fits; scrolling has no work.
            assert field.property('text')==text
            model.selectOperation('resize');assert model.setReformat('mode','format');QTest.qWait(240)
            assert model.setReformat('preset','Custom')
            image=window.grabWindow();assert image.save(str(root/f'{size[0]}-format.png'))
            model.setReformat('mode','scale')
        assert not warnings,warnings
        assert not children
        assert model.playback._player is player and find(window,'viewerVideoOutput') is renderer
        assert hashlib.sha256(source.read_bytes()).hexdigest()==original
        (root/'ui-proof.json').write_text(json.dumps(dict(status='PASS',cases=rows,source_sha256=original,
            processing_calls=len(children),screen=window.screen().name(),warnings=warnings),indent=2),encoding='utf-8')
        print('PASS: eight native Inspector paints + Format, real wheel scrolling, converter, zero processing.')
    finally:
        model.shutdown();close_native_windows(window);shiboken6.delete(engine)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--source',type=Path,required=True)
    args=parser.parse_args();run(args.output.resolve(),args.source.resolve())
