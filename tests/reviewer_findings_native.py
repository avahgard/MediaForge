"""PC-F1/2/4: independent actual picture, paint and wheel-input observations."""
from fractions import Fraction
import hashlib
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QWheelEvent
from PySide6.QtTest import QTest
from native_window_policy import resize_native_window
from native_workspace_regressions import find, wait, open_source, activate_native_input


def digest(frame):
    image = frame.toImage().convertToFormat(QImage.Format_RGBA8888)
    assert not image.isNull()
    return hashlib.sha256(bytes(image.constBits())).hexdigest()


def late_timing_checks(app, window, model, fixtures, output, initial):
    # PC2 supersedes the local-only contract. Keep the adversarial picture oracle
    # and verify exact global labels through the complete frame-level timeline.
    playback=model.playback;initial.close();settlement=None;observations=[]
    try:
        pictures=[]
        for source,targets in (('cfr-control.mp4',(6420,6460)),('late-vfr.mp4',(6460,6500))):
            if settlement:settlement.close()
            settlement=open_source(app,window,model,fixtures/source,rate=Fraction(25))
            source_pictures=[]
            for index,target in enumerate(targets,160):
                revision=settlement.revision;playback.seek(target)
                assert not playback.exactStepAvailable
                frame=settlement.wait_for('new seek decoded ordinal',lambda f:f.startTime()<=target*1000<f.endTime(),after_revision=revision)
                assert playback.framesReliable and playback.frameText==f'Frame {index+1} / 300'
                source_pictures.append(digest(frame));observations.append({'source':source,'timestamp_us':frame.startTime(),'label':playback.frameText})
            pictures.append(source_pictures)
        assert pictures[0]==pictures[1]
        playback.seek(6460)
        frame=settlement.wait_for('shifted picture160',lambda f:f.startTime()==6440000)
        assert digest(frame)==pictures[0][0] and playback.frameText=='Frame 161 / 300'
        revision=settlement.revision;activate_native_input(window)
        QTest.keyClick(window,Qt.Key_Right,Qt.ShiftModifier)
        frame=settlement.wait_for('shifted adjacent picture161',lambda f:playback._step_pending is None,after_revision=revision)
        assert frame.startTime()==6480000 and digest(frame)==pictures[0][1]
        assert playback.frameText=='Frame 162 / 300' and playback.framesReliable
        playback.seek(9020);assert not playback.exactStepAvailable
        settlement.wait_for('later regular actual mapped picture',lambda f:f.startTime()==9000000)
        assert playback.framesReliable
        playback.seek(3980)
        settlement.wait_for('irregular current actual picture',lambda f:f.startTime()==3960000)
        revision=settlement.revision;playback.stepFrame(1)
        frame=settlement.wait_for('irregular cadence exact next',lambda f:playback._step_pending is None,after_revision=revision)
        assert frame.startTime()==4020000 and playback.frameText=='Frame 101 / 300'
        output['late_timing']=observations
    finally:
        if settlement:settlement.close()


def trim_render_checks(app, window, model, settlement, evidence_directory):
    playback = model.playback
    sink = find(window,'viewerVideoOutput').property('videoSink')
    video = find(window,'viewerVideoOutput')
    from native_trim_paint import CaptureStorageError, ScenePaintOracle
    oracle=ScenePaintOracle(window,video,playback,evidence_directory)
    reference={}
    for timestamp in range(360000,840001,40000):
        revision=settlement.revision
        playback.seek(timestamp//1000+20)
        settlement.wait_for('independent paused paint reference',lambda f:f.startTime()==timestamp,after_revision=revision)
        reference[oracle.capture('reference',timestamp)]=timestamp
    assert len(reference)==13, 'Paint references must distinguish every picture'
    in_range={key for key,value in reference.items() if 400000<=value<800000}
    model.setOperationEnabled('trim',True);model.setTrimRange(400,800)
    playback.seek(420)
    settlement.wait_for('in-range loop picture',lambda f:f.startTime()==400000)
    # This is the independent Play-inside precondition, not decoded readiness.
    wait(app,lambda:400<=playback.position<800,'Play eligibility clock inside Trim')
    rendered, accepted, failures, raw, painted = [], [], [], [], []
    grabbing=False
    last_painted=None
    capture_failure=None
    def check_capture_complete():
        # Qt consumes Python slot exceptions. Re-raise the first captured error
        # (including its traceback) from the enclosing Python acceptance path.
        if capture_failure is not None:
            raise capture_failure
        if oracle.storage_error is not None:
            raise CaptureStorageError(oracle.storage_error)
    def swapped():
        nonlocal grabbing,last_painted,capture_failure
        if grabbing:return
        frame=sink.videoFrame()
        if playback.trimLoopActive and playback.playing and frame.isValid():
            raw.append([frame.startTime(),playback._presented_start])
            if frame.startTime()==playback._presented_start:
                rendered.append(frame.startTime())
                if not 400000<=frame.startTime()<800000:
                    failures.append(('coherent rendered',frame.startTime(),playback.position))
            # Readbacks at every swap perturb real-time decoding. Inspect every
            # distinct excluded-boundary candidate and each actual In return;
            # all coherent swaps still retain the strict timestamp assertion.
            boundary = not 400000<=frame.startTime()<800000 or frame.startTime()==400000
            previous=last_painted
            last_painted=frame.startTime()
            if boundary and frame.startTime()!=previous:
                grabbing=True
                try:
                    actual=oracle.capture('boundary')
                    painted.append(reference.get(actual))
                    if actual not in in_range:
                        failures.append(('actual painted picture',reference.get(actual),actual,frame.startTime(),playback._presented_start))
                except Exception as error:
                    if capture_failure is None:
                        capture_failure=error
                        failures.append(('paint capture failed',repr(error)))
                finally:grabbing=False
    def presented():
        frame=sink.videoFrame()
        if playback.trimLoopActive and playback.playing and frame.isValid() and playback._presented_start==frame.startTime():
            accepted.append(frame.startTime())
            if not 400000 <= frame.startTime()<800000:
                failures.append(('presented',frame.startTime(),playback.position))
    window.frameSwapped.connect(swapped)
    playback.changed.connect(presented)
    try:
        playback.togglePlayback()
        check_capture_complete()
        assert playback.trimLoopActive and playback.playing
        try:
            # Decoded/presented callbacks can reach four loops before a coherent
            # scene swap. Require both existing observations at the barrier.
            wait(app,lambda:(sum(b<a for a,b in zip(accepted,accepted[1:]))>=4
                            and bool(rendered) and bool(painted)) or bool(failures),
                 'four presented Out-to-In loops and coherent paint')
        except AssertionError as error:
            check_capture_complete()
            raise AssertionError((str(error),playback.state,playback.position,playback.trimLoopActive,
                playback._loop_seeking,accepted,rendered,raw,painted)) from error
        check_capture_complete()
        assert not failures, failures
        assert rendered and min(rendered)>=400000 and max(rendered)<800000, {
            'rendered': rendered, 'accepted': accepted, 'raw': raw,
            'painted': painted, 'failures': failures,
        }
        assert playback.playing and playback.trimLoopActive
        assert painted and all(400000<=value<800000 for value in painted)
        check_capture_complete()
        return {'rendered_us':rendered,'presented_us':accepted,'painted_picture_us':painted,
            'raw_sink_at_swap_us':raw,'excluded_out_us':800000}
    finally:
        window.frameSwapped.disconnect(swapped)
        playback.changed.disconnect(presented)


def crop_wheel_fallback(app, window, model):
    previous_operation=model.selectedOperation
    previous_size=window.width(),window.height()
    previous_viewer_width=find(window,'windowedViewerHost').width()
    model.selectOperation('crop');resize_native_window(window, 800,560)
    width,height=find(window,'cropWInput'),find(window,'cropHInput')
    column=width.parentItem().parentItem().parentItem().parentItem()
    wait(app,lambda:column.opacity()==1 and column.x()==0 and find(window,'mediaInspector').height()==478,'visible minimum Crop inspector')
    view=find(window,'inspectorScroll').property('contentItem')
    assert view.property('contentHeight')>view.height()
    def point(item):
        p=item.mapToScene(QPointF(item.width()/2,item.height()/2))
        return QPoint(round(p.x()),round(p.y()))
    QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point(width))
    assert width.property('activeFocus') and not height.property('activeFocus')
    before=[width.property('text'),height.property('text'),view.property('contentY')]
    QTest.mouseMove(window,point(height));app.processEvents()
    event=QWheelEvent(QPointF(point(height)),QPointF(window.mapToGlobal(point(height))),
        QPoint(),QPoint(0,-120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
    window._native_input_clock.stamp(event)
    app.sendEvent(window,event)
    try:
        wait(app,lambda:view.property('contentY')>before[2],'real Inspector wheel fallback')
    except AssertionError as error:
        raise AssertionError((str(error), before, view.property('contentY'), view.height(),
            view.property('contentHeight'), point(height), width.property('activeFocus'),
            height.property('activeFocus'), column.x(), column.opacity())) from error
    assert before[:2]==[width.property('text'),height.property('text')]
    assert width.property('activeFocus') and not height.property('activeFocus')
    result={'before':before,'after_y':view.property('contentY'),'content_height':view.property('contentHeight'),'viewport':view.height()}
    find(window,'workspaceKeyboardFocus').forceActiveFocus()
    model.selectOperation(previous_operation)
    resize_native_window(window, *previous_size)
    if previous_size==(1440,900):
        wait(app,lambda:abs(find(window,'windowedViewerHost').width()-previous_viewer_width)<.1,'restored workspace composition')
    return result
