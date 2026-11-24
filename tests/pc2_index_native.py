"""Native owned full-index cancellation, stale generations and changed-source reuse."""
import argparse
from array import array
import json
import logging
from pathlib import Path
import shutil
import time
from fractions import Fraction
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
import shiboken6
from native_window_policy import (load_native_shell as load_shell, resize_native_window, close_native_windows)
from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.resources.paths import qml_root
from mediaforge.application.contracts.source_timeline import TimelineResult,SourcePresentationTimeline
from native_workspace_regressions import wait,find,open_source,activate_native_input


def run(fixtures,output):
    output.mkdir(parents=True,exist_ok=True)
    app=QGuiApplication([]);engine=QQmlApplicationEngine()
    model=build_media_view_model(logging.getLogger('pc2-index'))
    engine.rootContext().setContextProperty('mediaViewModel',model)
    window=load_shell(engine,qml_root(),logging.getLogger('pc2-index'))
    playback=model.playback;service=playback._timeline_service;indexer=service._indexer
    factory=indexer._factory;children=[];evidence={};settlement=None
    def spawn(command,**options):
        process=factory(command,**options);children.append((command,process));return process
    indexer._factory=spawn
    mutable=output/'mutable source Кириллица.mp4'
    def start_scan(path):
        assert model.openMedia(QUrl.fromLocalFile(str(path)))
        wait(app,lambda:model.state=='loaded' and model.canOpen and playback.canPlay
             and playback._indexing and service._current is not None and service._current.owns_process,
             'actual owned full scan with paused first picture',timeout=20)
        control=service._current
        with control._lock:process=control._process
        assert process is not None and process.poll() is None
        return control,process
    try:
        resize_native_window(window, 1440,900)
        shutil.copyfile(fixtures/'practical.mp4',mutable)
        control,process=start_scan(mutable)
        request=service._current_request;source_generation=playback._source_generation
        index_generation=playback._timeline_generation;old=playback._player
        assert playback.frameText=='Indexing frames…' and playback.state=='Paused'
        assert not playback.framesReliable and not playback.totalFrameCountReliable
        assert find(window,'viewerVideoOutput').property('videoSink').videoFrame().isValid()
        playback.togglePlayback();assert playback.playing
        playback.seek(2020);assert playback.playing and not playback.exactStepAvailable
        playback.togglePlayback();assert playback.state=='Paused'
        before=time.perf_counter();assert model.removeMedia(model.activeMediaId)
        wait(app,lambda:process.poll() is not None and not control.owns_process,'removed-source process reaped')
        evidence['remove_cancel_ms']=(time.perf_counter()-before)*1000
        assert model.state=='empty' and not shiboken6.isValid(old)
        settlement=open_source(app,window,model,fixtures/'integral.mp4',rate=Fraction(25))
        timeline=playback._timeline;label=playback.frameText;current=playback._player
        stale=TimelineResult(request,SourcePresentationTimeline.from_timestamps(array('q',[0,40000]),40000))
        playback._on_timeline_result(stale,source_generation,index_generation)
        assert playback._timeline is timeline and playback.frameText==label and playback._player is current
        evidence['stale_removed_generation_ignored']=True
        settlement.close();settlement=None
        # Same pathname, independently changed bytes. A retained index is never
        # reused unless canonical path, size AND mtime_ns still match.
        shutil.copyfile(fixtures/'integral.mp4',mutable)
        settlement=open_source(app,window,model,mutable,rate=Fraction(25))
        mutable_id=model.activeMediaId;original_timeline=playback._timeline
        assert original_timeline.count==100
        settlement.close();settlement=None
        settlement=open_source(app,window,model,fixtures/'late-vfr.mp4',rate=Fraction(25))
        settlement.close();settlement=None
        shutil.copyfile(fixtures/'variable.mp4',mutable)
        assert model.selectMedia(mutable_id)
        wait(app,lambda:playback.exactStepAvailable,'changed retained source indexed')
        assert playback._timeline is not original_timeline and playback._timeline.count==60
        assert playback.frameText=='Frame 1 / 60' and playback.state=='Paused'
        old=playback._player
        shutil.copyfile(fixtures/'integral.mp4',mutable)
        assert model.selectMedia(mutable_id)  # Also validate already-active retained source.
        wait(app,lambda:playback.exactStepAvailable,'changed active source indexed')
        assert not shiboken6.isValid(old) and playback._timeline.count==100
        assert playback.frameText=='Frame 1 / 100'
        evidence['changed_retained_and_active_source_reindexed']=True
        current=playback._player;timeline=playback._timeline;launches=len(children)
        assert model.duplicateMedia(mutable_id)
        clone=next(item for item in model._workspace.items if item.id!=mutable_id and item.canonical_path==model._workspace.active.canonical_path)
        assert model.selectMedia(clone.id)
        assert playback._player is current and playback._timeline is timeline and len(children)==launches
        evidence['same_source_clone_shares_immutable_index_and_player']=True
        final=output/'shutdown source Кириллица.mp4';shutil.copyfile(fixtures/'practical.mp4',final)
        control,process=start_scan(final)
        current=playback._player;staging=playback._staging_sink
        assert len(playback.findChildren(QMediaPlayer))==len(playback.findChildren(QVideoSink))==1
        before=time.perf_counter();model.shutdown()
        evidence['shutdown_cancel_ms']=(time.perf_counter()-before)*1000
        assert process.poll() is not None and not control.owns_process and playback.state=='Empty'
        assert evidence['shutdown_cancel_ms']<6000
        playback._on_timeline_result(stale,source_generation,index_generation)
        assert playback.state=='Empty' and playback._timeline is None
        assert not playback.findChildren(QMediaPlayer) and not playback.findChildren(QVideoSink)
        assert not shiboken6.isValid(current) and not shiboken6.isValid(staging)
        evidence['shutdown_player_and_staging_disposed']=True
        evidence['children']=[{'command':command,'pid':child.pid,'exit':child.returncode} for command,child in children]
        assert all(child.poll() is not None for _,child in children)
        (output/'index-ownership.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    finally:
        if settlement:settlement.close()
        model.shutdown();close_native_windows(window);shiboken6.delete(engine)
        shiboken6.delete(model.playback._audio)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--fixtures',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    run(args.fixtures.resolve(),args.output.resolve())
