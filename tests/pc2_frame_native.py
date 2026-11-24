"""PC2: native keypress to actual decoded/presentation/ordinal latency gate."""
import argparse
from bisect import bisect_left
import hashlib
import json
import logging
import math
from pathlib import Path
import statistics
import subprocess
import time
from fractions import Fraction

from PySide6.QtCore import QUrl,Qt
from PySide6.QtGui import QGuiApplication,QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest,QSignalSpy
import shiboken6

from native_window_policy import (load_native_shell as load_shell, resize_native_window, close_native_windows)
from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.resources.paths import qml_root
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
from native_workspace_regressions import wait,find,DecodedFrameSettlement,activate_native_input
from native_shortcut_admission import NavigationRequestProbe, admit_adjacent_shortcut


def frame_hash(frame):
    image=frame.toImage().convertToFormat(QImage.Format_RGBA8888)
    assert not image.isNull()
    return hashlib.sha256(bytes(image.constBits())).hexdigest()


def run(fixtures,output):
    output.mkdir(parents=True,exist_ok=True)
    runtime=resolve_ffmpeg_runtime()
    app=QGuiApplication([])
    engine=QQmlApplicationEngine()
    model=build_media_view_model(logging.getLogger('pc2-native'))
    engine.rootContext().setContextProperty('mediaViewModel',model)
    window=load_shell(engine,qml_root(),logging.getLogger('pc2-native'))
    playback=model.playback
    render_swaps=QSignalSpy(window.frameSwapped)
    evidence={'platform':'Windows','qt_backend':'Qt Multimedia FFmpeg/default Windows hardware decoding',
              'dpr':window.devicePixelRatio(),'fixtures':[],'index_commands':[]}
    indexer=playback._timeline_service._indexer
    factory=indexer._factory
    def spawned(command,**options):
        evidence['index_commands'].append(command)
        return factory(command,**options)
    indexer._factory=spawned
    shot_requested=False
    def indexing_capture():
        nonlocal shot_requested
        if not shot_requested and model.state=='loaded' and playback.frameText=='Indexing frames…':
            shot_requested=True
            assert window.grabWindow().save(str(output/'frame-indexing.png'))
    window.afterRendering.connect(indexing_capture,Qt.QueuedConnection)
    failed_gate=[]
    try:
        resize_native_window(window, 1440,900)
        activate_native_input(window)
        for name in ('integral.mp4','fractional.mp4','long60.mp4','practical.mp4','variable.mp4','late-vfr.mp4'):
            path=fixtures/name
            result=subprocess.run([str(runtime.ffprobe),'-v','error','-select_streams','v:0',
                '-show_frames','-show_entries','frame=best_effort_timestamp_time:frame_side_data=',
                '-of','json',str(path)],capture_output=True,text=True,encoding='utf-8',timeout=300)
            assert result.returncode==0,result.stderr
            # Test-only independent frame-time query/parser; no product timeline
            # object, rate, requested position or UI text supplies the oracle.
            oracle=[round(Fraction(f['best_effort_timestamp_time'])*1_000_000)
                    for f in json.loads(result.stdout)['frames']]
            assert oracle and all(a<b for a,b in zip(oracle,oracle[1:]))
            assert model.openMedia(QUrl.fromLocalFile(str(path.resolve())))
            wait(app,lambda:model.state=='loaded' and playback.exactStepAvailable,'complete index/current frame',timeout=310)
            assert playback._timeline.count==len(oracle)
            assert playback._timeline.storage_bytes==len(oracle)*8
            assert list(playback._timeline.timestamps)==oracle
            assert len(playback.findChildren(QMediaPlayer))==1
            sink=find(window,'viewerVideoOutput').property('videoSink')
            def expected_index(frame):
                position=bisect_left(oracle,frame.startTime())
                candidates=[index for index in (position-1,position) if 0<=index<len(oracle)
                    and abs(oracle[index]-frame.startTime())<=2]
                assert len(candidates)==1,(frame.startTime(),candidates)
                return candidates[0]
            settlement=DecodedFrameSettlement(sink,playback,
                lambda frame:f'Frame {expected_index(frame)+1} / {len(oracle)}')
            requests=NavigationRequestProbe(playback)
            item={'name':name,'count':len(oracle),'storage_bytes':playback._timeline.storage_bytes,
                  'steps':[],'seek_observations':[],'dispatch_attempts':[]}
            evidence['fixtures'].append(item)
            try:
                first=settlement.wait_for('indexed first picture',lambda f:expected_index(f)==0)
                assert playback.state=='Paused' and first.startTime()==0
                if name in ('integral.mp4','variable.mp4'):
                    window.grabWindow().save(str(output/('frame-n-total-cfr.png' if name=='integral.mp4' else 'frame-n-total-vfr.png')))
                # Every fixture also exercises ordinary playback. Record only
                # coherent actual sink/Presentation observations and validate
                # each label against the independent full frame-time query.
                played=[];wrong=[]
                def playing_observation():
                    actual=sink.videoFrame()
                    if (actual.isValid() and playback._presented_start==actual.startTime()
                            and playback.framesReliable):
                        ordinal=expected_index(actual)
                        label=f'Frame {ordinal+1} / {len(oracle)}'
                        if playback.frameText!=label:wrong.append((actual.startTime(),playback.frameText,label))
                        if not played or played[-1]['timestamp_us']!=actual.startTime():
                            played.append({'timestamp_us':actual.startTime(),'label':playback.frameText})
                playback.changed.connect(playing_observation)
                try:
                    playback.togglePlayback()
                    wait(app,lambda:len(played)>=4,'ordinary playback decoded ordinal updates')
                    playback.togglePlayback()
                    assert not wrong and playback.state=='Paused',wrong
                    item['ordinary_playback_observations']=played
                finally:playback.changed.disconnect(playing_observation)
                def seek_index(index):
                    start=oracle[index]
                    end=oracle[index+1] if index+1<len(oracle) else start+playback._timeline.last_duration_us
                    revision=settlement.revision
                    playback.seek((start+end)//2000)
                    assert not playback.exactStepAvailable
                    frame=settlement.wait_for('arbitrary seek actual ordinal',lambda f:expected_index(f)==index,after_revision=revision)
                    item['seek_observations'].append({'index':index,'actual_us':frame.startTime(),'label':playback.frameText})
                    return frame
                # Four independently settled regions; exactly 100 intended
                # adjacent keypresses. No warm-up step is discarded.
                for anchor in (2,len(oracle)//3,len(oracle)//2,len(oracle)-16):
                    anchor=max(1,min(len(oracle)-15,anchor))
                    frame=seek_index(anchor)
                    for turn in range(25):
                        direction=1 if turn<13 else -1
                        current=expected_index(frame);target=current+direction
                        before_hash=frame_hash(frame)
                        revision=settlement.revision
                        launches=len(evidence['index_commands'])
                        admission=admit_adjacent_shortcut(window,model)
                        before_requests=len(requests.requests)
                        attempt={'from':current,'to':target,'admission':admission,
                                 'request_count_before':before_requests,'revision_before':revision}
                        item['dispatch_attempts'].append(attempt)
                        started=time.perf_counter()
                        QTest.keyClick(window,Qt.Key_Right if direction>0 else Qt.Key_Left,Qt.ShiftModifier)
                        attempt['request_count_after']=len(requests.requests)
                        attempt['request_serial_after']=playback._step_serial
                        attempt['requests']=requests.require_delta(before_requests)
                        frame=settlement.wait_for('adjacent decoded target',
                            lambda f:expected_index(f)==target and playback._step_pending is None,
                            after_revision=revision)
                        assert expected_index(frame)==target
                        assert frame_hash(frame)!=before_hash
                        assert playback.frameText==f'Frame {target+1} / {len(oracle)}'
                        assert len(evidence['index_commands'])==launches,'Per-keypress FFprobe prohibited'
                        # Include an actual native scene-graph swap after decoded
                        # settlement. Sink delivery alone is not the latency end.
                        settled_swap=render_swaps.count()
                        window.update()
                        wait(app,lambda:render_swaps.count()>settled_swap,'target presentation render swap')
                        assert sink.videoFrame()==frame and playback._presented_start==frame.startTime()
                        elapsed=(time.perf_counter()-started)*1000
                        item['steps'].append({'from':current,'to':target,'timestamp_us':frame.startTime(),
                            'label':playback.frameText,'latency_ms':elapsed,
                            'decoded_hash':frame_hash(frame),'revision_after':settlement.revision,
                            'render_swap':render_swaps.count()})
                latencies=[step['latency_ms'] for step in item['steps']]
                assert len(latencies)==100
                item['median_ms']=statistics.median(latencies)
                item['p95_ms']=sorted(latencies)[math.ceil(.95*len(latencies))-1]
                item['min_ms']=min(latencies);item['max_ms']=max(latencies)
                if item['median_ms']>150 or item['p95_ms']>250:
                    failed_gate.append(name)
                    evidence['failed_latency_gate']=failed_gate
                    (output/'latency.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
                    raise AssertionError(('BLOCKED: indexed Qt seek/decode latency exceeds acceptance',name,
                                          item['median_ms'],item['p95_ms']))
                # Actual first/last clamps retain the decoded picture, not clock.
                for index,direction in ((0,-1),(len(oracle)-1,1)):
                    frame=seek_index(index);before=frame_hash(frame)
                    admit_adjacent_shortcut(window,model)
                    count=len(requests.requests)
                    QTest.keyClick(window,Qt.Key_Left if direction<0 else Qt.Key_Right,Qt.ShiftModifier)
                    requests.require_delta(count,0)
                    assert expected_index(sink.videoFrame())==index and frame_hash(sink.videoFrame())==before
                if name=='late-vfr.mp4':
                    frame=seek_index(160)
                    assert frame.startTime()==6440000 and playback.frameText=='Frame 161 / 300'
                    revision=settlement.revision
                    admit_adjacent_shortcut(window,model)
                    count=len(requests.requests)
                    QTest.keyClick(window,Qt.Key_Right,Qt.ShiftModifier)
                    requests.require_delta(count)
                    frame=settlement.wait_for('late shifted adjacent identity',lambda f:expected_index(f)==161,after_revision=revision)
                    assert frame.startTime()==6480000 and playback.frameText=='Frame 162 / 300'
                (output/'latency.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
                print(name,'100 steps median=',item['median_ms'],'p95=',item['p95_ms'],flush=True)
            finally:
                requests.close()
                settlement.close()
        evidence['failed_latency_gate']=failed_gate
        (output/'latency.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        assert not failed_gate,('BLOCKED: indexed Qt seek/decode latency exceeds acceptance',failed_gate)
    except BaseException:
        (output/'failed-latency.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        raise
    finally:
        model.shutdown();close_native_windows(window);shiboken6.delete(engine)
        shiboken6.delete(model.playback._audio)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--fixtures',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    run(args.fixtures,args.output)
