"""Windows decoded staging, admitted Viewer pixels and pipeline lifetime proof."""
import argparse
from fractions import Fraction
import json
import logging
from pathlib import Path
import time

import shiboken6
from PySide6.QtCore import QObject, QUrl, Signal, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame, QVideoSink, QAudioOutput
from PySide6.QtQml import QQmlApplicationEngine
from native_window_policy import (load_native_shell as load_shell, resize_native_window, close_native_windows)
from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.resources.paths import qml_root
from native_workspace_regressions import find, wait, open_source, activate_native_input
from native_phase_diagnostics import NativePhaseDiagnostics
from native_trim_paint import ScenePaintOracle


class PendingDelivery(QObject):
    frame = Signal(QVideoFrame)


def run(fixtures, output):
    output.mkdir(parents=True,exist_ok=True)
    diagnostics=NativePhaseDiagnostics(output/'diagnostics')
    app=QGuiApplication([]);engine=QQmlApplicationEngine()
    model=build_media_view_model(logging.getLogger('trim-admission'))
    p=model.playback
    engine.rootContext().setContextProperty('mediaViewModel',model)
    warnings=[];engine.warnings.connect(lambda values:warnings.extend(v.toString() for v in values))
    window=load_shell(engine,qml_root(),logging.getLogger('trim-admission'))
    diagnostics.bind(app=app,window=window,model=model,playback=p,service=p._timeline_service)
    video=find(window,'viewerVideoOutput');viewer=video.property('videoSink')
    settlement=None;phase='startup';sequence=0;raw=[];admitted=[];seeks=[];public=[];viewer_events=[]
    evidence={'phases':[],'replacements':[]}
    journal=(output/'events.jsonl').open('a',encoding='utf8',buffering=1)
    original_admit=p._admit_staged_frame;original_return=p._return_to_trim_in
    def record(event,**facts):
        nonlocal sequence
        sequence+=1
        row={'seq':sequence,'monotonic_ns':time.monotonic_ns(),'phase':phase,'event':event,
             'source_generation':p._source_generation,'player_generation':p._player_generation,
             'player_identity':id(p._player),'staging_identity':id(p._staging_sink),
             'viewer_identity':id(viewer),'range_ms':[p._trim_in,p._trim_out],
             'armed':p.trimLoopActive,'pending_return':p._loop_seeking,
             'presentation_us':p._presented_start,'frame_label':p.frameText,
             'position_ms':p.position,'playing':p.playing,**facts}
        journal.write(json.dumps(row)+'\n');return row
    def observe_admission(receiver,frame):
        start=frame.startTime() if frame.isValid() else None
        owner=receiver is p._admission_receiver and p._accept_frame_owner(receiver)
        decision=('DROP stale' if not owner else 'REJECT invalid' if not frame.isValid()
                  else 'REJECT unknown' if p.trimLoopActive and start<0
                  else 'REJECT range' if p.trimLoopActive and not p._trim_in*1000<=start<p._trim_out*1000
                  else 'ADMIT')
        row=record('staging-before-admission',timestamp_us=start,decision=decision,
                   captured_source_generation=receiver.source_generation,
                   captured_player_generation=receiver.player_generation,
                   captured_player_identity=id(receiver.player),captured_staging_identity=id(receiver.staging))
        raw.append(row)
        count=len(viewer_events);published=len(admitted)
        original_admit(receiver,frame)
        record('staging-after-admission',timestamp_us=start,decision=decision,
               viewer_events=len(viewer_events)-count,admitted_events=len(admitted)-published)
        if decision.startswith(('REJECT','DROP')):
            assert len(viewer_events)==count and len(admitted)==published
    def observe_return():
        pending=p._loop_seeking
        original_return()
        if not pending:seeks.append(record('owned-Out-to-In-seek'))
    def admitted_frame(event):
        admitted.append(record('AdmittedVideoFrame',timestamp_us=event.frame.startTime()))
        assert viewer.videoFrame()==event.frame and p._presented_start==event.frame.startTime()
        assert event.staging is p._staging_sink and event.player is p._player
        if p.trimLoopActive:
            assert p._trim_in*1000<=event.frame.startTime()<p._trim_out*1000
            assert p.frameText==f'Frame {event.frame.startTime()//40000+1} / 100'
    def viewer_frame(frame):
        row=record('Viewer-publication',timestamp_us=frame.startTime() if frame.isValid() else None)
        viewer_events.append(row)
        if p.trimLoopActive and frame.isValid():
            assert p._trim_in*1000<=frame.startTime()<p._trim_out*1000
    def changed():
        public.append(record('Presentation-change'))
        if p.trimLoopActive and p._presented_start is not None:
            assert p._trim_in*1000<=p._presented_start<p._trim_out*1000
    p._admit_staged_frame=observe_admission;p._return_to_trim_in=observe_return
    p.admittedVideoFrame.connect(admitted_frame);viewer.videoFrameChanged.connect(viewer_frame);p.changed.connect(changed)
    def paused():
        if p.playing:p.togglePlayback()
        wait(app,lambda:p.state=='Paused','paused inspection')
    def exact_seek(timestamp):
        revision=settlement.revision;p.seek(timestamp//1000+20)
        return settlement.wait_for('decoded picture '+str(timestamp),lambda f:f.startTime()==timestamp,after_revision=revision)
    def topology():
        assert p._player.videoSink() is p._staging_sink and p._staging_sink is not viewer
        assert p._presentation_sink is viewer and find(window,'viewerVideoOutput') is video
        assert len(p.findChildren(QMediaPlayer))==len(p.findChildren(QVideoSink))==len(p.findChildren(QAudioOutput))==1
        return p._player,p._staging_sink,p._audio,video,viewer
    def core(name,returns=4):
        nonlocal phase
        phase=name;paused();model.setOperationEnabled('trim',True);assert model.setTrimRange(400,800)
        oracle=ScenePaintOracle(window,video,p,output/name)
        for timestamp in range(360000,920001,40000):
            exact_seek(timestamp);oracle.capture('reference',timestamp)
        assert len(oracle.references)==15
        allowed={key for key,value in oracle.references.items() if 400000<=value<800000}
        exact_seek(400000);wait(app,lambda:400<=p.position<800,'Play inside current range')
        identities=topology();raw_start=len(raw);admit_start=len(admitted);seek_start=len(seeks)
        p.togglePlayback();assert p.playing and p.trimLoopActive
        painted=[]
        # Deliberately ask the real current decoder for Out while the loop is
        # armed. This is a native boundary/preroll transport probe, not a user
        # manual-navigation action and not a synthetic QVideoFrame injection.
        for turn in range(returns):
            before=len(raw);after=len(admitted);seek_before=len(seeks)
            p._player.setPosition(820)
            wait(app,lambda:any(r['decision']=='REJECT range' and r['timestamp_us']>=800000 for r in raw[before:]),
                 'actual excluded native staging delivery')
            digest=oracle.capture('withheld')
            painted.append(oracle.references.get(digest))
            assert digest in allowed,('actual painted picture',oracle.references.get(digest),digest)
            wait(app,lambda:any(400000<=r['timestamp_us']<500000 for r in admitted[after:]) and not p._loop_seeking,
                 'admitted near-In return acknowledgement')
            # At least one owned seek, one per pending interval. Every repeat
            # while pending is separately asserted in the focused regression.
            assert len(seeks)>seek_before
            assert topology()==identities and p.playing and p.trimLoopActive
        excluded=[r for r in raw[raw_start:] if r['decision']=='REJECT range']
        assert excluded and any(r['timestamp_us']>=800000 for r in excluded)
        assert all(400000<=r['timestamp_us']<800000 for r in admitted[admit_start:])
        evidence['phases'].append({'name':name,'excluded_decoded_deliveries':len(excluded),
            'admitted_frames':len(admitted)-admit_start,'owned_returns':len(seeks)-seek_start,
            'actual_painted_picture_us':painted,'identities_preserved':True})
        paused();return QVideoFrame(p._staging_sink.videoFrame())
    try:
        resize_native_window(window, 1440,900)
        with diagnostics.phase_scope('initial no-churn native admission'):
            settlement=open_source(app,window,model,fixtures/'integral.mp4',rate=Fraction(25))
            core('no-churn')
        with diagnostics.phase_scope('20 sequential accepted replacements'):
            phase='replacement'
            for turn in range(20):
                old=p._player;old_staging=p._staging_sink;stale=p._frame_handler
                old_frame=QVideoFrame(viewer.videoFrame());factory=p._player_factory
                def checked_factory():
                    assert not shiboken6.isValid(old) and not shiboken6.isValid(old_staging)
                    assert not p.findChildren(QMediaPlayer) and not p.findChildren(QVideoSink)
                    return factory()
                p._player_factory=checked_factory
                settlement.close()
                try:
                    source='fractional.mp4' if turn%2==0 else 'integral.mp4'
                    settlement=open_source(app,window,model,fixtures/source,rate=Fraction(24000,1001) if turn%2==0 else Fraction(25))
                finally:p._player_factory=factory
                before=(p._presented_start,p.frameText,len(viewer_events),len(seeks))
                stale(old_frame);app.processEvents()
                assert before==(p._presented_start,p.frameText,len(viewer_events),len(seeks))
                topology();assert viewer.videoFrame().startTime()==0 and p.state=='Paused'
                evidence['replacements'].append({'turn':turn,'retired_player_disposed':not shiboken6.isValid(old),
                    'retired_staging_disposed':not shiboken6.isValid(old_staging),'stale_callback_dropped':True})
            core('after-20-replacements')
        with diagnostics.phase_scope('fullscreen armed native admission'):
            before=topology();window.enterViewerFullscreen();wait(app,lambda:p.fullscreen,'fullscreen')
            core('fullscreen');window.exitViewerFullscreen();wait(app,lambda:not p.fullscreen,'windowed')
            assert topology()==before
        with diagnostics.phase_scope('Crop Preview OFF ON native admission'):
            model.selectOperation('crop');model.setOperationEnabled('crop',True)
            assert model.setCropRect(20,20,240,140)
            for enabled in (False,True):
                before=topology();model.setCropPreview(enabled)
                core('crop-preview-'+str(enabled));assert topology()==before
            model.setCropPreview(False);model.setOperationEnabled('crop',False);model.selectOperation('trim')
        with diagnostics.phase_scope('disable re-enable range edit manual escape'):
            phase='current-state'
            exact_seek(400000);model.setTrimRange(400,800);p.togglePlayback();assert p.trimLoopActive
            model.setOperationEnabled('trim',False);assert not p.trimLoopActive
            p.seek(1020);settlement.wait_for('disabled outside decoded picture',lambda f:f.startTime()==1000000)
            model.setOperationEnabled('trim',True);assert not p.trimLoopActive
            p.seek(1220);settlement.wait_for('re-enabled unarmed outside',lambda f:f.startTime()==1200000)
            paused();exact_seek(400000);p.togglePlayback();assert p.trimLoopActive
            assert model.setTrimRange(400,600)
            start=len(raw);p._player.setPosition(620)
            wait(app,lambda:any(r['decision']=='REJECT range' and r['timestamp_us']==600000 for r in raw[start:]),'current edited Out rejected')
            wait(app,lambda:not p._loop_seeking and p._presented_start==400000,'edited range returned')
            p.seek(1020);assert not p.trimLoopActive
            settlement.wait_for('manual outside seek allowed',lambda f:f.startTime()==1000000)
            paused();exact_seek(400000);p.togglePlayback();assert p.trimLoopActive
            p.stepFrame(-1);assert not p.trimLoopActive
            settlement.wait_for('manual previous escapes saved range',lambda f:f.startTime()==360000 and p._step_pending is None)
            assert p.state=='Paused';topology()
            evidence['current_state']={'disable_reenable_unarmed':True,'current_range':True,'manual_seek_escape':True,'manual_step_escape':True}
        with diagnostics.phase_scope('pending old staging callback replacement'):
            phase='pending-replacement';paused();exact_seek(840000)
            late=QVideoFrame(p._staging_sink.videoFrame());assert late.startTime()==840000
            old=p._player;old_staging=p._staging_sink;stale=p._frame_handler
            delivery=PendingDelivery();delivery.frame.connect(p._admission_receiver.receive,Qt.QueuedConnection)
            delivery.frame.emit(late)
            settlement.close();settlement=open_source(app,window,model,fixtures/'fractional.mp4',rate=Fraction(24000,1001))
            assert not shiboken6.isValid(old) and not shiboken6.isValid(old_staging)
            before=(p._presented_start,p.frameText,len(viewer_events),len(seeks))
            stale(late);app.processEvents()
            assert before==(p._presented_start,p.frameText,len(viewer_events),len(seeks))
            assert viewer.videoFrame().startTime()==0
            oracle=ScenePaintOracle(window,video,p,output/'pending-replacement')
            reference=oracle.capture('reference',0)
            assert oracle.capture('after-stale')==reference
            evidence['pending_replacement']={'late_us':late.startTime(),'current_first_picture_painted':True,'stale_dropped':True}
        with diagnostics.phase_scope('queued stale admission shutdown'):
            phase='shutdown';delivery=PendingDelivery();delivery.frame.connect(p._admission_receiver.receive,Qt.QueuedConnection)
            delivery.frame.emit(late);stale=p._frame_handler;old=p._player;staging=p._staging_sink
            settlement.close();settlement=None;p.shutdown()
            before=(p._presented_start,p.frameText,len(viewer_events),len(seeks),len(public))
            stale(late);app.processEvents()
            assert before==(p._presented_start,p.frameText,len(viewer_events),len(seeks),len(public))
            assert not viewer.videoFrame().isValid() and not shiboken6.isValid(old) and not shiboken6.isValid(staging)
            assert not p.findChildren(QMediaPlayer) and not p.findChildren(QVideoSink)
            evidence['shutdown']={'queued_stale_dropped':True,'no_seek_or_publication':True,'native_ownership_released':True}
        assert not warnings,warnings
        (output/'admission-matrix.json').write_text(json.dumps(evidence,indent=2),encoding='utf8')
    except BaseException as error:
        evidence['failure']=repr(error)
        (output/'failed.json').write_text(json.dumps(evidence,indent=2),encoding='utf8');raise
    finally:
        if settlement:settlement.close()
        p._admit_staged_frame=original_admit;p._return_to_trim_in=original_return
        p.admittedVideoFrame.disconnect(admitted_frame);viewer.videoFrameChanged.disconnect(viewer_frame);p.changed.disconnect(changed)
        model.shutdown();close_native_windows(window);shiboken6.delete(engine)
        shiboken6.delete(model.playback._audio)
        journal.close();diagnostics.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--fixtures',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    run(args.fixtures.resolve(),args.output.resolve())
