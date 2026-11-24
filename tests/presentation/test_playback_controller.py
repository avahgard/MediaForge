from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtTest import QTest

from mediaforge.presentation.bridge.playback import PlaybackController, _format_time
from array import array
from mediaforge.application.contracts.source_timeline import SourcePresentationTimeline, SourceSignature, TimelineRequest, TimelineResult


def configure_fixture_timeline(view):
    # Explicit independently enumerated test pictures, not a production FPS fallback.
    view._timeline = SourcePresentationTimeline.from_timestamps(array('q', range(0,3000000,40000)),40000)
    # The fake transport has no native source-open prime. Require its next first
    # picture to supply the same decoded correspondence as the real prime.
    if view._first_presented_start is None and view._presented_start is None:
        view._priming = True
    view._establish_timeline_origin()
    view._map_current_frame()
    view.changed.emit()


@pytest.fixture(scope="module")
def app():
    return QCoreApplication.instance() or QCoreApplication([])


class FakeAudio(QObject):
    volumeChanged = Signal(float)
    mutedChanged = Signal(bool)

    def __init__(self):
        super().__init__()
        self._volume = 0.0
        self._muted = False

    def setVolume(self, value):
        self._volume = value
        self.volumeChanged.emit(value)

    def volume(self):
        return self._volume

    def setMuted(self, value):
        self._muted = value
        self.mutedChanged.emit(value)

    def isMuted(self):
        return self._muted


class FakePlayer(QObject):
    playbackStateChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    errorOccurred = Signal(object, str)

    def __init__(self):
        super().__init__()
        self.events = []
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self.video_sink = None
        self.video_output = None
        self.retired = False

    def setAudioOutput(self, audio):
        self.audio = audio

    def stop(self):
        self.events.append(("stop",))
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self.playbackStateChanged.emit(self._state)

    def setSource(self, source):
        assert isinstance(source, QUrl)
        self.events.append(("source", source.toLocalFile()))

    def setPosition(self, value):
        self.events.append(("position", value))
        self.positionChanged.emit(value)

    def play(self):
        self.events.append(("play",))
        self._state = QMediaPlayer.PlaybackState.PlayingState
        self.playbackStateChanged.emit(self._state)

    def pause(self):
        self.events.append(("pause",))
        self._state = QMediaPlayer.PlaybackState.PausedState
        self.playbackStateChanged.emit(self._state)

    def setVideoSink(self, sink):
        self.video_sink = sink

    def setVideoOutput(self, output):
        self.video_output = output


def controller():
    players = []

    def make_player():
        player = FakePlayer()
        players.append(player)
        return player

    def retire_player(player):
        player.retired = True

    player, audio = make_player(), FakeAudio()
    view = PlaybackController(
        player,
        audio,
        player_factory=make_player,
        player_disposer=retire_player,
    )
    view._test_players = players
    return view, player, audio


def test_empty_loading_ready_and_source_replacement_is_stop_detach_attach(app, tmp_path):
    view, player, _audio = controller()
    first = tmp_path / "first video.mp4"
    second = tmp_path / "Видео второе.mov"

    assert view.state == "Empty"
    view.begin_loading()
    assert view.state == "Loading" and not view.canPlay
    view.set_source(str(first))
    assert view.state == "Ready" and view.canPlay and not view.playing
    assert player.events[-4:-2] == [("stop",), ("source", "")]
    assert Path(player.events[-2][1]) == first
    assert player.events[-1] == ("position", 0)
    assert ("play",) not in player.events

    before = len(player.events)
    view.begin_loading()
    replacement = view._player
    assert replacement is not player and player.retired
    assert player.events[before:before + 2] == [("stop",), ("source", "")]
    view.set_source(str(second))
    assert replacement.events[:2] == [("stop",), ("source", "")]
    assert Path(replacement.events[-2][1]) == second
    assert replacement.events[-1] == ("position", 0)
    assert ("play",) not in replacement.events
    assert view.state == "Ready" and view.position == 0


def test_play_pause_end_and_replay_from_zero(app, tmp_path):
    view, player, _audio = controller()
    view.set_source(str(tmp_path / "movie.mp4"))
    view.togglePlayback()
    assert view.state == "Playing" and view.playing
    view.togglePlayback()
    assert view.state == "Paused" and not view.playing

    player.positionChanged.emit(4321)
    player.durationChanged.emit(9876)
    assert (view.position, view.duration) == (4321, 9876)
    assert (view.positionText, view.durationText) == ("00:04.321", "00:09.876")

    player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
    assert view.state == "Paused" and not view.playing
    player.events.clear()
    view.togglePlayback()
    assert player.events == [("play",)]
    assert view.state == "Playing"


def test_stale_source_signals_cannot_mutate_loading_replacement(app, tmp_path):
    view, player, _audio = controller()
    view.set_source(str(tmp_path / "A.mp4"))
    view.togglePlayback()
    old = dict(view._player_signal_handlers)

    view.begin_loading()
    old["media_status"](QMediaPlayer.MediaStatus.EndOfMedia)
    old["duration"](91_234)
    old["position"](9_876)

    assert view.state == "Loading"
    assert (view.duration, view.position) == (0, 0)


def test_stale_source_signals_cannot_overwrite_ready_replacements(app, tmp_path):
    view, player, _audio = controller()
    view.set_source(str(tmp_path / "A.mp4"))
    view.togglePlayback()
    old = dict(view._player_signal_handlers)

    view.begin_loading()
    view.set_source(str(tmp_path / "B.mp4"))
    old["media_status"](QMediaPlayer.MediaStatus.EndOfMedia)
    assert view.state == "Ready"

    view.set_source(str(tmp_path / "C.mp4"))
    old["error"](QMediaPlayer.Error.FormatError, "late A decode failure")
    assert view.state == "Ready" and view.errorMessage == ""

    view.set_source(str(tmp_path / "D.mp4"))
    old["playback_state"](QMediaPlayer.PlaybackState.PlayingState)
    assert view.state == "Ready"
    assert [event for event in player.events if event == ("play",)] == [("play",)]


def test_late_retired_backend_error_and_time_cannot_mutate_ready_source(app, tmp_path):
    view, retired, _audio = controller()
    view.set_source(str(tmp_path / "A.mp4"))

    view.begin_loading()
    view.set_source(str(tmp_path / "C.mp4"))
    assert retired.retired and view._player is not retired
    retired.errorOccurred.emit(QMediaPlayer.Error.FormatError, "late A error")
    assert view.state == "Ready" and view.errorMessage == ""

    current = view._player
    view.begin_loading()
    view.set_source(str(tmp_path / "D.mp4"))
    assert current.retired and view._player is not current
    current.durationChanged.emit(91_234)
    current.positionChanged.emit(9_876)
    assert view.state == "Ready"
    assert (view.duration, view.position) == (0, 0)


def test_late_retired_backend_end_cannot_end_playing_replacement(app, tmp_path):
    view, retired, _audio = controller()
    view.set_source(str(tmp_path / "old.mp4"))
    view.togglePlayback()
    retired.durationChanged.emit(10_000)
    retired.positionChanged.emit(9_999)

    view.begin_loading()
    view.set_source(str(tmp_path / "current.mp4"))
    current = view._player
    view.togglePlayback()
    assert view.state == "Playing" and current is not retired

    retired.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
    assert view.state == "Playing"

    current.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
    assert view.state == "Playing" and view.position == 0


def test_stale_end_near_replacement_boundary_is_ignored(app, tmp_path):
    view, player, _audio = controller()
    view.set_source(str(tmp_path / "A.mp4"))
    view.togglePlayback()
    player.durationChanged.emit(10_000)
    player.positionChanged.emit(9_999)
    old_end = view._player_signal_handlers["media_status"]
    play_count = player.events.count(("play",))

    view.begin_loading()
    old_end(QMediaPlayer.MediaStatus.EndOfMedia)
    assert view.state == "Loading"

    view.set_source(str(tmp_path / "B.mp4"))
    old_end(QMediaPlayer.MediaStatus.EndOfMedia)
    assert view.state == "Ready" and view.position == 0
    assert player.events.count(("play",)) == play_count


def test_current_source_signals_still_drive_play_pause_end_and_no_autoplay(app, tmp_path):
    view, _initial, _audio = controller()
    view.set_source(str(tmp_path / "old.mp4"))
    view.begin_loading()
    view.set_source(str(tmp_path / "current.mp4"))
    player = view._player
    assert view.state == "Ready" and ("play",) not in player.events

    player.durationChanged.emit(9_876)
    player.positionChanged.emit(4_321)
    assert (view.duration, view.position) == (9_876, 4_321)

    view.togglePlayback()
    assert view.state == "Playing"
    view.togglePlayback()
    assert view.state == "Paused"
    player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
    assert view.state == "Paused"

    player.events.clear()
    view.togglePlayback()
    assert player.events == [("play",)]
    assert view.state == "Playing"


def test_shutdown_invalidates_queued_player_callbacks(app, tmp_path):
    view, player, _audio = controller()
    view.set_source(str(tmp_path / "movie.mp4"))
    view.togglePlayback()
    queued = dict(view._player_signal_handlers)

    view.shutdown()
    queued["playback_state"](QMediaPlayer.PlaybackState.PlayingState)
    queued["media_status"](QMediaPlayer.MediaStatus.EndOfMedia)
    queued["duration"](12_000)
    queued["position"](4_000)
    queued["error"](QMediaPlayer.Error.FormatError, "late shutdown error")
    player.errorOccurred.emit(QMediaPlayer.Error.FormatError, "late backend error")

    assert view.state == "Empty"
    assert (view.position, view.duration, view.errorMessage) == (0, 0, "")


def test_volume_mapping_and_mute_share_zero_and_restore_volume(app):
    view, _player, audio = controller()
    view.setVolume(37)
    assert view.volume == 37 and audio.volume() == pytest.approx(0.37)
    view.toggleMuted()
    assert view.muted and view.volume == 0 and view.lastNonZeroVolume == 37
    view.setVolume(64)
    assert not view.muted and view.volume == 64
    view.toggleMuted()
    assert view.muted and view.volume == 0
    view.setVolume(0)
    view.toggleMuted()
    assert not view.muted and view.volume == 64
    view.setVolume(-20); assert view.volume == 0
    view.setVolume(120); assert view.volume == 100


def test_initial_audio_volume_is_preserved_and_zero_has_restore_fallback(app):
    player, audio = FakePlayer(), FakeAudio()
    audio.setVolume(.42)
    view = PlaybackController(player,audio)
    assert view.volumePercent == view.lastNonZeroVolume == 42 and not view.effectiveMuted
    view.toggleMuted(); assert view.volume == 0 and view.muted
    view.toggleMuted(); assert view.volume == 42 and not view.muted
    view.shutdown()


def test_index_ownership_manual_seek_source_and_shutdown_staleness(app, tmp_path):
    class Service:
        def __init__(self): self.callbacks=[]
        def activate(self,item,path,callback): self.callbacks.append((item,path,callback))
        def cancel(self, *, keep_scan=False): pass
        def shutdown(self): self.closed=True
    service=Service()
    view=PlaybackController(FakePlayer(),FakeAudio(),timeline_service=service,player_disposer=lambda p:None)
    sink=QVideoSink();view.attachVideoSink(sink)
    first=tmp_path/'first.mp4';first.write_bytes(b'first')
    second=tmp_path/'second.mp4';second.write_bytes(b'second')
    view.set_source(str(first));view._player.durationChanged.emit(3000)
    view.configure_timeline('A',str(first));view._priming=True;view._staging_sink.setVideoFrame(presented_frame(0))
    assert not view.exactStepAvailable and view.frameText=='Indexing frames…'
    timeline=SourcePresentationTimeline.from_timestamps(array('q',range(0,3000000,40000)),40000)
    old=service.callbacks[-1][2]
    old(TimelineResult(TimelineRequest('A',SourceSignature.capture(first),1),timeline))
    assert view.exactStepAvailable and view.frameText=='Frame 1 / 75'
    view.seek(200)
    assert not view.exactStepAvailable and view.frameText=='Frame 6 / 75'
    assert view._current_index==0  # Requested UI is not a decoded/export receipt.
    view._staging_sink.setVideoFrame(presented_frame(200000))
    assert view.frameText=='Frame 6 / 75' and view.exactStepAvailable
    view.set_source(str(second));view.configure_timeline('B',str(second))
    old(TimelineResult(TimelineRequest('A',SourceSignature.capture(first),1),timeline))
    assert view._timeline is None and view.frameText=='Indexing frames…'
    current=service.callbacks[-1][2]
    view.shutdown();current(TimelineResult(TimelineRequest('B',SourceSignature.capture(second),2),timeline))
    assert view.state=='Empty' and service.closed and view._timeline is None


def test_decoded_delivery_without_completed_index_cannot_establish_ordinal(app,tmp_path):
    view,player,audio=controller();sink=QVideoSink();view.attachVideoSink(sink)
    view.set_source(str(tmp_path/'source.mp4'));player.durationChanged.emit(3000)
    view._priming=True;view._staging_sink.setVideoFrame(presented_frame(0))
    view._staging_sink.setVideoFrame(presented_frame(40000));view.stepFrame(1)
    assert view.frameText=='Frame timeline unavailable' and not view.exactStepAvailable
    before=list(player.events)
    configure_fixture_timeline(view)
    assert view.frameText=='Frame 1 / 75' and view._current_index==1
    assert player.events==before
    view.shutdown()


def test_playback_clock_resumes_after_seek_during_indexing(app, tmp_path):
    view, player, _ = controller()
    view.attachVideoSink(QVideoSink(view))
    view.set_source(str(tmp_path / 'video.mp4'))
    player.durationChanged.emit(3000)
    view._priming = view._indexing = True
    view._staging_sink.setVideoFrame(presented_frame(0))
    view.togglePlayback()
    view.seek(200)
    assert view.position == 200 and not view.exactStepAvailable
    view.stepFrame(1)
    assert view.position == 200
    view._staging_sink.setVideoFrame(presented_frame(240000))
    player.positionChanged.emit(245)
    assert view.position == 245 and view.frameText == 'Indexing frames…'
    assert not view.exactStepAvailable
    configure_fixture_timeline(view)
    view._indexing = False
    assert view.exactStepAvailable and view._current_index == 6
    view.togglePlayback()
    view.stepFrame(1)
    assert not view.exactStepAvailable
    view._staging_sink.setVideoFrame(presented_frame(280000))
    assert view.exactStepAvailable and view._current_index == 7
    view.shutdown()


def test_revisit_restores_time_before_index_and_settles_by_own_prime(app, tmp_path):
    class Indexer:
        def activate(self, item, path, completion):
            self.complete = completion

        def cancel(self, *, keep_scan=False):
            pass

        def shutdown(self):
            pass

    indexer = Indexer()
    view = PlaybackController(FakePlayer(), FakeAudio(), timeline_service=indexer, player_disposer=lambda p: None)
    view.attachVideoSink(QVideoSink(view))
    source = tmp_path / 'A.mp4'
    other = tmp_path / 'B.mp4'
    source.touch()
    other.touch()

    def select(item, path):
        view.set_source(str(path))
        view._priming = True  # The fake has no native source-open preroll.
        view._player.durationChanged.emit(3000)
        view.configure_timeline(item, str(path))
        return indexer.complete

    try:
        stale = select('A', source)
        view._staging_sink.setVideoFrame(presented_frame(0))
        view.seek(840)
        saved = view.position
        signature = view._timeline_signature
        select('B', other)
        current = select('A', source)
        view.restore_session(saved)
        view._staging_sink.setVideoFrame(presented_frame(0))
        assert view.position == 840 and ('position', 840) in view._player.events
        assert not view.framesReliable
        timeline = SourcePresentationTimeline.from_timestamps(array('q', range(0, 3000000, 40000)), 40000)
        result = TimelineResult(TimelineRequest('A', signature, 1), timeline)
        stale(result)
        assert not view.timelineAvailable
        view._staging_sink.setVideoFrame(presented_frame(840000))
        app.processEvents()
        assert not view.framesReliable
        current(result)
        assert view.framesReliable and view._current_index == 21 and view.position == 840, (
            view._first_presented_start, view._presented_start, view._current_index,
            view.requestedFrameIndex, view._seek_waiting, view._timeline_offset, view.state)
    finally:
        view.shutdown()


def test_nonzero_decoded_origin_uses_relative_seek_and_never_refits_from_later_frame(app,tmp_path):
    view,player,audio=controller();sink=QVideoSink();view.attachVideoSink(sink)
    view.set_source(str(tmp_path/'origin.mp4'));player.durationChanged.emit(120)
    view._timeline=SourcePresentationTimeline.from_timestamps(array('q',[1000000,1040000,1080000]),40000)
    view._priming=True;view._staging_sink.setVideoFrame(presented_frame(1000000))
    assert view.frameText=='Frame 1 / 3'
    view.stepFrame(1)
    assert view.position==1060 and view.frameText=='Frame 2 / 3'
    assert view._current_index==0 and not view.framesReliable
    view._staging_sink.setVideoFrame(presented_frame(1040000))
    assert view.frameText=='Frame 2 / 3' and view._step_pending is None
    original_offset=view._timeline_offset
    view.seek(100);view._staging_sink.setVideoFrame(presented_frame(1080000))
    view._establish_timeline_origin()
    assert view._timeline_offset==original_offset and view._current_index==1
    assert view.frameText=='Frame 1 / 3' and not view.framesReliable  # requested 100ms, actual 1080ms
    view.shutdown()


def test_fullscreen_reuses_player_and_preserves_playback_state(app, tmp_path):
    view, _initial, _audio = controller()
    view.set_source(str(tmp_path / "old.mp4"))
    view.begin_loading()
    view.set_source(str(tmp_path / "movie.mp4"))
    player = view._player
    view.togglePlayback()
    player.positionChanged.emit(2450)
    identity = id(player)

    view.enterFullscreen()
    assert view.fullscreen and view.controlsVisible
    assert id(player) == identity and view.state == "Playing" and view.position == 2450
    view._hide_controls_after_idle()
    assert not view.controlsVisible
    view.pointerActivity()
    assert not view.controlsVisible
    view.setControlsZone(True)
    assert view.controlsVisible
    view.beginControlsInteraction()
    view._hide_controls_after_idle()
    assert view.controlsVisible
    view.endControlsInteraction()
    view.exitFullscreen()
    assert not view.fullscreen
    assert id(player) == identity and view.state == "Playing" and view.position == 2450


def test_video_sink_and_stable_error_mapping(app, tmp_path):
    view, initial, _audio = controller()
    sink = QVideoSink()
    view.attachVideoSink(sink)
    assert initial.video_sink is view._staging_sink and initial.video_sink is not sink
    view.set_source(str(tmp_path / "old.mp4"))
    view.begin_loading()
    view.set_source(str(tmp_path / "bad.mp4"))
    player = view._player
    assert player is not initial and player.video_sink is view._staging_sink and player.video_sink is not sink
    player.errorOccurred.emit(QMediaPlayer.Error.FormatError, "raw backend detail")
    assert view.state == "Error"
    assert view.errorMessage == "This media could not be played."


def test_qml_video_output_remains_renderer_across_staging_replacement(app, tmp_path):
    view, initial, _audio = controller()
    output = QObject()
    renderer = QVideoSink()
    output.setProperty("videoSink", renderer)
    view.attachVideoOutput(output)
    view.set_source(str(tmp_path / "old.mp4"))

    view.begin_loading()
    view.set_source(str(tmp_path / "new.mp4"))

    assert initial.retired
    assert view._player is not initial
    assert view._player.video_output is None
    assert view._presentation_sink is renderer
    assert view._player.video_sink is view._staging_sink and view._staging_sink is not renderer


@pytest.mark.parametrize(
    ("milliseconds", "expected"),
    [(0, "00:00.000"), (62_250, "01:02.250"), (3_661_007, "1:01:01.007")],
)
def test_time_format(milliseconds, expected):
    assert _format_time(milliseconds) == expected


def presented_frame(start_us):
    frame = QVideoFrame(QImage(16, 16, QImage.Format_RGB32))
    frame.setStartTime(start_us)
    return frame


def test_presented_trim_boundary_retains_picture_and_has_one_owned_loop_seek(app,tmp_path):
    view,player,audio=controller()
    sink=QVideoSink();view.attachVideoSink(sink)
    view.set_source(str(tmp_path/'trim.mp4'));player.durationChanged.emit(3000)
    view._staging_sink.setVideoFrame(presented_frame(400000));player.positionChanged.emit(400)
    view.configure_trim_loop(True,400,800);view.togglePlayback()
    assert view.playing and view.trimLoopActive
    view._staging_sink.setVideoFrame(presented_frame(760000));player.positionChanged.emit(760)
    count=player.events.count(('position',400))
    for timestamp in (800000,840000,880000,800000):
        view._staging_sink.setVideoFrame(presented_frame(timestamp))
        app.processEvents()  # Delivery guard forwards seek ownership to the GUI.
        assert sink.videoFrame().startTime()==view._presented_start==760000
    assert player.events.count(('position',400))==count+1
    assert view.playing and view.trimLoopActive and view._player is player and view._audio is audio
    view._staging_sink.setVideoFrame(presented_frame(400000))
    view._staging_sink.setVideoFrame(presented_frame(800000))
    app.processEvents()
    assert player.events.count(('position',400))==count+2
    view.seek(1000)
    view._staging_sink.setVideoFrame(presented_frame(1000000))
    assert not view.trimLoopActive and sink.videoFrame().startTime()==1000000
    view.shutdown()


def test_requested_position_is_not_a_frame_and_pending_steps_cannot_skip(app, tmp_path):
    view, player, _audio = controller()
    sink = QVideoSink()
    view.attachVideoSink(sink)
    view.set_source(str(tmp_path / "source.mp4"))
    configure_fixture_timeline(view)
    player.durationChanged.emit(3000)
    player.positionChanged.emit(40)
    assert view.frameText == "Frame — / 75" and not view.framesReliable
    view._staging_sink.setVideoFrame(presented_frame(0))
    assert view.frameText == "Frame 2 / 75" and view._current_index==0
    view.stepFrame(1)
    assert view.position == 60 and view.frameText == "Frame 2 / 75"
    assert view._current_index==0 and not view.framesReliable
    assert view._step_pending == 1
    for _ in range(5):
        view.stepFrame(1)
    assert view.position == 60 and player.events.count(("position", 60)) == 1
    view._staging_sink.setVideoFrame(presented_frame(40000))
    assert view._step_pending is None and view.frameText == "Frame 2 / 75"
    view._queued_step = None
    view.stepFrame(-1)
    assert view.position == 20 and view.frameText == "Frame 1 / 75"
    assert view._current_index==1 and not view.framesReliable
    view._staging_sink.setVideoFrame(presented_frame(0))
    assert view.frameText == "Frame 1 / 75"
    view.shutdown()


def test_global_label_waits_for_current_sink_and_presentation_coherence(app, tmp_path):
    view, player, _audio = controller()
    sink = QVideoSink()
    view.attachVideoSink(sink)
    view.set_source(str(tmp_path / 'source.mp4'))
    configure_fixture_timeline(view)
    player.durationChanged.emit(3000)
    view._staging_sink.setVideoFrame(presented_frame(0))
    assert view.frameText == 'Frame 1 / 75'
    # Model actual native delivery preceding its queued Presentation observation.
    view.admittedVideoFrame.disconnect(view._on_admitted_video_frame)
    admitted = []
    view.admittedVideoFrame.connect(admitted.append)
    view._staging_sink.setVideoFrame(presented_frame(40000))
    assert view._presented_start == 0
    assert not view.framesReliable and not view.exactStepAvailable
    assert view.frameText == 'Frame 1 / 75'  # unchanged requested clock
    view._on_admitted_video_frame(admitted[-1])
    assert view._current_index==1 and view.framesReliable
    assert view.frameText == 'Frame 1 / 75'  # callback does not forge user intent
    view.admittedVideoFrame.connect(view._on_admitted_video_frame)
    view.shutdown()


def test_unsettled_backend_step_fails_closed_without_fabricating_label(app, tmp_path):
    view, player, _audio = controller()
    sink = QVideoSink()
    view.attachVideoSink(sink)
    view.set_source(str(tmp_path / "source.mp4"))
    configure_fixture_timeline(view)
    player.durationChanged.emit(3000)
    view._staging_sink.setVideoFrame(presented_frame(0))
    view._step_timer.setInterval(15)
    view.stepFrame(1)
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    view.changed.connect(lambda: loop.quit() if view._step_failed else None)
    QTimer.singleShot(1000, loop.quit)
    loop.exec()
    assert not view.framesReliable and view.frameText == "Frame 2 / 75"
    assert view._current_index==0  # timeout never authenticates the requested picture
    before = list(player.events)
    view.stepFrame(1)
    assert player.events == before and view._presented_start == 0
    view.shutdown()


def test_retired_video_frame_callbacks_cannot_label_a_replacement_or_shutdown(app, tmp_path):
    view, player, _audio = controller()
    sink = QVideoSink()
    view.attachVideoSink(sink)
    view.set_source(str(tmp_path / "old.mp4"))
    configure_fixture_timeline(view)
    view._staging_sink.setVideoFrame(presented_frame(40000))
    retired_handler = view._frame_handler
    view.begin_loading()
    view.set_source(str(tmp_path / "current.mp4"))
    configure_fixture_timeline(view)
    retired_handler(presented_frame(80000))
    assert view.frameText == "Frame — / 75" and view._presented_start is None
    view._staging_sink.setVideoFrame(presented_frame(0))
    assert view.frameText == "Frame 1 / 75"
    current_handler = view._frame_handler
    view.shutdown()
    current_handler(presented_frame(120000))
    assert view._presented_start is None and view.state == "Empty"


def test_latest_seek_ignores_late_clock_and_picture_for_intent(app,tmp_path):
    view,player,_=controller();sink=QVideoSink();view.attachVideoSink(sink)
    view.set_source(str(tmp_path/'source.mp4'));configure_fixture_timeline(view)
    player.durationChanged.emit(3000);view._staging_sink.setVideoFrame(presented_frame(0))
    view.seek(1180);view.seek(1540)
    player.positionChanged.emit(1180);view._staging_sink.setVideoFrame(presented_frame(1160000))
    assert view.position==1540 and view.frameText=='Frame 39 / 75'
    assert view._current_index==0 and sink.videoFrame().startTime()==0 and not view.framesReliable
    view._staging_sink.setVideoFrame(presented_frame(1520000))
    assert view.framesReliable and view._current_index==38 and view.position==1540
    player.positionChanged.emit(1180)
    assert view.position==1540 and view.frameText=='Frame 39 / 75'
    view._staging_sink.setVideoFrame(presented_frame(1160000))
    assert sink.videoFrame().startTime()==1520000 and view._current_index==38
    view.shutdown()


def test_restore_prime_is_not_published_and_uses_original_origin(app,tmp_path):
    view,player,_=controller();sink=QVideoSink();view.attachVideoSink(sink)
    path=tmp_path/'source.mp4';path.write_bytes(b'fake source')
    view.set_source(str(path));configure_fixture_timeline(view)
    view._timeline_signature=SourceSignature.capture(path)
    player.durationChanged.emit(3000);view.restore_session(1540)
    view._staging_sink.setVideoFrame(presented_frame(0))
    assert not sink.videoFrame().isValid() and view._current_index is None
    assert ('position',1540) in player.events and view.frameText=='Frame 39 / 75'
    # A late frame/clock at zero may not overwrite logical intent.
    player.positionChanged.emit(0)
    view._staging_sink.setVideoFrame(presented_frame(1520000))
    assert view.position==1540 and view._current_index==38 and view.framesReliable
    view.shutdown()


@pytest.fixture
def saved_picture(app, tmp_path):
    view, player, _ = controller()
    view.attachVideoSink(QVideoSink(view))
    path = tmp_path / 'saved.mp4'
    path.touch()
    view.set_source(str(path))
    view._timeline_signature = SourceSignature.capture(path)
    # The owner's fractional boundary and variable intervals, with nonzero first PTS.
    view._timeline = SourcePresentationTimeline.from_timestamps(
        array('q', [12000, 1449822, 1467044, 1484133]), 17222)
    view._priming = True
    player.durationChanged.emit(1501)
    yield view, player
    view.shutdown()


@pytest.mark.parametrize('clock,expected', [(1455, 1449822), (1466, 1449822),
                                         (1468, 1467044), (1501, 1484133)])
def test_saved_picture_settles_on_matching_first_result(saved_picture, clock, expected):
    view, player = saved_picture
    view.restore_session(clock)
    view._staging_sink.setVideoFrame(presented_frame(12000))
    view._staging_sink.setVideoFrame(presented_frame(expected))
    assert view.position == clock and view.framesReliable and view.exactStepAvailable
    assert view._presented_start == expected and player.events[-1] == ('position', clock)
    assert not view._exact_seek_timer.isActive()


def test_saved_picture_boundary_retries_before_authorizing_export(app, saved_picture):
    view, player = saved_picture
    view.restore_session(1467)
    view._staging_sink.setVideoFrame(presented_frame(12000))
    assert ('position', 1467) in player.events
    view._staging_sink.setVideoFrame(presented_frame(1467044))
    assert view.requestedFrameIndex == 1 and not view.framesReliable and not view.exactStepAvailable
    assert not view._presentation_sink.videoFrame().isValid()
    app.processEvents()
    assert player.events[-1] == ('position', 1458)
    view._staging_sink.setVideoFrame(presented_frame(1449822))
    assert view.position == 1467 and view.exactStepAvailable
    assert view.export_picture_snapshot().ordinal == 1
    view.stepFrame(1)
    view._staging_sink.setVideoFrame(presented_frame(1467044))
    assert view._current_index == 2 and view.framesReliable


@pytest.mark.parametrize('restoring', [True, False])
@pytest.mark.parametrize('supersede', ['seek', 'replace', 'play'])
def test_exact_picture_correction_cannot_seek_after_supersession(saved_picture, tmp_path, supersede, restoring):
    view, player = saved_picture
    receiver = view._admission_receiver
    if restoring:
        view.restore_session(1467)
        receiver.receive(presented_frame(12000))
    else:
        receiver.receive(presented_frame(12000))
        view.seek(1467)
    receiver.receive(presented_frame(1467044))
    if supersede == 'seek':
        view.seek(1468)
        receiver.receive(presented_frame(1449822))
    elif supersede == 'replace':
        view.set_source(str(tmp_path / 'replacement.mov'))
        view._admit_staged_frame(receiver, presented_frame(1449822))
    else:
        view.togglePlayback()
        assert view.playing
    QTest.qWait(120)
    assert not view.framesReliable and ('position', 1458) not in player.events
    assert ('position', 1458) not in view._player.events
    assert view._exact_seek_timer.isActive() == (supersede == 'seek')


def test_saved_picture_missing_after_one_retry_fails_then_can_seek(app, saved_picture):
    view, player = saved_picture
    view._exact_seek_timer.setInterval(50)
    view.restore_session(1467)
    view._staging_sink.setVideoFrame(presented_frame(12000))
    view._staging_sink.setVideoFrame(presented_frame(1467044))
    app.processEvents()
    view._staging_sink.setVideoFrame(presented_frame(1467044))
    QTest.qWait(100)
    assert player.events.count(('position', 1458)) == 1
    assert view.frameStepStatus == 'Saved frame could not be restored. Seek or play to continue.'
    view._staging_sink.setVideoFrame(presented_frame(1449822))
    assert not view.framesReliable and view.position == 1467
    assert not view._presentation_sink.videoFrame().isValid()
    view.seek(1468)
    view._staging_sink.setVideoFrame(presented_frame(1467044))
    assert view.framesReliable and view.exactStepAvailable


def test_rapid_manual_seeks_correct_only_the_final_unsettled_picture(saved_picture):
    view, player = saved_picture
    sink = view._staging_sink
    sink.setVideoFrame(presented_frame(12000))
    for clock, adjacent in [(1467, 1467044), (1484, 1484133), (1467, 1467044)]:
        view.seek(clock)
        sink.setVideoFrame(presented_frame(adjacent))
        assert not view.framesReliable and not view.exactStepAvailable
        assert view._presentation_sink.videoFrame().startTime() == 12000
    QTest.qWait(120)
    assert ('position', 1475) not in player.events
    assert player.events.count(('position', 1458)) == 1
    sink.setVideoFrame(presented_frame(1449822))
    assert view.position == 1467 and view.framesReliable and view.exactStepAvailable
    assert view.export_picture_snapshot().ordinal == 1
    view.seek(1467)
    assert view.framesReliable and not view._exact_seek_timer.isActive()


def test_manual_seek_deadline_disables_exact_authority_until_a_new_action(saved_picture):
    from time import monotonic

    view, player = saved_picture
    sink = view._staging_sink
    sink.setVideoFrame(presented_frame(12000))
    view.togglePlayback()
    assert view._exact_seek_timer.interval() == 3000
    start = monotonic()
    view.seek(1467)
    while not view._exact_seek_failure and monotonic() - start < 4:
        sink.setVideoFrame(presented_frame(1467044))
        QTest.qWait(30)
    assert 2.7 <= monotonic() - start <= 4
    assert player.events.count(('position', 1458)) == 1
    assert view.position == 1467 and not view.playing
    assert view.frameStepStatus == 'Exact picture could not be shown. Seek or play to continue.'
    assert view.frameText == 'Seek failed · seek or play'
    sink.setVideoFrame(presented_frame(1449822))
    assert not view.framesReliable and not view.exactStepAvailable
    assert view._presentation_sink.videoFrame().startTime() == 12000
    view.seek(1468)
    sink.setVideoFrame(presented_frame(1467044))
    assert view.framesReliable and view.export_picture_snapshot().ordinal == 2


def test_restore_play_does_not_arm_trim_until_requested_picture(app,tmp_path):
    view,player,_=controller();sink=QVideoSink();view.attachVideoSink(sink)
    path=tmp_path/'source.mp4';path.write_bytes(b'fake source')
    view.set_source(str(path));configure_fixture_timeline(view)
    view._timeline_signature=SourceSignature.capture(path)
    player.durationChanged.emit(3000);view.configure_trim_loop(True,1000,2000)
    view.restore_session(1540,playing=True)
    view._staging_sink.setVideoFrame(presented_frame(0))
    assert not view.playing and not view.trimLoopActive and view._resume_after_seek
    view._staging_sink.setVideoFrame(presented_frame(1520000))
    assert view.playing and view.trimLoopActive and view._current_index==38
    assert ('position',1000) not in player.events
    view.shutdown()


def test_reentrant_requested_seek_cannot_target_replacement_player(app,tmp_path):
    view,player,_=controller();view.set_source(str(tmp_path/'one.mp4'));player.durationChanged.emit(3000)
    def replace():
        view.changed.disconnect(replace)
        view.set_source(str(tmp_path/'two.mp4'))
    view.changed.connect(replace)
    view.seek(1540)
    assert ('position',1540) not in view._player.events
    assert view.position==0
    view.shutdown()


def test_restore_first_frame_needs_no_second_decoder_delivery_and_shutdown_clears_intent(app,tmp_path):
    view,player,_=controller();sink=QVideoSink();view.attachVideoSink(sink)
    path=tmp_path/'source.mp4';path.write_bytes(b'fake source')
    view.set_source(str(path));configure_fixture_timeline(view)
    view._timeline_signature=SourceSignature.capture(path)
    player.durationChanged.emit(3000);view.restore_session(0)
    view._staging_sink.setVideoFrame(presented_frame(0))
    assert view.framesReliable and view._current_index==0 and view._restore_session is None
    view.seek(1540);view.shutdown()
    assert view.position==0 and view._restore_session is None and not view._resume_after_seek



def test_audio_only_transport_has_no_video_publisher_and_retires_before_image(app,tmp_path):
    view,player,_=controller();sink=QVideoSink();view.attachVideoSink(sink)
    view.set_source(str(tmp_path/'sound.wav'),audio_only=True);player.durationChanged.emit(800)
    assert view.frameText=='Audio' and view.canPlay and not view.timelineAvailable and player.video_sink is None
    view._staging_sink.setVideoFrame(presented_frame(0))
    assert not sink.videoFrame().isValid()  # even an unexpected audio backend picture cannot publish
    view.seek(200);assert view.position==200 and not view._seek_waiting
    view.togglePlayback();player.positionChanged.emit(320);assert view.position==320
    view.togglePlayback();assert not view.playing
    view.set_source(str(tmp_path/'real-video.mp4'))
    assert player.retired and view._player is not player and view._player.video_sink is view._staging_sink
    assert view.frameText!='Audio';view.shutdown()


@pytest.mark.parametrize('suffix,physical',[('avi',160),('mp4',180),('mov',180),('mkv',180)])
def test_avi_packet_rounding_changes_only_decoder_target_not_logical_intent(app,tmp_path,suffix,physical):
    view,player,_=controller();sink=QVideoSink();view.attachVideoSink(sink)
    path=tmp_path/('source.'+suffix);path.write_bytes(b'owned fake transport')
    view.set_source(str(path));configure_fixture_timeline(view);view._timeline_signature=SourceSignature.capture(path)
    player.durationChanged.emit(3000);view._staging_sink.setVideoFrame(presented_frame(0))
    view.seek(180);assert view.position==180 and view.frameText=='Frame 5 / 75'
    assert ('position',physical) in player.events
    view._staging_sink.setVideoFrame(presented_frame(200000))
    assert not view.framesReliable and view._current_index==0
    view._staging_sink.setVideoFrame(presented_frame(160000))
    assert view.framesReliable and view._current_index==4 and view.position==180
    view.stepFrame(1)
    assert ('position',200 if suffix=='avi' else 220) in player.events
    view._staging_sink.setVideoFrame(presented_frame(200000))
    assert view.framesReliable and view._current_index==5
    view.shutdown()
