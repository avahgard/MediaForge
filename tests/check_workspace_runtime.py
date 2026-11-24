"""Visible Windows TASK-008 interaction and capture check (run manually)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from PySide6.QtCore import QObject, QMetaObject, QUrl, Qt, QPoint, QPointF, QMimeData
from PySide6.QtGui import QGuiApplication, QDragEnterEvent, QDragLeaveEvent, QDropEvent, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow  # noqa: F401
from PySide6.QtTest import QTest
import shiboken6
from PySide6.QtMultimedia import QMediaPlayer

from native_window_policy import (load_native_shell as load_shell, resize_native_window, close_native_windows)
from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.resources.paths import qml_root
import logging
from native_capture_geometry import assert_capture_size


def wait(app, predicate, label, timeout=12):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"Timeout: {label}")


def capture(app, window, target):
    for _ in range(20):
        app.processEvents()
        time.sleep(0.02)
    image = window.grabWindow()
    assert window.width() == 1440 and window.height() == 900
    assert_capture_size(window, image)
    assert image.save(str(target))


def main():
    parser = argparse.ArgumentParser()
    for name in ('landscape', 'portrait', 'cfr', 'spaces', 'unicode', 'vfr', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    for name in ('landscape', 'portrait', 'cfr', 'spaces', 'unicode', 'vfr'):
        if not getattr(args, name).is_file():
            parser.error(f'Fixture does not exist: {getattr(args, name)}')
    args.output.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication([])
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(value.toString() for value in values))
    model = build_media_view_model(logging.getLogger('task008-runtime'))
    engine.rootContext().setContextProperty('mediaViewModel', model)
    window = load_shell(engine, qml_root(), logging.getLogger('task008-runtime'))
    try:
        resize_native_window(window, 1440, 900)
        window.showNormal()
        window.requestActivate()
        evidence = exercise(app, engine, model, window, args, warnings)
        assert not warnings, warnings
        evidence['warnings'] = warnings
        (args.output / 'runtime.json').write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps(evidence, ensure_ascii=False))
    finally:
        model.shutdown()
        close_native_windows(window)
        shiboken6.delete(engine)
        shiboken6.delete(model.playback._audio)


def exercise(app, engine, model, window, args, warnings):
    from PySide6.QtMultimedia import QAudioOutput, QAudioBufferOutput
    playback = model.playback
    audio = playback._audio
    output = window.findChild(QObject, 'viewerVideoOutput')
    evidence = {'fixtures': {}, 'retire_before_create_checks': 0, 'fullscreen_cycles': 0}
    real_factory = playback._player_factory
    def checked_factory():
        assert not shiboken6.isValid(playback._player), 'Old concrete player survived successor creation'
        assert len(playback.findChildren(QMediaPlayer)) == 0
        evidence['retire_before_create_checks'] += 1
        return real_factory()
    playback._player_factory = checked_factory
    metadata_calls = []
    real_open = model._use_case.open_media
    def counted_open(path):
        metadata_calls.append(path)
        return real_open(path)
    model._use_case.open_media = counted_open
    processing_calls = []
    def forbidden_processing(*args):
        processing_calls.append(args)
        raise AssertionError('Workspace edits started FFmpeg processing')
    for bridge in (model._trim, model._crop, model._resize):
        bridge._use_case.start = forbidden_processing

    def find(name):
        results = [obj for obj in window.findChildren(QObject) if obj.objectName() == name]
        def visual(item):
            if item.objectName() == name and item not in results: results.append(item)
            for child in item.childItems():
                visual(child)
        visual(window.contentItem())
        assert results, name
        return next((obj for obj in results if hasattr(obj, 'isVisible') and obj.isVisible()), results[0])
    def point(item, x=None, y=None):
        p = item.mapToScene(QPointF(item.width()/2 if x is None else x,
                                    item.height()/2 if y is None else y))
        return QPoint(round(p.x()), round(p.y()))
    def click(item, button=Qt.LeftButton):
        QTest.mouseClick(window, button, Qt.NoModifier, point(item)); QTest.qWait(30)
    def drag(item, x1, y1, x2, y2):
        start, end = point(item, x1, y1), point(item, x2, y2)
        QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(window, end, 40)
        QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
        QTest.qWait(30)
    def invoke(item, method):
        assert QMetaObject.invokeMethod(item, method, Qt.DirectConnection)
        QTest.qWait(30)
    def focus_workspace():
        find('workspaceKeyboardFocus').forceActiveFocus(); app.processEvents()
    def ready():
        wait(app, lambda: model.state == 'loaded' and model.canOpen and playback.duration > 0 and playback.canPlay and playback.exactStepAvailable, 'source Ready', timeout=310)
        assert playback.state in ('Ready', 'Paused') and playback.position == 0
        assert len(playback.findChildren(QMediaPlayer)) == 1
        assert len(playback.findChildren(QAudioOutput)) == 1
        assert playback._audio is audio and find('viewerVideoOutput') is output
    def decode(label, path):
        player = playback._player
        frames, buffers = [], []
        sink = output.property("videoSink")
        assert player.videoSink() is playback._staging_sink and player.videoSink() is not sink
        def receive(frame):
            if frame.isValid():
                frames.append((frame.width(), frame.height()))
        sink.videoFrameChanged.connect(receive)
        monitor = QAudioBufferOutput(player)
        monitor.audioBufferReceived.connect(lambda b: buffers.append(b.frameCount()) if b.isValid() else None)
        player.setAudioBufferOutput(monitor)
        playback.togglePlayback()
        wait(app, lambda: playback.position >= 400 and len(frames) >= 5 and any(buffers), 'decoded video/audio ' + label)
        playback.togglePlayback()
        wait(app, lambda: playback.state == 'Paused', 'pause ' + label)
        evidence['fixtures'][label] = {'path': str(path.resolve()), 'decoded_frames': len(frames),
                                      'image_size': frames[0], 'audio_buffers': len(buffers),
                                      'duration_ms': playback.duration, 'frames_reliable': playback.framesReliable}
        player.setAudioBufferOutput(None)
        sink.videoFrameChanged.disconnect(receive)

    def capture_state(name):
        if not name.startswith('00'):
            target_position = 3000 if name.startswith('12') else 2000
            playback.seek(target_position)
            def decoded_capture_ready():
                frame = output.property("videoSink").videoFrame()
                return (frame.isValid() and frame.startTime() <= target_position*1000+2
                        and frame.endTime() >= target_position*1000-2
                        and playback._presented_start == frame.startTime())
            wait(app, decoded_capture_ready, 'deterministic decoded capture frame')
        capture(app, window, args.output / (name + '.png'))
        sidebar = {'mediaSidebar': [18,18,190,864]}
        workspace_geometry = dict(sidebar, **{
            'metadataInfoButton': [506,31,32,32],
            'windowedViewerHost': [242,92,804,452], 'windowedTransport': [242,556,804,70],
            'operationSurface': [242,676,804,62], 'mediaTraySurface': [242,782,804,100],
            'mediaInspector': [1070,92,342,790],
        })
        expected = (dict(sidebar, homeOpenMediaSurface=[242,120,1170,520],
                         openMediaButton=[752,414,150,46]) if name.startswith('00') else
                    {'fullscreenTransport': [84,774,1272,94]} if name.startswith('12') else
                    dict(workspace_geometry, mediaContextMenu=[430,658,176,126]) if name.startswith('13') else
                    workspace_geometry)
        geometry = {}
        for object_name, reference in expected.items():
            obj = find(object_name)
            if not hasattr(obj, 'mapToScene'): obj = obj.property('background')
            origin = obj.mapToScene(QPointF(0,0))
            actual = [origin.x(),origin.y(),obj.width(),obj.height()]
            deviation = max(abs(a-b) for a,b in zip(actual, reference))
            assert deviation <= 6, (name,object_name,actual,reference)
            geometry[object_name] = {'actual': actual, 'svg': reference, 'max_deviation_px': deviation}
        evidence.setdefault('visual_geometry', {})[name] = geometry

    capture_state('00-home')
    # Exercise genuine Qt drag/drop dispatch, including feedback and malformed payloads.
    for urls in (None, [QUrl('https://example.com/a.mp4')],
                 [QUrl.fromLocalFile(str(args.output / 'missing.mp4'))],
                 [QUrl.fromLocalFile(str(args.landscape.resolve()))] * 2):
        mime = QMimeData()
        if urls is None: mime.setText('not a media URL')
        else: mime.setUrls(urls)
        event = QDragEnterEvent(QPoint(827,270), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        app.sendEvent(window, event)
        assert not event.isAccepted() and model.state == 'empty'
        app.sendEvent(window, QDragLeaveEvent())
    def drop(path, home=False):
        mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(path.resolve()))])
        event = QDragEnterEvent(QPoint(827,270), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        app.sendEvent(window, event)
        assert event.isAccepted()
        assert find('homeDropArea' if home else 'loadedMediaDropArea').property('containsDrag')
        event = QDropEvent(QPointF(827,270), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        app.sendEvent(window, event); assert event.isAccepted()
        ready()
    drop(args.landscape, home=True)
    decode('landscape', args.landscape)
    assert playback.framesReliable
    first_id = model.activeMediaId
    first_player = playback._player
    # Real same-button Info toggle and no extra probe.
    for _ in range(3):
        click(find('metadataInfoButton')); assert find('metadataInfoPopup').property('visible')
        click(find('metadataInfoButton'))
        wait(app, lambda: not find('metadataInfoPopup').property('visible'), 'Info closed')
    assert len(metadata_calls) == 1
    # Timeline click, drag, frame step, clamped seconds, I/O and both mouse reset actions.
    timeline = find('mediaTimeline')
    click(timeline)
    wait(app, lambda: abs(playback.position - playback.duration/2) < 100, 'timeline click')
    drag(timeline, timeline.width()*.5, 35, timeline.width()*.2, 35)
    wait(app, lambda: abs(playback.position - playback.duration*.2) < 100, 'timeline drag')
    focus_workspace(); playback.seek(1000)
    wait(app, lambda: playback.position == 1000, 'CFR target')
    wait(app, lambda: playback.exactStepAvailable, "bounded local proof after seek")
    sink = find('viewerVideoOutput').property('videoSink')
    before_frame = round(sink.videoFrame().startTime() * 25 / 1000000)
    wait(app, lambda: playback.exactStepAvailable, 'exact capability')
    QTest.keyClick(window, Qt.Key_Right, Qt.ShiftModifier)
    wait(app, lambda: playback._step_pending is None, 'CFR next frame settled')
    assert round(sink.videoFrame().startTime() * 25 / 1000000) == before_frame + 1
    QTest.keyClick(window, Qt.Key_Left, Qt.ShiftModifier)
    wait(app, lambda: playback._step_pending is None, 'CFR previous frame settled')
    assert round(sink.videoFrame().startTime() * 25 / 1000000) == before_frame
    playback.seek(1000)
    wait(app, lambda: playback.position == 1000, 'seconds clamp starting point')
    QTest.keyClick(window, Qt.Key_Right)
    wait(app, lambda: playback.position == playback.duration, '+5 clamp')
    QTest.keyClick(window, Qt.Key_Left); QTest.keyClick(window, Qt.Key_Left)
    wait(app, lambda: playback.position == 0, '-5 clamp')
    QTest.keyClick(window, Qt.Key_Space)
    wait(app, lambda: playback.playing, 'Space play')
    QTest.keyClick(window, Qt.Key_Space)
    wait(app, lambda: playback.state == 'Paused', 'Space pause')
    model.selectOperation('trim'); model.setOperationEnabled('trim', True)
    playback.seek(1000); wait(app, lambda: playback.position == 1000, 'In target')
    QTest.keyClick(window, Qt.Key_I); assert model.editState['trimIn'] == 1000
    click(find('setTrimInButton'), Qt.RightButton); assert model.editState['trimIn'] == 0
    playback.seek(4000); wait(app, lambda: playback.position == 4000, 'Out target')
    focus_workspace(); QTest.keyClick(window, Qt.Key_O); assert model.editState['trimOut'] == 4000
    click(find('setTrimOutButton'), Qt.RightButton); assert model.editState['trimOut'] == model.editState['duration']
    assert model.setTrimRange(1000, 4000)
    # Handle dragging changes shared Inspector state, without crossing/swapping.
    drag(timeline, timeline.width()/6, 35, timeline.width()/4, 35)
    assert 1450 <= model.editState['trimIn'] <= 1550
    drag(timeline, timeline.width()*2/3, 35, timeline.width()*.75, 35)
    assert 4450 <= model.editState['trimOut'] <= 4550
    assert find('trimInInput').property('text').startswith('00:01.5')
    assert not model.setTrimRange(5000, 4500)
    # A/B/C must be in the real tray for the approved captures.
    drop(args.portrait); second_id = model.activeMediaId
    decode('portrait', args.portrait)
    model.setOperationEnabled('trim', True)
    assert model.setTrimRange(500, 3000)
    model.setOperationEnabled('resize', True); assert model.setResizeSize(720,1280)
    drop(args.cfr); third_id = model.activeMediaId
    decode('fractional-cfr', args.cfr)
    assert playback.framesReliable
    focus_workspace(); playback.seek(0)
    wait(app, lambda: sink.videoFrame().startTime() == 0, 'fractional first displayed frame')
    wait(app, lambda: playback.exactStepAvailable, 'exact capability')
    QTest.keyClick(window, Qt.Key_Right, Qt.ShiftModifier)
    wait(app, lambda: playback._step_pending is None, 'fractional adjacent frame settled')
    assert abs(sink.videoFrame().startTime() - 1001000 / 24) <= 2
    assert playback.frameText.startswith('Frame 2 / ')
    assert model.selectMedia(first_id); ready(); decode('landscape-restored', args.landscape)
    assert model.editState['trimIn'] >= 1450 and not model.editState['resizeEnabled']
    assert model.setTrimRange(1000, 4000)
    playback.seek(2000); wait(app, lambda: playback.position == 2000, 'capture position')
    model.selectOperation('trim')
    capture_state('01-media-trim')

    def check_crop(width, height):
        model.selectOperation('crop'); model.setOperationEnabled('crop', True)
        assert model.setCropRect(20,20,width,height)
        QTest.qWait(230)
        overlay, output = find('cropOverlay'), find('viewerVideoOutput')
        displayed = overlay.property('displayed')
        actual = output.property('contentRect')
        assert abs(displayed.width() - actual.width()) < .01
        assert abs(displayed.height() - actual.height()) < .01
        assert displayed.x() >= 18 and displayed.y() >= 18
        rect = find('cropOverlayRect')
        scale_x, scale_y = displayed.width()/model.editState['sourceWidth'], displayed.height()/model.editState['sourceHeight']
        assert abs(rect.x() - displayed.x() - 20*scale_x) < .1
        assert abs(rect.y() - displayed.y() - 20*scale_y) < .1
        before = model.editState.copy()
        drag(rect, rect.width()/2,rect.height()/2,rect.width()/2+13,rect.height()/2+11)
        assert model.editState['cropX'] != before['cropX'] or model.editState['cropY'] != before['cropY']
        assert model.editState['cropWidth'] == width and model.editState['cropHeight'] == height
        before = model.editState.copy()
        drag(rect, rect.width()-2,rect.height()/2,rect.width()+13,rect.height()/2)
        assert model.editState['cropWidth'] > width and model.editState['cropX'] == before['cropX']
        before = model.editState.copy()
        drag(rect,rect.width()-2,rect.height()-2,rect.width()-15,rect.height()-15)
        assert model.editState['cropWidth'] < before['cropWidth'] and model.editState['cropHeight'] < before['cropHeight']
        assert all(model.editState[key]%2 == 0 for key in ('cropX','cropY','cropWidth','cropHeight'))
        # Typed odd values are visibly rejected with no overlay/session mutation.
        editor = find('cropXInput'); before = model.editState.copy()
        click(editor); editor.setProperty('text','1'); QTest.keyClick(window,Qt.Key_Return)
        assert editor.property('invalid') and model.editState == before
        editor.setProperty('text',str(before['cropX'])); QTest.keyClick(window,Qt.Key_Return)
        assert not editor.property('invalid')
        focus_workspace()
        # Letterbox points do not move/resize the rectangle.
        if displayed.x() > 25:
            before = model.editState.copy()
            p = point(overlay, 10, overlay.height()/2)
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,p)
            assert model.editState == before
        return {'content_rect': [displayed.x(),displayed.y(),displayed.width(),displayed.height()],
                'geometry': {key:model.editState[key] for key in ('cropX','cropY','cropWidth','cropHeight')}}

    evidence['landscape_crop'] = check_crop(1200,600)
    assert model.setCropRect(320,180,1280,640)
    capture_state('02-media-crop-edit')
    concrete = playback._player; position = playback.position; geometry = model.editState.copy()
    click(find('cropPreviewToggle'))
    assert model.editState['cropPreview'] and playback._player is concrete and playback.position == position
    assert not find('cropOverlay').isVisible()
    viewport = find('videoViewport')
    assert abs(viewport.width()/viewport.height() - 2) < .01
    focus_workspace()
    capture_state('03-media-crop-preview')
    # Playback continues on the exact same backend while Preview is ON.
    playback.togglePlayback(); wait(app, lambda: playback.position > position+100, 'live cropped playback')
    playback.togglePlayback(); wait(app, lambda: playback.state == 'Paused', 'cropped pause')
    position = playback.position
    click(find('cropPreviewToggle'))
    assert playback._player is concrete and playback.position == position
    assert all(model.editState[k] == geometry[k] for k in ('cropX','cropY','cropWidth','cropHeight'))
    assert find('cropOverlay').isVisible()
    model.selectOperation('resize'); model.setOperationEnabled('resize', True)
    assert model.setResizeSize(1024,768)
    assert find('keepAspectRatioControl').property('enabled')
    assert not find('keepAspectRatioControl').property('checked')
    capture_state('04-media-resize')
    assert model.selectMedia(second_id); ready(); decode('portrait-restored',args.portrait)
    assert model.editState['trimIn'] == 500 and model.editState['resizeWidth'] == 720
    evidence['portrait_crop'] = check_crop(240,400)
    assert model.setCropRect(20,20,240,400)
    concrete = playback._player; position = playback.position
    click(find('cropPreviewToggle')); assert playback._player is concrete and playback.position == position
    assert abs(find('videoViewport').width()/find('videoViewport').height() - .6) < .01
    click(find('cropPreviewToggle')); assert playback._player is concrete and playback.position == position

    # Repeated sequential replacement proves old destruction at the factory instant.
    for index in range(18):
        assert model.selectMedia((first_id,second_id,third_id)[index%3]); ready()
    assert model.selectMedia(first_id); ready(); decode('final-landscape',args.landscape)
    assert model.editState['resizeWidth'] == 1024 and model.editState['cropWidth'] == 1280
    model.selectOperation('resize')
    # Context actions operate on their target, including disabled boundaries.
    tray = find('mediaTray')
    invoke(tray,'openActiveContext')
    assert not find('contextMoveleft').property('enabled')
    assert find('contextMoveright').property('enabled')
    capture_state('13-media-context-menu')
    click(find('contextDuplicate'))
    assert len(model.mediaItems) == 4
    clone_id = model.mediaItems[1]['id']; current = playback._player
    assert model.selectMedia(clone_id) and playback._player is current
    assert model.editState['resizeWidth'] == 1024
    assert model.moveMedia(clone_id,-1) and model.mediaItems[0]['id'] == clone_id
    assert model.removeMedia(clone_id) and args.landscape.exists()
    assert model.activeMediaId == first_id
    focus_workspace(); QTest.keyClick(window,Qt.Key_Delete); assert len(model.mediaItems) == 3
    tray.forceActiveFocus()
    QTest.keyClick(window,Qt.Key_Delete); ready()
    assert len(model.mediaItems) == 2 and args.landscape.exists()
    # A previously removed source is probed again when re-added.
    assert model.openMedia(QUrl.fromLocalFile(str(args.landscape.resolve())))
    ready(); decode('fullscreen-landscape',args.landscape)
    current = playback._player
    probes = len(metadata_calls)
    assert model.openMedia(QUrl.fromLocalFile(str(args.landscape.resolve())))
    assert playback._player is current and len(metadata_calls) == probes
    for maximized in (False, True):
        if maximized: window.showMaximized()
        else: window.showNormal()
        QTest.qWait(100)
        for _ in range(5):
            invoke(window,'enterViewerFullscreen')
            assert playback._player is current and window.visibility() == QWindow.FullScreen
            QTest.qWait(180)  # Native fullscreen resize/layout completes after the visibility signal.
            full = find('fullscreenTimeline'); click(full)
            assert full.isVisible()
            wait(app,lambda: abs(playback.position - playback.duration/2)<100,'fullscreen seek')
            QTest.keyClick(window,Qt.Key_Escape); QTest.qWait(30)
            assert not playback.fullscreen and playback._player is current
            assert window.visibility() == (QWindow.Maximized if maximized else QWindow.Windowed)
            evidence['fullscreen_cycles'] += 1
    window.showNormal(); resize_native_window(window, 1440,900)
    invoke(window,'enterViewerFullscreen')
    QTest.qWait(180)
    QTest.mouseMove(window, QPoint(window.width()//2,window.height()//2), 40)
    wait(app,lambda: not playback.controlsVisible,'fullscreen idle',timeout=5)
    QTest.mouseMove(window, QPoint(window.width()//2,window.height()-15), 40)
    wait(app,lambda: playback.controlsVisible,'fullscreen bottom zone')
    playback.beginControlsInteraction(); QTest.qWait(2200); assert playback.controlsVisible
    playback.endControlsInteraction()
    # Reference viewport capture keeps the actual fullscreen composition at 1440x900.
    playback.beginControlsInteraction()
    window.showNormal(); resize_native_window(window, 1440,900)
    wait(app,lambda: find('viewerFullscreenLayer').height() == 900,'reference fullscreen layout')
    QTest.mouseMove(window, QPoint(window.width()//2,window.height()-15), 40)
    wait(app,lambda: playback.controlsVisible,'reference bottom zone')
    # This deliberate 1440x900 reference composition is windowed; the native
    # bottom-zone/grab contract is separately tested in actual OS fullscreen.
    playback.setControlsZone(True)
    capture_state('12-fullscreen-viewer-controls')
    playback.endControlsInteraction()
    click(find('exitFullscreenButton')); assert not playback.fullscreen and playback._player is current
    window.showNormal(); resize_native_window(window, 1440,900)
    playback.setVolume(42); click(find('muteButton')); assert playback.muted and playback.volume == 0
    assert find('volumeSlider').property('value') == 0
    click(find('muteButton')); assert not playback.muted and playback.volume == 42
    assert 88 <= find('volumeSlider').width() <= 104
    for label, path in (('spaces',args.spaces),('unicode',args.unicode),('vfr',args.vfr)):
        assert model.openMedia(QUrl.fromLocalFile(str(path.resolve())))
        ready(); decode(label,path)
        if label == 'vfr':
            assert playback.framesReliable and find('frameReliabilityText').property('text') == playback.frameText
            before = playback._current_index; focus_workspace(); QTest.keyClick(window,Qt.Key_Right,Qt.ShiftModifier)
            wait(app, lambda: playback._step_pending is None, 'VFR indexed adjacent decoded picture')
            assert playback._current_index == before + 1 and playback.frameText == f'Frame {before+2} / {playback._timeline.count}'
    assert not processing_calls
    evidence['processing_calls'] = len(processing_calls)
    evidence['metadata_calls'] = len(metadata_calls)
    evidence['same_player_preview'] = True
    evidence['same_player_fullscreen'] = True
    evidence['native_drop_feedback_and_rejection'] = True
    evidence['cfr_vfr_behavior'] = True
    evidence['session_state_independent'] = True
    # Removal touches no source bytes and the final item returns to Home.
    paths = [args.landscape,args.portrait,args.cfr,args.spaces,args.unicode,args.vfr]
    sizes = [p.stat().st_size for p in paths]
    for row in list(model.mediaItems): assert model.removeMedia(row['id'])
    assert model.state == 'empty' and not model.mediaItems
    assert [p.stat().st_size for p in paths] == sizes
    evidence['remove_last_to_home'] = True
    assert not warnings, warnings
    return evidence


if __name__ == '__main__':
    main()
