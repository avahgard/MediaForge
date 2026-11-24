"""PC2-F1 origin truth and exceptional teardown contracts using real Qt frames."""

from array import array

import pytest
from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame, QVideoSink

from mediaforge.application.contracts.source_timeline import SourcePresentationTimeline
from test_playback_controller import controller, presented_frame


@pytest.fixture(scope="module")
def app():
    return QCoreApplication.instance() or QCoreApplication([])


def source(view, player, sink, path, *, first=None):
    view.attachVideoSink(sink)
    view.set_source(str(path))
    player.durationChanged.emit(3000)
    view._priming = True  # Fake transport: model the native source-open prime.
    if first is not None:
        view._staging_sink.setVideoFrame(presented_frame(first))


def timeline(view, first=0):
    view._timeline = SourcePresentationTimeline.from_timestamps(
        array("q", (first + i * 40000 for i in range(75))), 40000)
    view._establish_timeline_origin()
    view._map_current_frame()


@pytest.mark.parametrize("unknown", [-1, -2, -(1 << 63)])
@pytest.mark.parametrize("index_first", [False, True])
def test_unknown_first_decoded_timestamp_never_authorizes_later_ordinal(app, tmp_path, unknown, index_first):
    view, player, _ = controller()
    sink = QVideoSink()
    source(view, player, sink, tmp_path / "source.mp4")
    if index_first:
        timeline(view)
    view._staging_sink.setVideoFrame(presented_frame(unknown))
    if not index_first:
        timeline(view)
    assert view.state == "Paused" and view.totalFrameCountReliable
    assert view._first_presented_start is None and view._timeline_offset is None
    assert not view._origin_established and view._origin_generation is None
    assert view.frameText == "Frame — / 75"
    # Reviewer oracle: second independently identified picture has decoded
    # origin +120ms, but its first picture supplied no trustworthy correspondence.
    for later in (160000, 40000, 0):
        view._staging_sink.setVideoFrame(presented_frame(later))
        view._establish_timeline_origin()
        assert view.frameText == "Frame — / 75"
        assert not view.framesReliable and not view.globalFrameOrdinalReliable
        assert not view.exactStepAvailable and view._current_index is None
    before = list(player.events)
    view.stepFrame(1)
    assert player.events == before
    view.shutdown()


def test_invalid_frame_and_default_offset_are_not_origin_evidence(app, tmp_path):
    view, player, _ = controller()
    sink = QVideoSink()
    source(view, player, sink, tmp_path / "source.mp4")
    timeline(view)
    view._staging_sink.setVideoFrame(QVideoFrame())
    assert view._first_presented_start is None and not view._origin_established
    # Deliberate adversarial state: a numerical default/decoded match cannot
    # bypass the separate proven-origin admission guard.
    view._priming = False
    view._timeline_offset = 0
    view._staging_sink.setVideoFrame(presented_frame(40000))
    assert view._current_index is None and view.frameText == "Frame — / 75"
    assert not view.exactStepAvailable
    view.shutdown()


def test_nonzero_origin_is_decoded_once_and_never_refitted_from_seek_clock(app, tmp_path):
    view, player, _ = controller()
    sink = QVideoSink()
    source(view, player, sink, tmp_path / "origin.mp4")
    # FFprobe origin differs from Qt. No player.position method is needed or
    # consulted; both transport clock updates below are intentionally unrelated.
    timeline(view, first=5000000)
    player.positionChanged.emit(999)
    view._staging_sink.setVideoFrame(presented_frame(120000))
    assert view._current_index==0 and view.exactStepAvailable
    assert view.frameText == "Frame 22 / 75"  # logical 999ms, decoded 120ms
    offset = view._timeline_offset
    assert offset == -4880000
    view._staging_sink.setVideoFrame(presented_frame(160000))
    assert view._current_index==1 and view.framesReliable
    assert view.frameText == "Frame 22 / 75"
    view.stepFrame(-1)
    assert view.position == 140  # Inside actual Qt [120000,160000) interval.
    view._staging_sink.setVideoFrame(presented_frame(120000))
    assert view.frameText == "Frame 1 / 75" and view._step_pending is None
    view.seek(1000)
    assert not view.exactStepAvailable
    view._staging_sink.setVideoFrame(presented_frame(200000))
    view._establish_timeline_origin()
    assert view._timeline_offset == offset and view._current_index==0
    assert view.frameText == "Frame 23 / 75" and not view.framesReliable
    # An unmatchable later picture must not induce a new offset.
    view.seek(2000)
    view._staging_sink.setVideoFrame(presented_frame(980000))
    view._establish_timeline_origin()
    assert view._timeline_offset == offset and view.frameText == "Frame 48 / 75"
    assert not view.framesReliable
    view.shutdown()


def test_replacement_rejects_retired_origin_and_frame_callbacks(app, tmp_path):
    view, player, _ = controller()
    sink = QVideoSink()
    source(view, player, sink, tmp_path / "first.mp4")
    timeline(view)
    view._staging_sink.setVideoFrame(presented_frame(120000))
    assert view.frameText == "Frame 1 / 75"
    stale = view._frame_handler
    view.set_source(str(tmp_path / "second.mp4"))
    timeline(view)
    assert view._timeline_offset is None and view._origin_generation is None
    stale(presented_frame(160000))
    view._staging_sink.setVideoFrame(presented_frame(160000))  # Later delivery is not a prime.
    assert not view._origin_established and view.frameText == "Frame — / 75"
    view._priming = True
    view._staging_sink.setVideoFrame(presented_frame(80000))
    assert view.frameText == "Frame 1 / 75" and view._timeline_offset == 80000
    assert view._origin_generation == view._source_generation
    view.shutdown()


class OwnershipFailure:
    def __init__(self):
        self.error = RuntimeError("Source timeline cleanup failed; child ownership retained")
        self.poisoned = True
        self.calls = 0

    def shutdown(self):
        self.calls += 1
        raise self.error


@pytest.mark.parametrize("playing", [False, True])
def test_timeline_ownership_failure_still_finishes_every_qt_cleanup(app, tmp_path, playing):
    view, player, audio = controller()
    sink = QVideoSink()
    source(view, player, sink, tmp_path / "source.mp4")
    timeline(view)
    view._staging_sink.setVideoFrame(presented_frame(0))
    if playing:
        view.togglePlayback()
    view._indexing = True
    service = OwnershipFailure()
    view._timeline_service = service
    stale_player = dict(view._player_signal_handlers)
    stale_frame = view._frame_handler
    stale_audio = dict(view._audio_signal_handlers)
    generation = view._source_generation
    with pytest.raises(RuntimeError) as first:
        view.shutdown()
    assert first.value is service.error and service.poisoned
    assert view._closed and view.state == "Empty" and not view.playing and not view.canPlay
    assert view._source_generation > generation
    assert player._state == QMediaPlayer.StoppedState
    assert player.audio is None and player.video_sink is None
    assert ("source", "") in player.events and not sink.videoFrame().isValid()
    assert view._frame_handler is None and not view._player_signal_handlers
    assert view._timeline is None and not view._origin_established
    assert not view._prime_timer.isActive() and not view._step_timer.isActive()
    changes = []
    view.changed.connect(lambda: changes.append(True))
    stale_player["position"](1234)
    stale_player["playback_state"](QMediaPlayer.PlayingState)
    stale_player["error"](QMediaPlayer.FormatError, "stale")
    stale_frame(presented_frame(40000))
    stale_audio["volume"](0.5)
    audio.setVolume(0.5)
    app.processEvents()
    assert not changes and view.state == "Empty" and view.position == 0
    events = list(player.events)
    with pytest.raises(RuntimeError) as repeated:
        view.shutdown()
    assert repeated.value is first.value and player.events == events and service.calls == 1


@pytest.mark.parametrize("action", ["stop", "setSource", "setAudioOutput"])
def test_secondary_qt_failure_retains_primary_and_remaining_cleanup_is_retryable(app, tmp_path, action):
    view, player, _ = controller()
    sink = QVideoSink()
    source(view, player, sink, tmp_path / "source.mp4", first=0)
    view.togglePlayback()
    service = OwnershipFailure()
    view._timeline_service = service
    original = getattr(player, action)
    secondary = RuntimeError(f"injected {action} failure")
    calls = []

    def fail_once(*args):
        calls.append(args)
        if len(calls) == 1:
            raise secondary
        return original(*args)

    setattr(player, action, fail_once)
    with pytest.raises(RuntimeError) as first:
        view.shutdown()
    assert first.value is service.error and view.state == "Empty"
    assert any(error is secondary for _, error in view._shutdown_secondary_errors)
    assert any(action in note or "Playback teardown" in note for note in first.value.__notes__)
    assert len(view._shutdown_pending) == 1
    with pytest.raises(RuntimeError) as repeated:
        view.shutdown()
    assert repeated.value is first.value and len(calls) == 2 and not view._shutdown_pending
    assert player._state == QMediaPlayer.StoppedState and player.audio is None
    assert player.video_sink is None and not sink.videoFrame().isValid()
    with pytest.raises(RuntimeError):
        view.shutdown()
    assert len(calls) == 2 and service.poisoned
