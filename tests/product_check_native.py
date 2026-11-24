"""Product Check native Windows acceptance through the persistent QML output."""
from fractions import Fraction
import hashlib
import json
import time
from PySide6.QtCore import QObject, QPoint, QPointF, QUrl, Qt
from PySide6.QtGui import QImage, QWheelEvent
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QAudioBufferOutput
from PySide6.QtTest import QTest
import shiboken6
from native_window_policy import resize_native_window
from native_workspace_regressions import wait, find, rect, open_source, DecodedFrameSettlement, activate_native_input


def product_checks(app, window, model, fixtures, output, initial_settlement):
    playback = model.playback
    video = find(window, 'viewerVideoOutput')
    sink = video.property('videoSink')
    initial_settlement.close()
    audio, states, raw_positions, lifetimes, audio_buffers = playback._audio, [], [], [], []
    monitor = QAudioBufferOutput()
    monitor.audioBufferReceived.connect(lambda buffer: audio_buffers.append(buffer.byteCount()) if buffer.byteCount() else None)
    original_factory, original_disposer = playback._player_factory, playback._player_disposer
    def factory():
        assert not shiboken6.isValid(playback._player)
        assert not playback.findChildren(QMediaPlayer)
        player = original_factory()
        player.setAudioBufferOutput(monitor)
        player.playbackStateChanged.connect(lambda state: states.append(state.name))
        player.positionChanged.connect(raw_positions.append)
        lifetimes.append({'retired_before_create': True})
        return player
    playback._player_factory = factory
    playback.setVolume(42)
    replacements = []
    settlement = None
    try:
        for turn in range(20):
            print('native source replacement',turn,flush=True)
            path = fixtures / ('long60.mp4' if turn % 2 == 0 else 'integral.mp4')
            if settlement is not None: settlement.close()
            old, old_handlers, old_frame_handler = playback._player, dict(playback._player_signal_handlers), playback._frame_handler
            old_staging=playback._staging_sink
            old_player_generation=playback._player_generation
            old_source_generation=playback._source_generation
            old_address = shiboken6.getCppPointer(old)[0]
            old_frame = sink.videoFrame()
            delivered_widths = []
            def cycle_frame(frame):
                if frame.isValid(): delivered_widths.append(frame.width())
            sink.videoFrameChanged.connect(cycle_frame)
            settled_states, settled_buffers = len(states), len(audio_buffers)
            settlement = open_source(app, window, model, path, rate=Fraction(60 if turn % 2 == 0 else 25))
            frame = settlement.wait_for('replacement paused first frame', lambda frame: frame.startTime() == 0)
            assert not shiboken6.isValid(old) and playback._player is not old
            assert playback._player.playbackState() == QMediaPlayer.PausedState and playback.position == 0
            assert not playback._desired_playing and playback.loopMode == 'WholeSourceLoop'
            assert 'PlayingState' not in states[settled_states:] and len(audio_buffers) == settled_buffers
            assert frame.width() == (1024 if turn % 2 == 0 else 320)
            sink.videoFrameChanged.disconnect(cycle_frame)
            assert delivered_widths and set(delivered_widths) == {frame.width()}, delivered_widths
            assert playback._audio is audio and find(window,'viewerVideoOutput') is video
            assert len(playback.findChildren(QMediaPlayer)) == len(playback.findChildren(QAudioOutput)) == 1
            before = playback.state, playback.position, playback.frameText, playback._presented_start
            old_handlers['position'](99999)
            old_handlers['media_status'](QMediaPlayer.EndOfMedia)
            old_handlers['error'](QMediaPlayer.ResourceError, 'retired source')
            old_frame_handler(old_frame)
            from mediaforge.presentation.bridge.playback import AdmittedVideoFrame
            playback._on_admitted_video_frame(AdmittedVideoFrame(old_frame,old_source_generation,old_player_generation,old,old_staging))
            assert not shiboken6.isValid(old_staging)
            assert before == (playback.state, playback.position, playback.frameText, playback._presented_start)
            replacements.append({'turn':turn, 'source':path.name, 'timestamp_us':frame.startTime(),
                                 'position_ms':playback.position, 'width':frame.width(), 'no_playing_or_audio':True,
                                 'retired_address':old_address,'current_address':shiboken6.getCppPointer(playback._player)[0],
                                 'only_current_source_frames':True})
        settlement.close(); settlement = open_source(app, window, model, fixtures/'long60.mp4', rate=Fraction(60))
        current = playback._player
        metadata = model._workspace.active.metadata
        assert metadata.width == 1024 and metadata.height == 768 and metadata.duration_seconds == 180
        # Total now comes from the full presentation scan. The earlier bounded
        # metadata proof is deliberately not an ordinal/count authority.
        count = 10800  # Controlled fixture, also independently scanned by open_source.
        assert playback._timeline.count == count and playback.totalFrameCountReliable
        def label(frame):
            # Explicit controlled 60-fps fixture oracle, not product rate math.
            return f'Frame {round(Fraction(frame.startTime(),1000000)*60)+1} / {count}'
        def image_hash(frame):
            image = frame.toImage().convertToFormat(QImage.Format_RGBA8888)
            assert not image.isNull()
            return hashlib.sha256(bytes(image.constBits())).hexdigest()
        def seek(target):
            revision = settlement.revision
            playback.seek(target)
            return settlement.wait_for(f'long seek {target}', lambda f: f.startTime() <= target*1000+2 and f.endTime() >= target*1000-2, after_revision=revision)
        observations = []
        for region, index in (('start',2),('25%',2700),('50%',5400),('75%',8100),('end',10797)):
            frame = seek(round((index+Fraction(1,2))*1000/60))
            for direction in (1,-1,1,1,-1,-1):
                wait(app, lambda: playback.exactStepAvailable, "long local proof")
                before = round(Fraction(frame.startTime(),1_000_000)*60)
                digest, revision = image_hash(frame), settlement.revision
                started = time.perf_counter()
                playback.stepFrame(direction)
                frame = settlement.wait_for('long adjacent decoded frame', lambda f: playback._step_pending is None, after_revision=revision)
                elapsed = (time.perf_counter()-started)*1000
                after = round(Fraction(frame.startTime(),1_000_000)*60)
                assert after == before+direction and abs(frame.startTime()-round(after*1_000_000/60)) <= 2
                assert image_hash(frame) != digest and playback.frameText == label(frame) and playback.framesReliable
                observations.append({'region':region,'before':before,'direction':direction,'after':after,'timestamp_us':frame.startTime(), 'step_ms':elapsed, 'image_sha256':image_hash(frame)})
        # Exact first/last clamps still inspect timestamps, images and labels.
        for index, direction in ((0,-1),(10799,1)):
            frame = seek(round((index+Fraction(1,2))*1000/60))
            before = frame.startTime(), image_hash(frame), playback.frameText
            wait(app, lambda: playback.exactStepAvailable, "endpoint local proof")
            playback.stepFrame(direction)
            frame = settlement.wait_for('long endpoint clamp')
            assert before == (frame.startTime(), image_hash(frame), playback.frameText)
        output['long_cfr'] = {'width':1024,'height':768,'duration_s':180,'frames':10800,
                              'steps':observations,'no_local_probes':True,
                              'min_step_ms':min(o['step_ms'] for o in observations),'max_step_ms':max(o['step_ms'] for o in observations),
                              'mean_step_ms':sum(o['step_ms'] for o in observations)/len(observations)}
        (output['directory']/'long-cfr-evidence.json').write_text(json.dumps(output['long_cfr'],indent=2),encoding='utf-8')
        # Real playback loop observes actual native position transitions.
        settlement.close(); settlement = open_source(app, window, model, fixtures/'integral.mp4', rate=Fraction(25))
        current = playback._player
        model.setOperationEnabled('trim',True); model.setTrimRange(400,800)
        from reviewer_findings_native import trim_render_checks, crop_wheel_fallback
        raw_positions.clear()
        output['trim_render_boundary'] = trim_render_checks(app, window, model, settlement,output['directory']/'trim-paint')
        assert playback.playing and playback.trimLoopActive
        output['loop_native_positions'] = list(raw_positions)
        playback.seek(1000)
        assert not playback.trimLoopActive and playback.playing
        model.setTrimRange(1200,1600)
        assert not playback.trimLoopActive
        playback.togglePlayback(); playback.seek(1400); playback.togglePlayback()
        assert playback.trimLoopActive
        position = playback.position
        model.setOperationEnabled('trim',False)
        assert playback.position == position and playback.playing and not playback.trimLoopActive
        model.setOperationEnabled('trim',True); assert not playback.trimLoopActive
        playback.togglePlayback(); model.setOperationEnabled('trim',False)
        output['crop_wheel_fallback'] = crop_wheel_fallback(app,window,model)
        # Single/double fallback through native window events, preserving player.
        def point(item):
            p = item.mapToScene(QPointF(item.width()/2,item.height()/2))
            return QPoint(round(p.x()),round(p.y()))
        def click(name):
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point(find(window,name)))
        click('viewerBackgroundInput')
        wait(app,lambda: playback.playing,'native Viewer single click')
        click('playPauseButton'); wait(app,lambda: not playback.playing,'explicit pause')
        before = len(states)
        click('viewerBackgroundInput')
        QTest.mouseDClick(window,Qt.LeftButton,Qt.NoModifier,point(find(window,'viewerBackgroundInput')))
        wait(app,lambda: playback.fullscreen,'native double click enter')
        QTest.qWait(app.styleHints().mouseDoubleClickInterval()+40)
        assert len(states) == before and playback._player is current and not playback.playing
        # Entry orientation expires with the pointer outside the activation zone.
        QTest.mouseMove(window,QPoint(window.width()//2,20))
        wait(app,lambda: not playback.controlsVisible,'orientation hide')
        transport = find(window,'fullscreenTransport')
        wait(app,lambda: transport.opacity() == 0,'hidden transport fade')
        def capture(name):
            assert window.grabWindow().save(str(output['directory']/(name+'.png')))
        capture('fullscreen-idle')
        QTest.mouseMove(window,QPoint(window.width()//2,100))
        assert not playback.controlsVisible
        QTest.mouseMove(window,QPoint(window.width()//2,window.height()-15))
        wait(app,lambda: playback.controlsVisible and transport.opacity() == 1,'bottom zone reveal')
        capture('fullscreen-bottom-zone')
        for control_name in ('fullscreenTimeline','fullscreenVolumeSlider','fullscreenMuteButton'):
            control = find(window,control_name)
            QTest.mouseMove(window,point(control))
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point(control))
            assert playback._controls_interactions > 0
            QTest.mouseMove(window,QPoint(window.width()//2,window.height()-280))
            QTest.qWait(400)  # Exceeds the specified leave debounce + fade, testing held pointer ownership.
            assert playback.controlsVisible and playback._controls_interactions > 0
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,QPoint(window.width()//2,window.height()-280))
            assert playback._controls_interactions == 0, (control_name,playback._controls_interactions,playback._controls_zone)
            assert not playback._controls_zone, (control_name, 'pointer outside zone after release')
            wait(app,lambda: not playback.controlsVisible,'leave after drag release')
            QTest.mouseMove(window,QPoint(window.width()//2,window.height()-15))
            wait(app,lambda: playback.controlsVisible and transport.opacity() == 1,'reveal after drag')
        # Control click belongs to transport, never to Viewer fallback.
        before = len(states)
        click('fullscreenMuteButton')
        QTest.qWait(app.styleHints().mouseDoubleClickInterval()+40)
        assert len(states) == before
        surface_point = transport.mapToScene(QPointF(4,4))
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,QPoint(round(surface_point.x()),round(surface_point.y())))
        QTest.qWait(app.styleHints().mouseDoubleClickInterval()+40)
        assert len(states) == before
        click('viewerBackgroundInput')
        QTest.mouseDClick(window,Qt.LeftButton,Qt.NoModifier,point(find(window,'viewerBackgroundInput')))
        wait(app,lambda: not playback.fullscreen,'native double click exit')
        QTest.qWait(app.styleHints().mouseDoubleClickInterval()+40)
        assert len(states) == before and playback._player is current and playback._audio is audio and find(window,'viewerVideoOutput') is video
        resize_native_window(window, 1440,900)
        wait(app,lambda: window.width()==1440 and window.height()==900,'reference viewport')
        playback.setVolume(42)
        model.selectOperation('trim')
        QTest.qWait(220)  # Approved Inspector transition; decoded readiness remains event-driven.
        capture('normal-volume')
        click('muteButton'); assert playback.volume == 0 and playback.lastNonZeroVolume == 42
        capture('normal-muted')
        playback.toggleMuted(); assert playback.volume == 42
        assert find(window,'volumeSlider').property('value') == 42
        model.setOperationEnabled('trim',True); model.setTrimRange(400,1200)
        revision = settlement.revision
        playback.seek(600); playback.togglePlayback(); assert playback.trimLoopActive
        settlement.wait_for('loop capture decoded delivery',
                            lambda frame: 400000 <= frame.startTime() < 1200000,
                            after_revision=revision)
        capture('trim-loop-range')
        playback.togglePlayback(); model.setOperationEnabled('trim',False)
        # Larger source makes the specified 828 -> 829 -> 830 scrub valid.
        settlement.close(); settlement = open_source(app,window,model,fixtures/'long60.mp4',rate=Fraction(60))
        model.selectOperation('crop'); model.setOperationEnabled('crop',True)
        assert model.setCropRect(10,20,828,400)
        QTest.qWait(220)
        capture('operations-whole-pill')
        editor = find(window,'cropWInput')
        activate_native_input(window)
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point(editor))
        assert editor.property('activeFocus'), 'Native Crop wheel requires focused editor'
        editor.setProperty('cursorPosition',3)
        rect_width = find(window,'cropOverlayRect').width()
        p = editor.mapToScene(QPointF(editor.width()/2,editor.height()/2))
        QTest.mouseMove(window,point(editor))
        event = QWheelEvent(p,QPointF(window.mapToGlobal(point(editor))),QPoint(),QPoint(0,120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
        window._native_input_clock.stamp(event)
        app.sendEvent(window,event); app.processEvents()
        assert editor.property('text') == '829' and editor.property('invalid'), {
            'text': editor.property('text'), 'invalid': editor.property('invalid'),
            'focus': editor.property('activeFocus'), 'cursor': editor.property('cursorPosition'),
        }
        assert model.editState['cropWidth'] == 828 and find(window,'cropOverlayRect').width() == rect_width
        capture('crop-invalid-wheel')
        event = QWheelEvent(p,QPointF(window.mapToGlobal(point(editor))),QPoint(),QPoint(0,120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
        window._native_input_clock.stamp(event)
        app.sendEvent(window,event)
        assert model.editState['cropWidth'] == 830 and not editor.property('invalid')
        # Accepted current-source Preview/fullscreen identity remains unchanged.
        current, position = playback._player, playback.position
        for _ in range(4): model.setCropPreview(True); model.setCropPreview(False)
        assert playback._player is current and playback.position == position
        output['paused_first_frames'] = replacements
        output['retire_before_create_checks'] = len(lifetimes)
        output['native_input'] = {'single_double_click':True,'zone_height':140,'timeline_volume_grabs':True,'crop_invalid_wheel':True}
        # Complete-timeline navigation still fails closed if Qt never settles
        # the actual target. A timer expiry is terminal, not a fabricated label.
        wait(app,lambda:playback.exactStepAvailable,'native pre-failure indexed map')
        playback._step_did_not_settle()
        assert not playback.framesReliable and not playback.exactStepAvailable and playback._player is current
        output['native_unsettled_step_closed'] = True
        settlement.close(); settlement = open_source(app,window,model,fixtures/'integral.mp4',rate=Fraction(25))
        assert playback.framesReliable
        playback._player.errorOccurred.emit(QMediaPlayer.ResourceError,'test-injected native backend error signal')
        settlement.wait_for('current backend error fails closed',expected_label=f'Frame — / {playback._timeline.count}')
        assert playback.state == 'Error' and not playback.framesReliable and not playback.exactStepAvailable and not playback.canPlay
        output['current_backend_error_signal_fail_closed'] = True
    finally:
        if settlement is not None: settlement.close()
        playback._player_factory, playback._player_disposer = original_factory, original_disposer
