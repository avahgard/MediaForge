"""Small native final-UX check using a retained public fixture read-only."""
import argparse
import faulthandler
from fractions import Fraction
import hashlib
import json
import logging
from pathlib import Path

import shiboken6
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.resources.paths import qml_root
from native_window_policy import load_native_shell, resize_native_window, close_native_windows
from native_input_clock import NativeInputClock
from native_workspace_regressions import find, open_source, wait, activate_native_input


def run(source, out):
    out.mkdir(parents=True,exist_ok=False)
    original=hashlib.sha256(source.read_bytes()).hexdigest()
    evidence={'source':str(source),'source_sha256':original,'captures':[],'handles':[]}
    app=QGuiApplication([]); model=build_media_view_model(logging.getLogger('final-ux'))
    engine=QQmlApplicationEngine(); engine.rootContext().setContextProperty('mediaViewModel',model)
    warnings=[]; engine.warnings.connect(lambda values:warnings.extend(v.toString() for v in values))
    window=load_native_shell(engine,qml_root(),logging.getLogger('final-ux'))
    window._native_input_clock=NativeInputClock(window)
    settlement=None; swaps=[0]
    window.frameSwapped.connect(lambda:swaps.__setitem__(0,swaps[0]+1))
    modes=('left-top','right-top','left-bottom','right-bottom','left','right','top','bottom')
    anchors={'left-top':(0,0),'right-top':(1,0),'left-bottom':(0,1),'right-bottom':(1,1),
             'left':(0,.5),'right':(1,.5),'top':(.5,0),'bottom':(.5,1)}
    def bounds(obj):return QRectF(obj.mapToScene(QPointF()),obj.size())
    def shot(name):
        content=find(window,'inspectorContent')
        wait(app,lambda:content.opacity()==1 and content.x()==0,'settled Inspector')
        before=swaps[0];window.update();wait(app,lambda:swaps[0]>before,'fresh paint')
        image=window.grabWindow();assert not image.isNull() and image.save(str(out/(name+'.png')))
        evidence['captures'].append(name+'.png');return image
    def handles(name):
        image=shot(name);area=bounds(find(window,'cropHandleLayer'));crop=bounds(find(window,'cropOverlayRect'))
        for mode in modes:
            target=find(window,'cropHandle-'+mode);box=bounds(target)
            circle=bounds(find(window,'cropHandleCircle-'+mode))
            assert target.isVisible() and area.adjusted(-.01,-.01,.01,.01).contains(circle),(mode,circle,area)
            ax,ay=anchors[mode];anchor=QPointF(crop.x()+ax*crop.width(),crop.y()+ay*crop.height())
            assert (circle.center()-anchor).manhattanLength()<.001,(name,mode,circle.center(),anchor)
            for other in modes:
                if other!=mode:assert not box.intersects(bounds(find(window,'cropHandle-'+other))),(name,mode,other)
            p=circle.center();dpr=window.devicePixelRatio();c=image.pixelColor(round(p.x()*dpr),round(p.y()*dpr))
            assert 130<c.red()<205 and 40<c.green()<140 and c.blue()>210,(name,mode,c.getRgb())
            # The circle's four cardinal pixels must also survive clipping/overlays.
            for dx,dy in ((-3,0),(3,0),(0,-3),(0,3)):
                edge=image.pixelColor(round((p.x()+dx)*dpr),round((p.y()+dy)*dpr))
                assert 130<edge.red()<205 and 40<edge.green()<140 and edge.blue()>210,(name,mode,dx,dy,edge.getRgb())
            evidence['handles'].append({'case':name,'mode':mode,'bounds':[box.x(),box.y(),box.width(),box.height()],
                                        'anchor':[anchor.x(),anchor.y()],'center':[p.x(),p.y()],'rgba':c.getRgb()})
    def click(item,button=Qt.LeftButton):
        QTest.mouseClick(window,button,Qt.NoModifier,item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint())
    try:
        resize_native_window(window,1440,900)
        settlement=open_source(app,window,model,source,rate=Fraction(25));p=model.playback
        identities=p._player,p._audio,find(window,'viewerVideoOutput')
        def forbidden(*args,**kwargs):raise AssertionError('Processing spawned by UX edit')
        for bridge in (model.trim,model.crop,model.resize):bridge._use_case._executor._processes._factory=forbidden
        model.selectOperation('crop');model.setOperationEnabled('crop',True)
        for w,h in ((800,560),(1440,900)):
            resize_native_window(window,w,h);model.viewerViewport.fit()
            for crop in ((0,0,1024,768),(128,84,768,600)):
                assert model.setCropRect(*crop);handles(f'handles-{w}-{crop[0]}')
            for mode in modes:
                assert model.setCropRect(0,0,1024,768);app.processEvents()
                target=find(window,'cropHandle-'+mode);start=target.mapToScene(QPointF(12,12)).toPoint()
                dx=-10 if 'right' in mode else 10 if 'left' in mode else 0
                dy=-8 if 'bottom' in mode else 8 if 'top' in mode else 0
                QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
                QTest.mouseMove(window,start+QPointF(dx,dy).toPoint())
                QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,start+QPointF(dx,dy).toPoint())
                s=model.editState;assert (s['cropWidth'],s['cropHeight'])!=(1024,768),(w,mode,s)
                assert (s['cropWidth']<1024)==(dx!=0),(w,mode,s)
                assert (s['cropHeight']<768)==(dy!=0),(w,mode,s)
                assert all(s[k]%2==0 for k in ('cropX','cropY','cropWidth','cropHeight'))
        assert model.setCropRect(256,192,512,384)
        overlay=find(window,'cropOverlay')
        model.viewerViewport.wheel(overlay.width()/2,overlay.height()/2,2);model.viewerViewport.pan(-20,15)
        handles('zoom-pan')
        p.enterFullscreen();handles('fullscreen-handles')
        model.viewerViewport.fit();assert model.setCropRect(0,0,1024,768);handles('fullscreen-full-source')
        p.exitFullscreen();model.viewerViewport.fit()
        model.setOperationEnabled('trim',True);assert model.setTrimRange(100,1500)
        model.setOperationEnabled('resize',True);assert model.setReformat('mode','format')
        assert model.setReformat('preset','Source') and model.setReformat('placement','fill')
        model.setCropPreview(True);before=model.editState.copy()
        click(find(window,'resetCropButton'));s=model.editState
        assert model.selectedOperation=='crop' and not s['cropEnabled'] and not s['cropPreview']
        assert (s['cropX'],s['cropY'],s['cropWidth'],s['cropHeight'])==(0,0,1024,768)
        for k in ('trimIn','trimOut','trimEnabled','resizeEnabled','reformatPreset','reformatPlacement'):assert s[k]==before[k]
        model.setOperationEnabled('crop',True);assert model.setCropRect(128,84,768,600)
        model.selectOperation('resize');assert model.setReformat('mode','scale') and model.setReformat('scale','.7')
        shot('scale-before-reset');click(find(window,'reformatScaleSlider'),Qt.RightButton)
        assert model.editState['reformatScale']=='1.0' and find(window,'reformatScaleInput').property('text')=='1.0'
        assert (model.editState['reformatOutputWidth'],model.editState['reformatOutputHeight'])==(768,600)
        activate_native_input(window);find(window,'reformatScaleSlider').forceActiveFocus()
        QTest.keyClick(window,Qt.Key_Right);assert model.editState['reformatScale']=='1.01'
        slider=find(window,'reformatScaleSlider')
        start=slider.mapToScene(QPointF(slider.property('leftPadding')+slider.property('visualPosition')*(slider.property('availableWidth')-14)+7,slider.height()/2)).toPoint()
        QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
        QTest.mouseMove(window,start+QPointF(25,0).toPoint())
        QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,start+QPointF(25,0).toPoint())
        assert float(model.editState['reformatScale'])>1.01
        shot('scale-reset-keyboard')
        assert (p._player,p._audio,find(window,'viewerVideoOutput'))==identities
        assert hashlib.sha256(source.read_bytes()).hexdigest()==original and not warnings,warnings
        evidence.update(verdict='PASS',screen=window.screen().name(),dpr=window.devicePixelRatio())
    except BaseException as error:
        evidence['failure']=repr(error);raise
    finally:
        if settlement:settlement.close()
        model.shutdown();close_native_windows(window);shiboken6.delete(engine)
        (out/'evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();faulthandler.dump_traceback_later(15,repeat=True)
    try:run(args.source.resolve(),args.output.resolve())
    finally:faulthandler.cancel_dump_traceback_later()
