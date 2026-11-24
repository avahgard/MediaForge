"""PC2 permanent native interaction, loop and actual window-paint state matrix."""
import argparse
import json
import logging
from pathlib import Path
from fractions import Fraction
from PySide6.QtCore import QObject,QPoint,QPointF,QUrl,Qt,QMetaObject
from PySide6.QtGui import QGuiApplication,QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput,QAudioBufferOutput
from PySide6.QtTest import QTest,QSignalSpy
import shiboken6
from native_window_policy import (load_native_shell as load_shell, resize_native_window, close_native_windows)
from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.resources.paths import qml_root
from native_workspace_regressions import wait,find,open_source,activate_native_input
from reviewer_findings_native import trim_render_checks


def run(fixtures,output):
    output.mkdir(parents=True,exist_ok=True)
    app=QGuiApplication([]);engine=QQmlApplicationEngine()
    model=build_media_view_model(logging.getLogger('pc2-workspace'))
    engine.rootContext().setContextProperty('mediaViewModel',model)
    warnings=[];engine.warnings.connect(lambda values:warnings.extend(v.toString() for v in values))
    window=load_shell(engine,qml_root(),logging.getLogger('pc2-workspace'))
    playback=model.playback;evidence={'dpr':window.devicePixelRatio(),'fullscreen_cycles':[]}
    settlement=None
    def point(item):
        p=item.mapToScene(QPointF(item.width()/2,item.height()/2))
        return QPoint(round(p.x()),round(p.y()))
    def click(name,button=Qt.LeftButton):
        QTest.mouseClick(window,button,Qt.NoModifier,point(find(window,name)))
        app.processEvents()
    def capture(name):
        assert window.grabWindow().save(str(output/name))
    paint_count=[0]
    def paint():paint_count[0]+=1
    window.frameSwapped.connect(paint,Qt.QueuedConnection)
    def painted():
        revision=paint_count[0];window.update()
        wait(app,lambda:paint_count[0]>revision,'actual window render swap')
    try:
        resize_native_window(window, 1440,900)
        activate_native_input(window)
        print('matrix: open source',flush=True)
        settlement=open_source(app,window,model,fixtures/'integral.mp4',rate=Fraction(25))
        print('matrix: operation pills',flush=True)
        video=find(window,'viewerVideoOutput');audio=playback._audio;player=playback._player
        # Every operation owns a single outer painted capsule. Button backgrounds
        # are genuinely unpainted Items; the enabled circle keeps its hit region.
        operations=[]
        for name in ('Trim','Crop','Reformat','Noise','Output'):
            pill=find(window,'operation'+name)
            selector=find(window,'select'+name);toggle=find(window,'enable'+name)
            for control in (selector,toggle):
                background=control.property('background')
                assert background.property('color') is None and background.property('radius') is None
            if name not in ('Noise','Output'):
                selected=model.selectedOperation
                old=model.editState[('resize' if name == 'Reformat' else name.lower())+'Enabled']
                click('enable'+name);assert model.selectedOperation==selected
                assert model.editState[('resize' if name == 'Reformat' else name.lower())+'Enabled'] is not old
                click('select'+name);assert model.selectedOperation==('resize' if name == 'Reformat' else name.lower())
                click('select'+name,Qt.RightButton)
                assert model.editState[('resize' if name == 'Reformat' else name.lower())+'Enabled'] is old
                click('enable'+name,Qt.RightButton)
                assert model.editState[('resize' if name == 'Reformat' else name.lower())+'Enabled'] is not old
                click('activeOperationToggle')
                assert model.editState[('resize' if name == 'Reformat' else name.lower())+'Enabled'] is old
            operations.append({'operation':name,'one_outer_capsule':True,'circle_hit_width':toggle.width()})
        model.selectOperation('trim');model.setOperationEnabled('trim',True)
        assert model.setTrimRange(400,800)
        wait(app,lambda:find(window,'inspectorContent').opacity()==1 and find(window,'inspectorContent').x()==0,'settled Inspector reveal')
        painted();capture('operation-pill-selected.png')
        print('matrix: disabled Trim',flush=True)
        model.setOperationEnabled('trim',False)
        saved=model.editState['trimIn'],model.editState['trimOut']
        assert saved==(400,800)
        assert not model.setTrimRange(0,4000)
        for method in (model.setTrimInAtPlayhead,model.setTrimOutAtPlayhead):
            assert not method(False) and not method(True)
        playback.seek(1220)
        settlement.wait_for('disabled Trim manual seek',lambda f:f.startTime()==1200000)
        find(window,'workspaceKeyboardFocus').forceActiveFocus()
        for key,modifier in ((Qt.Key_I,Qt.NoModifier),(Qt.Key_O,Qt.NoModifier),
                             (Qt.Key_I,Qt.ShiftModifier),(Qt.Key_O,Qt.ShiftModifier)):
            QTest.keyClick(window,key,modifier)
        for name in ('setTrimInButton','setTrimOutButton'):
            assert not find(window,name).isEnabled()
            click(name);click(name,Qt.RightButton)
        for name in ('trimInInput','trimOutInput'):
            editor=find(window,name);assert editor.property('readOnly')
            text=editor.property('text');click(name)
            QTest.keyClick(window,Qt.Key_A,Qt.ControlModifier)
            for key in (Qt.Key_0,Qt.Key_0,Qt.Key_Colon,Qt.Key_0,Qt.Key_0,Qt.Key_Period,Qt.Key_0,Qt.Key_0,Qt.Key_0):
                QTest.keyClick(window,key)
            QTest.keyClick(window,Qt.Key_Return)
            assert editor.property('text')==text
            assert not editor.property('invalid')
        for name in ('trimInHandle','trimOutHandle'):
            handle=find(window,name);start=point(handle)
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
            QTest.mouseMove(window,QPoint(start.x()+100,start.y()))
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,QPoint(start.x()+100,start.y()))
        assert (model.editState['trimIn'],model.editState['trimOut'])==saved
        painted();capture('trim-disabled-readonly.png')
        model.setOperationEnabled('trim',True)
        assert (model.editState['trimIn'],model.editState['trimOut'])==saved
        evidence['operations']=operations;evidence['disabled_trim_all_mutation_paths']=True
        find(window,'workspaceKeyboardFocus').forceActiveFocus()
        evidence['trim_boundary']=trim_render_checks(app,window,model,settlement,output/'trim-paint')
        print('matrix: whole source loop',flush=True)
        assert playback.playing and playback.loopMode=='TrimRangeLoop'
        # Presentation transformations preserve both loop modes and Playing
        # intent; they never replace or reposition the accepted current player.
        position=playback.position
        for preview in (True,False):
            model.setCropPreview(preview)
            assert playback.loopMode=='TrimRangeLoop' and playback._desired_playing
            assert playback.position==position and playback._player is player
        playback.setVolume(42);playback.toggleMuted()
        assert playback.volume==0 and playback.lastNonZeroVolume==42
        audio_times=[]
        monitor=QAudioBufferOutput(player)
        def audio_received(buffer):
            if buffer.isValid():audio_times.append(buffer.startTime())
        monitor.audioBufferReceived.connect(audio_received)
        player.setAudioBufferOutput(monitor)
        playback.seek(3800)
        assert playback.playing and playback.loopMode=='WholeSourceLoop'
        states=[];times=[]
        def observe():
            states.append(playback.state)
            if playback._presented_start is not None:times.append(playback._presented_start)
        playback.changed.connect(observe)
        try:
            wait(app,lambda:sum(b<a for a,b in zip(times,times[1:]))>=2
                 and sum(b<a for a,b in zip(audio_times,audio_times[1:]))>=2,
                 'two actual video/audio whole-source loops',timeout=12)
            assert set(states)=={'Playing'} and playback._desired_playing
            assert playback._player is player and playback._audio is audio
            assert sum(b<a for a,b in zip(audio_times,audio_times[1:]))>=2,audio_times
            assert playback.volume==0 and playback.lastNonZeroVolume==42 and audio.isMuted()
            position=playback.position
            for preview in (True,False):model.setCropPreview(preview)
            assert playback.loopMode=='WholeSourceLoop' and playback._desired_playing
            assert playback.position==position and playback._player is player
            model.windowPresentation.enter(window)
            wait(app,lambda:window.visibility()==QWindow.FullScreen,'Playing fullscreen')
            assert playback.loopMode=='WholeSourceLoop' and playback._desired_playing and playback.playing
            model.windowPresentation.exit()
            wait(app,lambda:not playback.fullscreen,'Playing fullscreen restore')
            assert playback.loopMode=='WholeSourceLoop' and playback._desired_playing and playback.playing
            assert (playback._player,playback._audio,find(window,'viewerVideoOutput'))==(player,audio,video)
            playback.toggleMuted();assert playback.volume==42 and not audio.isMuted()
            playback.togglePlayback();assert playback.state=='Paused' and not playback._desired_playing
        finally:
            playback.changed.disconnect(observe);player.setAudioBufferOutput(None)
            monitor.audioBufferReceived.disconnect(audio_received)
        evidence['whole_source_loop']={'states':states,'decoded_timestamps':times,'audio_timestamps':audio_times,
            'loops':sum(b<a for a,b in zip(times,times[1:])),'mute_volume_restore':42,'preview_fullscreen_playing_preserved':True}
        # Paused last picture never restarts by itself; user Play there enters
        # whole-source mode and survives EOF on the same concrete player.
        revision=settlement.revision;playback.seek(3980)
        settlement.wait_for('paused final picture',lambda f:f.startTime()==3960000,after_revision=revision)
        playback.stepFrame(1)
        assert playback.state=='Paused' and not playback._desired_playing
        assert model.setTrimRange(0,4000)  # Even a full-length enabled Trim does not override final-picture Play.
        playback.togglePlayback();assert playback.loopMode=='WholeSourceLoop'
        settlement.wait_for('Play from final picture loops',lambda f:f.startTime()<400000)
        playback.togglePlayback()
        # Hold burst: one current + one coalesced intent, no queued key backlog.
        revision=settlement.revision;playback.seek(2020)
        settlement.wait_for('burst setup actual frame',lambda f:f.startTime()==2000000,after_revision=revision)
        starts=[]
        def frame_event():
            timestamp=playback._presented_start
            if timestamp is not None and (not starts or starts[-1]!=timestamp):starts.append(timestamp)
        playback.changed.connect(frame_event)
        launches=[];indexer=playback._timeline_service._indexer;factory=indexer._factory
        def spawn(*a,**k):launches.append(a[0]);return factory(*a,**k)
        indexer._factory=spawn
        try:
            for _ in range(40):QTest.keyClick(window,Qt.Key_Right,Qt.ShiftModifier)
            wait(app,lambda:playback._presented_start==2080000 and playback._step_pending is None and playback._queued_step is None,'two drained coalesced adjacent intents')
            assert starts==[2000000,2040000,2080000] or starts==[2040000,2080000],starts
            assert not launches and playback.frameText=='Frame 53 / 100'
        finally:
            playback.changed.disconnect(frame_event);indexer._factory=factory
        evidence['hold_burst']={'keypresses':40,'actual_timestamps':starts,'per_key_probe_launches':len(launches)}
        # Trace every native window-state/geometry event and each actual render
        # swap. Window-state events are captured synchronously on their GUI owner;
        # render-swap notifications are queued to avoid Python/GUI calls on Qt's
        # render thread. The native C++ QSignalSpy records every swap immediately;
        # comparing its revisions at synchronous state events proves that even
        # Qt's internal Windowed notification has no rendered Normal frame.
        for mode in ('Normal','Maximized'):
            print('matrix: fullscreen',mode,flush=True)
            if mode=='Normal':
                window.showNormal()
                normal_placement=resize_native_window(window,1440,900)
            else:window.showMaximized()
            target=QWindow.Windowed if mode=='Normal' else QWindow.Maximized
            wait(app,lambda:window.visibility()==target,'initial '+mode);painted()
            before=window.geometry();identities=(playback._player,playback._audio,find(window,'viewerVideoOutput'))
            position=playback.position
            if mode=='Maximized':capture('fullscreen-maximized-before.png')
            for turn in range(20):
                trace=[]
                swap_spy=QSignalSpy(window.frameSwapped)
                def observed(kind):
                    g=window.geometry()
                    trace.append({'event':kind,'visibility':window.visibility().name,
                                  'geometry':[g.x(),g.y(),g.width(),g.height()],
                                  'render_swap_revision':swap_spy.count(),
                                  'fullscreen_viewer':playback.fullscreen})
                def swapped():observed('frameSwapped')
                def state_changed(_):observed('visibilityChanged')
                window.frameSwapped.connect(swapped,Qt.QueuedConnection)
                window.visibilityChanged.connect(state_changed)
                try:
                    activate_native_input(window);find(window,'workspaceKeyboardFocus').forceActiveFocus()
                    click('fullscreenButton')  # F is Viewer Fit; use the explicit presentation action.
                    wait(app,lambda:window.visibility()==QWindow.FullScreen and playback.fullscreen,'enter fullscreen')
                    painted()
                    QTest.keyClick(window,Qt.Key_Escape)
                    wait(app,lambda:window.visibility()==target and not playback.fullscreen,'restore '+mode)
                    painted()
                    assert window.geometry()==before,(mode,before,window.geometry())
                    if mode=='Maximized':
                        assert not any(e['event']=='frameSwapped' and e['visibility']=='Windowed' for e in trace),trace
                        events=[e for e in trace if e['event']=='visibilityChanged']
                        for index,event in enumerate(events):
                            if event['visibility']=='Windowed':
                                assert events[index+1]['visibility']=='Maximized',trace
                                assert events[index+1]['render_swap_revision']==event['render_swap_revision'],trace
                    expected=[before.x(),before.y(),before.width(),before.height()]
                    assert all(e['geometry']==expected for e in trace if e['event']=='frameSwapped' and e['visibility']==target.name),trace
                    assert identities==(playback._player,playback._audio,find(window,'viewerVideoOutput'))
                    assert playback.position==position and playback.state=='Paused'
                    evidence['fullscreen_cycles'].append({'mode':mode,'turn':turn,'trace':trace})
                finally:
                    window.frameSwapped.disconnect(swapped);window.visibilityChanged.disconnect(state_changed)
            if mode=='Maximized':capture('fullscreen-maximized-after.png')
        # Direct Maximized restores must also retain the original normal
        # placement for a later ordinary window restore, not overwrite it with
        # the maximized rectangle intercepted during the transaction.
        window.showNormal()
        wait(app,lambda:window.visibility()==QWindow.Windowed,'normal placement after maximized cycles')
        painted()
        normal=window.geometry()
        assert normal==normal_placement,(normal,normal_placement)
        evidence['normal_geometry_after_maximized_cycles']=[normal.x(),normal.y(),normal.width(),normal.height()]
        assert len(playback.findChildren(QMediaPlayer))==len(playback.findChildren(QAudioOutput))==1
        assert not warnings,warnings
        (output/'workspace-matrix.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    except BaseException:
        (output/'workspace-failed.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8');raise
    finally:
        if settlement:settlement.close()
        model.shutdown();close_native_windows(window);shiboken6.delete(engine)
        shiboken6.delete(model.playback._audio)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--fixtures',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    run(args.fixtures.resolve(),args.output.resolve())
