"""Pre-publication admission and immutable pipeline ownership regressions."""
import threading
from array import array
from pathlib import Path

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal
from PySide6.QtMultimedia import QVideoFrame, QVideoSink
from PySide6.QtTest import QTest
from mediaforge.application.contracts.source_timeline import SourcePresentationTimeline
from test_playback_controller import controller, presented_frame


@pytest.fixture
def admitted(tmp_path):
    app = QCoreApplication.instance() or QCoreApplication([])
    view, player, audio = controller()
    renderer = QVideoSink()
    view.attachVideoSink(renderer)
    view.set_source(str(tmp_path / 'admission Unicode Видео.mp4'))
    player.durationChanged.emit(3000)
    view._timeline = SourcePresentationTimeline.from_timestamps(array('q',range(0,3000000,40000)),40000)
    view._priming = True
    view._staging_sink.setVideoFrame(presented_frame(0))
    view._staging_sink.setVideoFrame(presented_frame(400000))
    player.positionChanged.emit(400)
    yield app,view,player,audio,renderer
    view.shutdown()


@pytest.mark.parametrize('timestamp',[800000,840000,2040000,360000,-1,-2])
def test_excluded_or_unknown_is_never_published(admitted,timestamp):
    app,view,player,audio,renderer=admitted
    view.configure_trim_loop(True,400,800);view.togglePlayback()
    view._staging_sink.setVideoFrame(presented_frame(760000))
    viewer_events=[];admitted_events=[];changes=[]
    renderer.videoFrameChanged.connect(viewer_events.append)
    view.admittedVideoFrame.connect(admitted_events.append)
    view.changed.connect(lambda:changes.append(view._presented_start))
    before=renderer.videoFrame(),view._presented_start,view.frameText
    seeks=player.events.count(('position',400))
    for _ in range(4):
        view._staging_sink.setVideoFrame(presented_frame(timestamp))
        assert before==(renderer.videoFrame(),view._presented_start,view.frameText)
    assert not viewer_events and not admitted_events
    assert all(value==760000 for value in changes)
    assert player.events.count(('position',400))==seeks+(timestamp>=0)
    assert view._player is player and view._audio is audio and view.playing
    if timestamp>=0:
        assert view._loop_seeking
        view._staging_sink.setVideoFrame(presented_frame(520000))
        assert view._loop_seeking  # In-range, but not the accepted near-In ack.
        view._staging_sink.setVideoFrame(presented_frame(400000))
        assert not view._loop_seeking
        assert admitted_events[-1].frame==renderer.videoFrame()


def test_invalid_delivery_cannot_replace_retained_picture(admitted):
    _,view,_,_,renderer=admitted
    view.configure_trim_loop(True,400,800);view.togglePlayback()
    before=renderer.videoFrame(),view._presented_start,view.frameText
    view._staging_sink.setVideoFrame(QVideoFrame())
    assert before==(renderer.videoFrame(),view._presented_start,view.frameText)


def test_current_range_disable_reenable_and_manual_escape(admitted):
    _,view,player,_,renderer=admitted
    view.configure_trim_loop(True,400,800);view.togglePlayback()
    view.configure_trim_loop(True,400,600)
    view._staging_sink.setVideoFrame(presented_frame(600000))
    assert renderer.videoFrame().startTime()==400000 and view._loop_seeking
    view._staging_sink.setVideoFrame(presented_frame(400000))
    view.configure_trim_loop(False,400,600)
    assert not view.trimLoopActive
    view._staging_sink.setVideoFrame(presented_frame(840000))
    assert renderer.videoFrame().startTime()==view._presented_start==840000
    view.configure_trim_loop(True,400,600)
    view._staging_sink.setVideoFrame(presented_frame(1000000))
    assert not view.trimLoopActive and view._presented_start==1000000
    view.seek(420);view._staging_sink.setVideoFrame(presented_frame(400000))
    # Pause then arm using existing Play-inside semantics.
    view.togglePlayback();view.togglePlayback()
    assert view.trimLoopActive
    view.stepFrame(-1)
    assert not view.trimLoopActive
    view._staging_sink.setVideoFrame(presented_frame(360000))
    assert view._presented_start==360000 and view._step_pending is None


def test_retirement_precedes_successor_and_stale_owners_cannot_publish(admitted,tmp_path):
    app,view,player,_,renderer=admitted
    old_sink=view._staging_sink;old_receive=view._frame_handler
    original=view._player_factory
    def factory():
        assert player.retired and player.video_sink is None
        assert not shiboken6.isValid(old_sink)
        assert not view.findChildren(QVideoSink)
        return original()
    view._player_factory=factory
    view.begin_loading();view.set_source(str(tmp_path/'successor.mp4'))
    assert view._player.video_sink is view._staging_sink and view._staging_sink is not renderer
    assert len(view.findChildren(QVideoSink))==1
    before=view._presented_start,view.frameText,list(view._player.events)
    old_receive(presented_frame(840000));app.processEvents()
    assert before==(view._presented_start,view.frameText,list(view._player.events))
    assert not renderer.videoFrame().isValid()
    current=view._staging_sink;stale=view._frame_handler
    view.shutdown()
    assert not shiboken6.isValid(current) and not view.findChildren(QVideoSink)
    before=view._presented_start,view.frameText,list(view._player.events)
    stale(presented_frame(840000));app.processEvents()
    assert before==(view._presented_start,view.frameText,list(view._player.events))
    assert not renderer.videoFrame().isValid()


def test_queued_worker_delivery_admits_on_gui_owner(admitted):
    app,view,_,_,renderer=admitted
    class Delivery(QObject):
        frame=Signal(QVideoFrame)
    delivery=Delivery()
    delivery.frame.connect(view._admission_receiver.receive)
    threads=[]
    renderer.videoFrameChanged.connect(lambda _:threads.append(QThread.currentThread()))
    worker=threading.Thread(target=lambda:delivery.frame.emit(presented_frame(440000)))
    worker.start();worker.join()
    assert view._presented_start==400000
    app.processEvents()
    assert view._presented_start==440000 and threads==[view.thread()]
