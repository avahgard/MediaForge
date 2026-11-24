"""PC3 native Windows Transport, source facts, Explorer and audio ownership."""
import argparse,json,logging,shutil
from fractions import Fraction
from pathlib import Path
from PySide6.QtCore import QObject,QPoint,QPointF,Qt,QMetaObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput,QMediaDevices,QAudioDevice
from PySide6.QtTest import QTest
import shiboken6
from native_window_policy import (load_native_shell as load_shell, resize_native_window, close_native_windows)
from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.resources.paths import qml_root
from native_workspace_regressions import wait,find,open_source,activate_native_input
from native_phase_diagnostics import NativePhaseDiagnostics
from native_explorer_selection import ExplorerSelectionObserver


def run(fixtures,output):
    output.mkdir(parents=True,exist_ok=True)
    diagnostics=NativePhaseDiagnostics(output/'diagnostics')
    app=QGuiApplication([]);engine=QQmlApplicationEngine();model=build_media_view_model(logging.getLogger('pc3'))
    engine.rootContext().setContextProperty('mediaViewModel',model);warnings=[]
    engine.warnings.connect(lambda values:warnings.extend(v.toString() for v in values))
    window=load_shell(engine,qml_root(),logging.getLogger('pc3'))
    p=model.playback;diagnostics.bind(app=app,window=window,model=model,playback=p,service=p._timeline_service)
    evidence={};settlement=None
    def point(item):
        a=item.mapToScene(QPointF(item.width()/2,item.height()/2));return QPoint(round(a.x()),round(a.y()))
    def capture(name):
        if 'fullscreen' in name:
            QTest.mouseMove(window,QPoint(window.width()//2,window.height()-60))
            p.setControlsZone(True)
        QTest.qWait(220);assert window.grabWindow().save(str(output/name))
    try:
        resize_native_window(window, 1440,900)
        activate_native_input(window)
        with diagnostics.phase_scope('open exact source'):
            settlement=open_source(app,window,model,fixtures/'integral.mp4',rate=Fraction(25))
            diagnostics.bind(observer=settlement)
            p.seek(1220);settlement.wait_for('arbitrary frame',lambda f:f.startTime()==1200000)
        with diagnostics.phase_scope('shared fullscreen Trim'):
            model.setOperationEnabled('trim',True);assert model.setTrimRange(400,3000)
            identities=(p._player,p._audio,find(window,'viewerVideoOutput'),p._timeline)
            label=p.frameText;assert label=='Frame 31 / 100'
            window.enterViewerFullscreen();wait(app,lambda:p.fullscreen,'fullscreen');QTest.qWait(250)
            full=find(window,'fullscreenTimeline');assert find(window,'fullscreenFrameReliabilityText').property('text')==label
            assert full.property('handleIn')/full.width()==0.1 and full.property('handleOut')/full.width()==0.75
            for name,fraction in [('fullscreenTrimInHandle',0.2),('fullscreenTrimOutHandle',0.85)]:
                p.setControlsZone(True);QTest.qWait(200)
                start=point(find(window,name));target=full.mapToScene(QPointF(full.width()*fraction,15))
                end=QPoint(round(target.x()),start.y()-180)
                QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
                assert p._controls_interactions==1
                QTest.mouseMove(window,end);QTest.qWait(p._controls_idle_ms+100)
                assert p.controlsVisible and p._controls_interactions==1
                QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,end);assert p._controls_interactions==0
                key='trimIn' if 'In' in name else 'trimOut';assert abs(model.editState[key]-fraction*4000)<=10
            saved=(model.editState['trimIn'],model.editState['trimOut'])
            window.exitViewerFullscreen();wait(app,lambda:not p.fullscreen,'return')
            normal=find(window,'mediaTimeline');assert abs(normal.property('handleIn')/normal.width()-saved[0]/4000)<.001
            # Normal handle edit is immediately the same fullscreen state.
            start=point(find(window,'trimInHandle'));end=QPoint(start.x()-round(normal.width()*.05),start.y())
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start);QTest.mouseMove(window,end);QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,end)
            saved=(model.editState['trimIn'],model.editState['trimOut'])
            window.enterViewerFullscreen();wait(app,lambda:p.fullscreen,'return fullscreen');QTest.qWait(200)
            assert abs(full.property('handleIn')/full.width()-saved[0]/4000)<.001
            model.setOperationEnabled('trim',False)
            for name in ('fullscreenTrimInHandle','fullscreenTrimOutHandle'):
                a=point(find(window,name));b=QPoint(a.x()+40,a.y())
                QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,a);QTest.mouseMove(window,b);QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,b)
            find(window,'workspaceKeyboardFocus').forceActiveFocus()
            for key,mod in ((Qt.Key_I,Qt.NoModifier),(Qt.Key_O,Qt.NoModifier),(Qt.Key_I,Qt.ShiftModifier),(Qt.Key_O,Qt.ShiftModifier)):
                QTest.keyClick(window,key,mod)
            assert (model.editState['trimIn'],model.editState['trimOut'])==saved
            model.setOperationEnabled('trim',True)
            p.seek(1220);settlement.wait_for('shortcut In target',lambda f:f.startTime()==1200000)
            find(window,'workspaceKeyboardFocus').forceActiveFocus();QTest.keyClick(window,Qt.Key_I)
            assert model.editState['trimIn']==p.position
            QTest.keyClick(window,Qt.Key_I,Qt.ShiftModifier);assert model.editState['trimIn']==0
            p.seek(2420);settlement.wait_for('shortcut Out target',lambda f:f.startTime()==2400000)
            find(window,'workspaceKeyboardFocus').forceActiveFocus();QTest.keyClick(window,Qt.Key_O)
            assert model.editState['trimOut']==p.position
            QTest.keyClick(window,Qt.Key_O,Qt.ShiftModifier);assert model.editState['trimOut']==4000
            # Crop owns its existing overlay input; Trim transport owns its grab.
            model.selectOperation('crop');model.setOperationEnabled('crop',True);assert model.setCropRect(20,20,200,120)
            crop=find(window,'cropOverlayRect');start=point(crop);end=QPoint(start.x()+30,start.y()+20)
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start);QTest.mouseMove(window,end);QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,end)
            assert model.editState['cropX']>20 and model.editState['cropY']>20
            capture('pc3-fullscreen.png')
            for turn in range(5):
                window.exitViewerFullscreen();wait(app,lambda:not p.fullscreen,'cycle exit')
                window.enterViewerFullscreen();wait(app,lambda:p.fullscreen,'cycle enter')
                assert (p._player,p._audio,find(window,'viewerVideoOutput'),p._timeline)==identities
            evidence['fullscreen']={'shared_frame_label':label,'shared_handles_and_shortcuts':True,'disabled_read_only':True,'outside_zone_grab':True,'crop_pointer_edit':True,'same_four_identities_cycles':5}
        with diagnostics.phase_scope('native audio default follower controlled notifications'):
            follower=p._audio_follower;observer=follower._devices
            # Control only the observer's fresh-default query. Devices are actual
            # native QAudioDevice objects; the Windows system default is untouched.
            outputs=QMediaDevices.audioOutputs();assert outputs
            original_query=observer.defaultAudioOutput
            query=[outputs[0]];observer.defaultAudioOutput=lambda:query[0]
            p.setVolume(37);p.toggleMuted();model.setCropPreview(True)
            audio_results=[]
            try:
                for playing in (False,True):
                    if p.playing!=playing:p.togglePlayback()
                    for device in outputs:
                        before=(p._player,p._audio,p._source,p._desired_playing,p.volume,p.muted,p.loopMode,p.fullscreen,dict(model.editState))
                        position=p.position;query[0]=device;observer.audioOutputsChanged.emit()
                        assert bytes(p._audio.device().id())==bytes(device.id())
                        assert before==(p._player,p._audio,p._source,p._desired_playing,p.volume,p.muted,p.loopMode,p.fullscreen,dict(model.editState))
                        assert p.position==position
                        observer.audioOutputsChanged.emit()
                        audio_results.append({'playing':playing,'device_id':bytes(device.id()).hex(),'same_ownership_intent_and_ui':True})
                query[0]=QAudioDevice();observer.audioOutputsChanged.emit();assert follower.pending
                query[0]=original_query();observer.audioOutputsChanged.emit();assert not follower.pending
            finally:
                observer.defaultAudioOutput=original_query;follower.refresh()
            if p.playing:p.togglePlayback()
            assert len(p.findChildren(QMediaPlayer))==1 and len(p.findChildren(QAudioOutput))==1
            evidence['audio']={'controlled_native_device_application':audio_results,'null_default_reappearance':True,'physical_system_default_switch':'NOT VERIFIED; requires user Windows default change / audible observation'}
        with diagnostics.phase_scope('Info and exact Explorer selection'):
            window.exitViewerFullscreen();wait(app,lambda:not p.fullscreen,'Info normal')
            source=output/'Папка с пробелами'/'публичный source.mp4';source.parent.mkdir();shutil.copyfile(fixtures/'integral.mp4',source)
            settlement.close();settlement=open_source(app,window,model,source,rate=Fraction(25));diagnostics.bind(observer=settlement)
            rows=model.mediaInfoRows;assert next(r['value'] for r in rows if r['label']=='Frames')=='100'
            assert next(r['value'] for r in rows if r['label']=='Container')=='MP4'
            assert next(r['value'] for r in rows if r['label']=='Frame rate')=='25 fps (25/1)'
            assert any(r['value']=='AAC (LC)' for r in rows) or any(r['value']=='AAC' for r in rows)
            assert next(r['value'] for r in rows if r['label']=='Path')==str(source.resolve())
            popup=find(window,'metadataInfoPopup');QMetaObject.invokeMethod(popup,'open',Qt.DirectConnection)
            QTest.qWait(200)
            background=popup.property('background');origin=background.mapToScene(QPointF())
            assert origin.x()>=0 and origin.y()>=0
            assert origin.x()+background.width()<=window.width()+1
            assert origin.y()+background.height()<=window.height()+1
            capture('pc3-info.png')
            resize_native_window(window, 800,560)
            wait(app,lambda:background.height()<=window.height()-92,'minimum Info viewport resize')
            QTest.qWait(200);origin=background.mapToScene(QPointF())
            assert origin.x()>=0 and origin.y()>=0
            assert origin.x()+background.width()<=window.width()+1
            assert origin.y()+background.height()<=window.height()+1
            scroll=find(window,'metadataInfoScroll')
            assert QMetaObject.invokeMethod(scroll,'scrollToEnd',Qt.DirectConnection)
            QTest.qWait(100)
            assert abs(scroll.property('scrollOffset')-scroll.property('maximumScrollOffset'))<1
            capture('pc3-info-minimum-scrolled.png')
            resize_native_window(window, 1440,900);QTest.qWait(200)
            explorer=ExplorerSelectionObserver();initial={r['hwnd'] for r in explorer.selections()};selected=[]
            try:
                assert model.revealCurrentSource()
                def selected_exact():
                    selected[:]=[r for r in explorer.selections() if r['paths']==[str(source.resolve())]]
                    return bool(selected)
                wait(app,selected_exact,'Explorer selected exact Unicode/spaces source',timeout=15)
                evidence['reveal']={'exact_selected_path':str(source.resolve()),'spaces_unicode':True}
            finally:
                for browser in selected:
                    if browser['hwnd'] not in initial:explorer.close_new(browser['hwnd'])
                explorer.close();activate_native_input(window)
            # Windows correctly locks a source owned by the decoder. Retire
            # that concrete reader before making the selected session path
            # missing; this is a harness setup step, not a product fallback.
            p.clear_source()
            source.unlink();assert not model.revealCurrentSource() and model.revealMessage
            evidence['reveal']['missing_source_bounded']=True
            evidence['info_rows']=rows
            popup.close()
        assert not warnings,warnings
        with diagnostics.phase_scope('shutdown ignores queued audio notifications'):
            follower=p._audio_follower;observer=follower._devices;old_device=bytes(p._audio.device().id())
            settlement.close();settlement=None
            model.shutdown();observer.audioOutputsChanged.emit();follower.refresh()
            assert bytes(p._audio.device().id())==old_device
            evidence['audio']['shutdown_stale_ignored']=True
        (output/'pc3-native.json').write_text(json.dumps(evidence,indent=2),encoding='utf8')
        print('PC3 native matrix PASS; physical Windows default changes NOT VERIFIED',flush=True)
    except BaseException:
        (output/'partial.json').write_text(json.dumps(evidence,indent=2),encoding='utf8');raise
    finally:
        if settlement is not None:settlement.close()
        diagnostics.call('final playback teardown',model.shutdown)
        close_native_windows(window);shiboken6.delete(engine)
        shiboken6.delete(model.playback._audio)
        diagnostics.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--fixtures',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.fixtures.resolve(),args.output.resolve())
