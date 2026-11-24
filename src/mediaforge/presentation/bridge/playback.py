"""Presentation ownership and stable state for the Qt Multimedia viewer."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Property, QTimer, QUrl, Signal, Slot, Qt
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickWindow
from mediaforge.application.contracts.source_timeline import SourceSignature
from mediaforge.presentation.bridge.default_audio import DefaultAudioDeviceFollower
from mediaforge.presentation.bridge.sequence_transport import SequenceTransport


@dataclass(frozen=True)
class AdmittedVideoFrame:
    """A current decoded picture already published to the sole Viewer sink."""

    frame: QVideoFrame
    source_generation: int
    player_generation: int
    player: QMediaPlayer
    staging: QVideoSink


@dataclass
class _ExactPictureSeek:
    decoder_target_ms: int
    restoring: bool
    correction_queued: bool = False


class _FrameAdmissionReceiver(QObject):
    """Immutable delivery owner; AutoConnection marshals decoding to the GUI."""

    def __init__(self, controller, staging):
        super().__init__(controller)
        self.controller = controller
        self.source_generation = controller._source_generation
        self.player_generation = controller._player_generation
        self.player = controller._player
        self.staging = staging

    @Slot(QVideoFrame)
    def receive(self, frame):
        self.controller._admit_staged_frame(self, frame)


class _ImageRenderReceiver(QObject):
    """GUI-thread render progress only; never a CPU/GPU retirement certificate."""
    def __init__(self, controller, window):
        super().__init__(controller)
        self.controller = controller
        self.window = window
        self.generation = controller._source_generation
        self.serial = controller._image_serial
        self.swaps = 0
        window.frameSwapped.connect(self.swapped, Qt.ConnectionType.QueuedConnection)

    @Slot()
    def swapped(self):
        c = self.controller
        if self is not c._image_receiver or self.generation != c._source_generation:
            return
        self.swaps += 1
        if self.swaps == 1:
            # One already-in-flight render can precede the new source/layer state.
            # Ask for another progress observation; no resource lease is freed.
            self.window.update()
            return
        c._on_image_painted(self)


class PlaybackController(QObject):
    """Own one player/audio pair and expose presentation-friendly viewer state."""

    changed = Signal()
    fullscreenChanged = Signal()
    controlsVisibleChanged = Signal()
    timelineFinished = Signal(object, int, int)
    admittedVideoFrame = Signal(object)

    def __init__(
        self,
        player: QMediaPlayer,
        audio_output: QAudioOutput,
        logger: logging.Logger | None = None,
        parent: QObject | None = None,
        controls_idle_ms: int = 1200,
        player_factory: Callable[[], QMediaPlayer] | None = None,
        player_disposer: Callable[[QMediaPlayer], None] | None = None,
        timeline_service=None,
        audio_devices=None,
        posters=None,
    ) -> None:
        super().__init__(parent)
        self.posters = posters
        self.sequence = SequenceTransport(self)
        self._image_prepared = None
        self._image_mode = False
        self._image_ready = False
        self._image_serial = 0
        self._retired_images = []
        self._image_window = None
        self._image_receiver = None
        self._timeline_service = timeline_service
        self._timeline = None
        self._indexing = False
        self._timeline_generation = 0
        self._timeline_item_id = None
        self._timeline_signature = None
        self._timeline_offset = None
        self._first_presented_start = None
        self._first_presented_generation = None
        self._origin_generation = None
        self._current_index = None
        self._step_target_index = None
        self._queued_step = None
        self._step_serial = 0
        self._step_expected_start = None
        self._origin_established = False
        self._priming = False
        self._prime_requested = False
        self._prime_timer = QTimer(self)
        self._prime_timer.setSingleShot(True)
        self._prime_timer.setInterval(10000)
        self._prime_timer.timeout.connect(lambda: self._set_error("The first video frame could not be shown.") if self._priming else None)
        self.timelineFinished.connect(self._on_timeline_result)
        self.admittedVideoFrame.connect(self._on_admitted_video_frame)
        self._desired_playing = False
        self._loop_active = False
        self._trim_enabled = False
        self._trim_in = self._trim_out = 0
        self._trim_picture_bounds_us = (None, None)
        self._loop_seeking = False
        self._seek_waiting = False
        self._clock_seek = False
        self._controls_zone = False
        self._controls_idle_ms = controls_idle_ms
        self._player = player
        self._player_factory = player_factory or type(player)
        self._player_disposer = player_disposer or _dispose_qt_player
        self._audio = audio_output
        self._logger = logger or logging.getLogger("mediaforge")
        self._source: Path | None = None
        self._state = "Empty"
        self._position = 0
        self._request_position = None
        self._restore_session = None
        self._exact_picture_seek: _ExactPictureSeek | None = None
        self._exact_seek_failure = ""
        self._exact_seek_timer = QTimer(self)
        self._exact_seek_timer.setSingleShot(True)
        self._exact_seek_timer.setInterval(3000)
        self._exact_seek_timer.timeout.connect(self._exact_seek_did_not_settle)
        self._requested_offset_hint = None
        self._resume_after_seek = False
        self._duration = 0
        self._error_message = ""
        self._fullscreen = False
        self._controls_visible = True
        self._controls_interactions = 0
        self._closed = False
        self._shutting_down = False
        self._shutdown_running = False
        self._shutdown_error: Exception | None = None
        self._shutdown_secondary_errors: list[tuple[str, Exception]] = []
        self._shutdown_pending: list[tuple[str, Callable[[], None]]] | None = None
        self._source_generation = 0
        self._player_generation = 0
        self._replacing_source = False
        self._play_requested_generation: int | None = None
        self._played_generation: int | None = None
        self._player_signal_handlers: dict[str, object] = {}
        self._video_sink: QVideoSink | None = None
        self._video_output: QObject | None = None
        self._navigation_feedback = ""
        self._presentation_sink: QVideoSink | None = None
        self._frame_handler = None
        self._admission_receiver = None
        self._staging_sink = None
        self._presented_start: int | None = None
        self._step_pending: int | None = None
        self._step_failed = False
        self._step_timer = QTimer(self)
        self._step_timer.setSingleShot(True)
        self._step_timer.setInterval(1500)
        self._step_timer.timeout.connect(self._step_did_not_settle)

        self._audio.setParent(self)
        self._last_nonzero_volume = max(1, round(self._audio.volume() * 100)) if self._audio.volume() > 0 else 100
        self._audio.setMuted(self._audio.volume() <= 0)
        self._audio_follower = (DefaultAudioDeviceFollower(self._audio, self._logger, self, devices=audio_devices)
                                if callable(getattr(self._audio, 'setDevice', None)) else None)

        self._configure_player(self._player)
        self._bind_player_signals()
        self._audio_signal_handlers = {
            "volume": lambda _value: self._notify_audio_change(),
            "muted": lambda _value: self._notify_audio_change(),
        }
        self._audio.volumeChanged.connect(self._audio_signal_handlers["volume"])
        self._audio.mutedChanged.connect(self._audio_signal_handlers["muted"])

        self._controls_timer = QTimer(self)
        self._controls_timer.setSingleShot(True)
        self._controls_timer.setInterval(controls_idle_ms)
        self._controls_timer.timeout.connect(self._hide_controls_after_idle)

    @Property(str, notify=changed)
    def state(self) -> str:
        return self._state

    @Property(bool, notify=changed)
    def playing(self) -> bool:
        return self._state == "Playing"

    @Property(bool, notify=changed)
    def canPlay(self) -> bool:
        if self.sequence.item is not None:
            return not self._closed and not self._shutting_down and self.sequence.item.sequence.confirmed
        return (not self._image_mode and not self._closed and not self._shutting_down and self._source is not None
                and self._state not in ("Loading", "Priming", "Error"))

    @Property(bool, notify=changed)
    def imageMode(self):
        return self._image_mode

    @Property(bool, notify=changed)
    def videoPictureAvailable(self):
        # Presentation availability is deliberately weaker than decoder/PTS
        # readiness. A source-bound poster never authorizes current-frame Export.
        return bool(self._source is not None and not self._image_mode and not getattr(self,'_audio_only',False)
                    and self._presentation_sink is not None and self._presentation_sink.videoFrame().isValid())

    @Property(bool, notify=changed)
    def timelineAvailable(self):
        return (not self._image_mode and self._timeline is not None and self._timeline_signature is not None
                and self._origin_established and self._origin_generation==self._source_generation
                and self._timeline_offset is not None)

    def export_picture_snapshot(self):
        """Freeze the admitted ordinal/clock on the GUI thread, not Viewer pixels."""
        from mediaforge.application.contracts.image_export import PictureSnapshot
        if not self.timelineAvailable:raise ValueError('Exact source timeline is unavailable.')
        return PictureSnapshot(self._timeline_signature,self._timeline,self._current_index,
            self._source_generation,self._player_generation,self._timeline_offset or 0)

    @Property(bool, notify=changed)
    def sequenceMode(self): return self.sequence.item is not None

    @Property(int, notify=changed)
    def imageWidth(self):
        return (self._image_prepared.raster.plan.width if hasattr(self._image_prepared.raster,'plan')
                else self._image_prepared.metadata.width) if self._image_prepared else 0

    @Property(int, notify=changed)
    def imageHeight(self):
        return (self._image_prepared.raster.plan.height if hasattr(self._image_prepared.raster,'plan')
                else self._image_prepared.metadata.height) if self._image_prepared else 0

    @Property('QVariantMap',notify=changed)
    def imageRegion(self):
        if self._image_prepared is None:return dict(x=0,y=0,width=1,height=1)
        r=self._image_prepared.raster
        region=getattr(getattr(r,'plan',None),'region',None) or (0,0,r.source.facts.width,r.source.facts.height)
        return dict(x=region[0],y=region[1],width=region[2],height=region[3])

    @Property(bool, notify=changed)
    def imageAlpha(self):
        return bool(self._image_prepared and self._image_prepared.raster.source.facts.alpha)

    @Property("qlonglong", notify=changed)
    def imageReservedBytes(self):
        return self._image_prepared.budget.reserved if self._image_prepared else 0

    def prepare_image(self, prepared):
        """Validate display allocation without changing the active publisher."""
        if hasattr(prepared.raster,'display'):
            frame = QVideoFrame(prepared.raster.display)
            if not frame.isValid(): raise ValueError('Image display frame allocation failed.')
            return frame
        facts = prepared.raster.source.facts
        image = QImage(prepared.raster.rgba, facts.width, facts.height,
                       facts.width * 4, QImage.Format_RGBA8888)
        display = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        frame = QVideoFrame(display)
        if display.isNull() or not frame.isValid():
            raise ValueError('The image cannot be presented by the existing Viewer.')
        return frame

    def update_image_preview(self,prepared,frame,sequence_slot=None):
        sequence_member = (self.sequence.item is not None and sequence_slot is not None
            and 0 <= sequence_slot < self.sequence.item.metadata.source.manifest.count
            and prepared.metadata.source == self.sequence.item.metadata.source.manifest.members[sequence_slot])
        allowed = sequence_member if self.sequence.item is not None else (
            sequence_slot is None and self._image_prepared is not None
            and prepared.metadata.source == self._image_prepared.metadata.source)
        if self._closed or not self._image_mode or not allowed:
            raise ValueError('Stale image quality request.')
        old = self._image_prepared
        self._image_prepared = prepared
        self._image_serial += 1
        self._arm_image_retirement()
        self.changed.emit()
        self._presentation_sink.setVideoFrame(frame)
        # Drop only application-owned reference. All deferred Qt backing/textures
        # stay charged in the session-wide renderer envelope, independent of swaps.
        old.release()

    def set_image(self, prepared, frame=None, start_us=0):
        if self._closed or self._shutting_down:
            prepared.release()
            raise ValueError('Viewer is closed.')
        self._audio_only=False
        frame = self.prepare_image(prepared) if frame is None else frame
        if self._image_window is None and callable(getattr(self._video_output, 'window', None)):
            self._image_window = self._video_output.window()
        self._priming = False
        self._prime_timer.stop()
        self._replace_source(None, new_generation=True, image=True)
        self._image_prepared = prepared
        self._source = Path(prepared.metadata.source_path)
        self._image_serial += 1
        self._image_mode = True
        self._image_ready = False
        self._error_message = ''
        self._state = 'Priming'
        self._arm_image_retirement()
        self.changed.emit()  # Enable the bounded layer before publication.
        generation = self._source_generation
        if self._presentation_sink is not None:
            frame.setStartTime(start_us)
            self._presentation_sink.setVideoFrame(frame)
        if generation == self._source_generation and self._image_mode:
            self.changed.emit()

    def _disconnect_image_retirement(self):
        receiver = self._image_receiver
        self._image_receiver = None
        if receiver is not None:
            try:
                receiver.window.frameSwapped.disconnect(receiver.swapped)
            except (RuntimeError, TypeError):
                pass
            _dispose_qt_object(receiver)

    def _arm_image_retirement(self):
        self._disconnect_image_retirement()
        if self._image_window is not None:
            self._image_receiver = _ImageRenderReceiver(self, self._image_window)

    def _on_image_painted(self, receiver):
        if self._image_mode:
            self._image_ready = True
            self._state = 'Playing' if self.sequence.playing else 'Paused'
            self.changed.emit()
        self._disconnect_image_retirement()

    @Property(int, notify=changed)
    def position(self) -> int:
        if self.sequence.item is not None: return self.sequence.pending_slot if self.sequence.pending_slot is not None else self.sequence.slot
        return self._request_position if self._request_position is not None else self._position

    @Property(int, notify=changed)
    def duration(self) -> int:
        if self.sequence.item is not None: return self.sequence.item.metadata.source.manifest.count
        return self._duration

    @Property(str, notify=changed)
    def positionText(self) -> str:
        if self.sequence.item is not None:
            return _format_time(int(self.sequence.item.metadata.source.manifest.time(self.position,self.sequence.item.sequence.rate)*1000))
        return _format_time(self.position)

    @Property(str, notify=changed)
    def durationText(self) -> str:
        if self.sequence.item is not None:
            return _format_time(int(self.sequence.item.metadata.source.manifest.time(self.duration,self.sequence.item.sequence.rate)*1000))
        return _format_time(self._duration)

    @Property(int, notify=changed)
    def volume(self) -> int:
        return round(self._audio.volume() * 100)

    @Property(bool, notify=changed)
    def muted(self) -> bool:
        return self._audio.isMuted()

    @Property(int, notify=changed)
    def volumePercent(self) -> int:
        return self.volume

    @Property(int, notify=changed)
    def lastNonZeroVolume(self) -> int:
        return self._last_nonzero_volume

    @Property(bool, notify=changed)
    def effectiveMuted(self) -> bool:
        return self.volume == 0

    @Property(bool, notify=changed)
    def trimLoopActive(self) -> bool:
        if self.sequence.item is not None: return self.sequence.loop
        return self._loop_active

    @Property(str, notify=changed)
    def loopMode(self) -> str:
        return "TrimRangeLoop" if self.trimLoopActive else "WholeSourceLoop"

    def configure_trim_loop(self, enabled: bool, in_ms: int, out_ms: int, *, picture_bounds_us=(None, None)) -> None:
        self._trim_enabled = enabled and 0 <= in_ms < out_ms
        self._trim_in, self._trim_out = in_ms, out_ms
        self._trim_picture_bounds_us = picture_bounds_us
        if self._loop_active and (not self._trim_enabled or not in_ms <= self._position < out_ms):
            self._loop_active = False
            self._configure_normal_loop()
        self.changed.emit()

    def _trim_contains_frame(self, timestamp_us):
        start, end = self._trim_picture_bounds_us
        exact_us = timestamp_us
        if (start is not None or end is not None) and self.timelineAvailable:
            index = self._timeline.map_timestamp(timestamp_us - self._timeline_offset)
            if index is None:
                return False
            exact_us = self._timeline.timestamps[index] + self._timeline_offset
        return ((self._trim_in * 1000 <= timestamp_us if start is None else start <= exact_us)
                and (timestamp_us < self._trim_out * 1000 if end is None else exact_us < end))


    def _manual_navigation(self, target: int) -> None:
        if self._loop_active and not self._trim_in <= target < self._trim_out:
            self._loop_active = False
            self._configure_normal_loop()
            self.changed.emit()

    @Property(str, notify=changed)
    def errorMessage(self) -> str:
        return self._error_message

    @Property(bool, notify=fullscreenChanged)
    def fullscreen(self) -> bool:
        return self._fullscreen

    @Property(bool, notify=controlsVisibleChanged)
    def controlsVisible(self) -> bool:
        return self._controls_visible

    @Property(bool, notify=changed)
    def framesReliable(self) -> bool:
        if self.sequence.item is not None: return self._image_prepared is not None
        if (not self._origin_established or self._origin_generation != self._source_generation
                or self._timeline_offset is None or self._timeline is None
                or self._current_index is None or self._step_failed or self._seek_waiting
                or self._exact_seek_failure):
            return False
        if self._presentation_sink is not None:
            actual = self._presentation_sink.videoFrame()
            return actual.isValid() and actual.startTime() == self._presented_start
        return False

    @Property(bool, notify=changed)
    def globalFrameOrdinalReliable(self) -> bool:
        return self.framesReliable

    @Property(bool, notify=changed)
    def totalFrameCountReliable(self) -> bool:
        if self.sequence.item is not None: return True
        return self._timeline is not None

    @Property(bool, notify=changed)
    def exactStepAvailable(self) -> bool:
        if self.sequence.item is not None: return self.canPlay
        return self.canPlay and self.framesReliable and not self._seek_waiting

    @Property(str, notify=changed)
    def frameStepStatus(self) -> str:
        if self.sequence.item is not None:
            m=self.sequence.item.metadata.source.manifest
            return f'Displayed frame {self.sequence.slot+1} / {m.count} · Source {m.numbers[self.sequence.slot]:0{m.padding}d}. Requested frame {self.position+1}. Decode may slow playback.'
        if self._indexing: return "Indexing frames…"
        if self._timeline is None: return "Frame timeline unavailable"
        if self._exact_seek_failure: return self._exact_seek_failure
        if not self.exactStepAvailable: return "Current frame mapping unavailable"
        return f"Displayed frame {self._current_index+1}. Requested frame {self.requestedFrameIndex+1}. Exact presentation-frame navigation available."

    @Property(str, notify=changed)
    def exactFramesText(self) -> str:
        if self.sequence.item is not None: return str(self.duration)
        if self._indexing: return 'Indexing…'
        return str(self._timeline.count) if self._timeline is not None else 'Unavailable'

    @Property(str, notify=changed)
    def frameText(self) -> str:
        if getattr(self,'_audio_only',False):return 'Audio'
        if self.sequence.item is not None:
            m = self.sequence.item.metadata.source.manifest
            requested=self.position
            return f'Frame {requested+1} / {m.count}'
        if self._image_mode:
            return 'Image 1 / 1'
        if self._indexing: return "Indexing frames…"
        if self._timeline is None: return "Frame timeline unavailable"
        if self._exact_seek_failure: return "Seek failed · seek or play"
        index=self.requestedFrameIndex
        current=index+1 if index >= 0 else '—'
        return f"Frame {current} / {self._timeline.count}"

    @Property(int, notify=changed)
    def requestedFrameIndex(self):
        if self._timeline is None:return -1
        offset=self._timeline_offset if self._origin_established else self._requested_offset_hint
        if offset is None:return -1
        return self._timeline.index_at_time(self.position*1000-offset)

    def restore_session(self, position, *, playing=False, request_offset=None):
        """Compact source-generation intent. The first prime only anchors PTS;
        an inactive player is never kept alive to remember a seek position.
        """
        # Previously proven same-source correspondence is logical UI intent only;
        # current decoder admission still requires its own new prime/origin.
        self._requested_offset_hint=request_offset
        self._restore_session=(max(0,int(position)),bool(playing))
        self._request_position=max(0,int(position))
        self._continue_restore()
        self.changed.emit()

    def _continue_restore(self):
        if getattr(self,'_audio_only',False):
            if self._restore_session is not None and self.canPlay and self._duration>0:
                target,playing=self._restore_session;self._restore_session=None
                self.seek(target)
                if playing:self.togglePlayback()
            return
        if self._restore_session is None or self._priming or not self.canPlay or self._duration<=0:
            return
        target,playing=self._restore_session
        self._restore_session=None
        generation = self._source_generation
        serial = self._seek(target, restore_picture=True)
        # Restored Play/Trim is armed only after its requested picture arrives.
        # Arming the loop on the prime/old poster could seek to Trim In instead.
        if generation==self._source_generation and serial==self._step_serial and self.position==target:
            actual=self._presentation_sink.videoFrame() if self._presentation_sink is not None else QVideoFrame()
            if (self._current_index==self.requestedFrameIndex and actual.isValid()
                    and actual.startTime()==self._presented_start):
                self._settle_requested_picture()  # already admitted same-generation target (e.g. frame 1)
            self._resume_after_seek=playing
            if playing and self._clock_seek:
                # Time-only playback can progress before exact indexing; do
                # not wait on an ordinal that this decoder cannot yet prove.
                self._resume_after_seek=False
                self.togglePlayback()
                return
            if playing and not self._seek_waiting:
                self._resume_after_seek=False
                self.togglePlayback()


    def configure_timeline(self,item_id,source_path):
        if getattr(self,'_audio_only',False):return
        if self.posters is not None:
            self.posters.activate(source_path)
        self._timeline_generation+=1
        index_generation=self._timeline_generation
        player_generation=self._source_generation
        self._timeline_item_id=item_id
        self._timeline=None
        self._current_index=None
        self._timeline_signature=None
        self._indexing=self._timeline_service is not None
        self._cancel_step()
        self._step_failed=False
        self.changed.emit()
        if self._timeline_service is not None:
            try:
                self._timeline_signature=SourceSignature.capture(source_path)
                self._timeline_service.activate(item_id,source_path,
                    lambda result:self.timelineFinished.emit(result,player_generation,index_generation))
            except (OSError,ValueError):
                self._indexing=False
                self.changed.emit()

    @Slot(object,int,int)
    def _on_timeline_result(self,result,player_generation,index_generation):
        if (self._closed or self._shutting_down or player_generation!=self._source_generation
                or index_generation!=self._timeline_generation
                or result.request.item_id!=self._timeline_item_id): return
        try:
            if SourceSignature.capture(result.request.source.canonical_path)!=result.request.source:
                self._indexing=False
                self._timeline=None
                self.changed.emit()
                return
        except OSError:
            self._indexing=False
            self.changed.emit()
            return
        self._indexing=False
        self._timeline=result.timeline
        self._timeline_signature=result.request.source
        # The accepted paused first-picture prime gives one source-generation
        # correspondence. It is never re-fit from a later seek or nominal FPS.
        self._establish_timeline_origin()
        self._map_current_frame()
        self._settle_requested_picture()
        if self._exact_picture_seek is not None and self._timeline is not None and not self._exact_seek_timer.isActive():
            self._exact_seek_timer.start()
        if (self._clock_seek and self._seek_waiting and self._request_position is not None
                and self._timeline is not None and self._origin_established and not self._desired_playing):
            # A time-only seek can land outside the selected exact interval.
            # Retry once now that the original clock is known. Keep the
            # logical seek position while addressing the interval midpoint.
            if self._exact_picture_seek is not None:
                self._correct_exact_picture(self._admission_receiver)
            else:
                target = self.requestedFrameIndex
                try:
                    decoder_target = self._timeline.seek_ms(target, self._timeline_offset)
                except ValueError:
                    decoder_target = None
                if decoder_target is not None and self._player is not None:
                    self._clock_seek = False
                    self._player.setPosition(decoder_target)
        self._continue_restore()
        self.changed.emit()

    def _map_current_frame(self):
        self._current_index=(self._timeline.map_timestamp(self._presented_start-self._timeline_offset)
            if self._origin_established and self._origin_generation == self._source_generation
            and self._timeline_offset is not None and self._timeline is not None
            and self._presented_start is not None and self._presented_start >= 0 else None)

    def _settle_requested_picture(self):
        if (not self._exact_seek_failure and self._request_position is not None and self._current_index is not None
                and self._current_index == self.requestedFrameIndex):
            if self._desired_playing:
                self._request_position = None
            self._seek_waiting = False
            self._clock_seek = False
            if self._exact_picture_seek is not None:
                self._cancel_exact_seek()

    def _cancel_exact_seek(self) -> None:
        self._exact_seek_timer.stop()
        self._exact_picture_seek = None
        self._exact_seek_failure = ""

    def _exact_seek_did_not_settle(self) -> None:
        seek = self._exact_picture_seek
        if seek is None:
            return
        self._logger.info("Exact picture seek timed out at %d ms (restore=%s)", self.position, seek.restoring)
        self._cancel_exact_seek()
        self._exact_seek_failure = ("Saved frame could not be restored. Seek or play to continue." if seek.restoring
                                    else "Exact picture could not be shown. Seek or play to continue.")
        self._seek_waiting = self._clock_seek = self._resume_after_seek = False
        if self._desired_playing:
            self.pauseForExternal()
        self.changed.emit()

    def _correct_exact_picture(self, receiver, observed_us=None) -> None:
        seek = self._exact_picture_seek
        if seek is None or seek.correction_queued or receiver is None:
            return
        timeline = self._timeline
        index = self.requestedFrameIndex
        try:
            decoder_target = timeline.seek_ms(index, self._timeline_offset)
        except ValueError:
            return  # No integer-millisecond address inside the exact interval.
        if decoder_target == seek.decoder_target_ms:
            return
        seek.correction_queued = True

        def correct():
            if (seek is not self._exact_picture_seek or timeline is not self._timeline
                    or receiver is not self._admission_receiver or not self._accept_frame_owner(receiver)):
                return
            self._clock_seek = False
            self._logger.info("%s at %d ms: requested ordinal %d (%d us), observed %s us, seek %d ms",
                              "Saved picture restore" if seek.restoring else "Manual exact seek",
                              self.position, index + 1, timeline.timestamps[index], observed_us, decoder_target)
            receiver.player.setPosition(decoder_target)

        # Leave the decoder callback before seeking. A short delay on manual
        # seeks also lets rapid scrub updates supersede queued corrections.
        QTimer.singleShot(0 if seek.restoring else 75, self, correct)

    def _establish_timeline_origin(self):
        if (not self._origin_established and self._timeline is not None
                and self._first_presented_generation == self._source_generation
                and self._first_presented_start is not None and self._first_presented_start >= 0):
            self._timeline_offset = self._first_presented_start - self._timeline.timestamps[0]
            self._origin_generation = self._source_generation
            self._origin_established = True

    def forget_timeline(self, item_id):
        if self._timeline_service is not None:
            self._timeline_service.forget(item_id)

    def timeline_source_changed(self, item_id, path):
        return self._timeline_service is not None and not self._timeline_service.source_matches(item_id,path)

    def _cancel_step(self) -> None:
        self._step_timer.stop()
        self._step_serial+=1
        self._step_pending=None
        self._step_expected_start=None
        self._step_target_index=None
        self._queued_step=None

    def _step_did_not_settle(self) -> None:
        self._cancel_step()
        self._step_failed=True
        self._navigation_feedback="Frame step unavailable"
        self.changed.emit()

    def _disconnect_frame_signal(self) -> None:
        receiver = self._admission_receiver
        if receiver is not None:
            try:
                receiver.staging.videoFrameChanged.disconnect(receiver.receive)
            except (RuntimeError, TypeError):
                pass
        self._admission_receiver = None
        self._frame_handler = None
        if receiver is not None:
            _dispose_qt_object(receiver)

    def _bind_frame_signal(self) -> None:
        self._disconnect_frame_signal()
        sink = (self._video_output.property("videoSink")
                if self._video_output is not None else self._video_sink)
        if isinstance(sink, QVideoSink):
            self._presentation_sink = sink
        if self._staging_sink is not None:
            receiver = _FrameAdmissionReceiver(self, self._staging_sink)
            self._admission_receiver = receiver
            self._frame_handler = receiver.receive
            self._staging_sink.videoFrameChanged.connect(receiver.receive)

    def _accept_frame_owner(self, owner) -> bool:
        return (self._accept_player_signal(owner.source_generation, owner.player)
                and owner.player_generation == self._player_generation
                and owner.staging is self._staging_sink)

    def _admit_staged_frame(self, receiver, frame: QVideoFrame) -> None:
        if (receiver is not self._admission_receiver or not self._accept_frame_owner(receiver)
                or getattr(self,'_audio_only',False)
                or self._source is None or self._state == "Loading"
                or self._exact_seek_failure
                or self._presentation_sink is None or not frame.isValid()):
            return
        # Staging is non-rendering. An untrustworthy timestamp cannot prove
        # membership in an armed range; neither clocks nor seek targets do so.
        if self._loop_active:
            start = frame.startTime()
            if start < 0:
                return
            if not self._trim_contains_frame(start):
                self._return_to_trim_in()
                return
        if (not self._priming and self._request_position is not None
                and not (self._clock_seek and self.playing and self._desired_playing)
                and (self._seek_waiting or not self._desired_playing) and self._timeline is not None
                and self._origin_established and self._origin_generation==self._source_generation):
            index=self._timeline.map_timestamp(frame.startTime()-self._timeline_offset) if frame.startTime()>=0 else None
            requested = self.requestedFrameIndex
            if index != requested:
                if index is not None and abs(index - requested) == 1:
                    self._correct_exact_picture(receiver, frame.startTime())
                return  # Obsolete same-player seek result must not paint or authorize Export.
        if self._priming and self._restore_session is not None and self._restore_session[0]>0:
            # Observe first-picture correspondence without painting frame 1 over
            # a restored session or a same-source retained poster.
            self._first_presented_start=frame.startTime() if frame.startTime() >= 0 else None
            self._first_presented_generation=receiver.source_generation
            self._establish_timeline_origin()
            self._priming=False
            self._prime_timer.stop()
            self._set_state("Paused")
            if not self._accept_frame_owner(receiver):return
            self._continue_restore()
            if (not self._accept_frame_owner(receiver) or self._restore_session is not None
                    or self.requestedFrameIndex!=0):return
        self._presentation_sink.setVideoFrame(frame)
        # Viewer observers can re-enter source replacement/shutdown. Never
        # publish old ownership after such a transition.
        if receiver is self._admission_receiver and self._accept_frame_owner(receiver):
            self.admittedVideoFrame.emit(AdmittedVideoFrame(
                QVideoFrame(frame), receiver.source_generation, receiver.player_generation,
                receiver.player, receiver.staging))

    @Slot(object)
    def _on_admitted_video_frame(self, admitted: AdmittedVideoFrame) -> None:
        if (not self._accept_frame_owner(admitted)
                or self._source is None or self._state == "Loading"):
            return
        generation, frame = admitted.source_generation, admitted.frame
        if self._presentation_sink is not None and frame != self._presentation_sink.videoFrame():
            return  # A queued delivery superseded by the sink's current picture.
        if self._loop_active and self._loop_seeking and frame.isValid() and frame.startTime() < (self._trim_in + 100) * 1000:
            self._loop_seeking = False
        # Qt's unknown/uninitialized timestamp (including negative sentinels)
        # cannot supply a correspondence, even when the bitmap itself is valid.
        self._presented_start=frame.startTime() if frame.isValid() and frame.startTime() >= 0 else None
        if self._priming and frame.isValid():
            # Only the source-open first-picture prime may anchor normalization.
            # Complete priming even with unknown timing, but never adopt an
            # arbitrary later picture or requested player clock as origin proof.
            self._first_presented_start=self._presented_start
            self._first_presented_generation=generation if self._presented_start is not None else None
            self._establish_timeline_origin()
            self._priming=False
            self._prime_timer.stop()
            self._set_state("Paused")
            if not self._accept_frame_owner(admitted):return
        self._map_current_frame()
        if self._restore_session is not None:self._continue_restore()
        if not self._accept_frame_owner(admitted):return
        if self.posters is not None:
            self.posters.remember(self._timeline_signature,frame,self._current_index,self._timeline,self._timeline_offset)
        # A paused seek stays authoritative over later Qt position callbacks.
        self._settle_requested_picture()
        completed=(self._step_pending is not None and self._current_index is not None
            and self._current_index==self._step_target_index)
        queued=self._queued_step if completed else None
        if completed: self._cancel_step()
        self.changed.emit()
        if not self._accept_frame_owner(admitted):return
        if self._resume_after_seek and not self._seek_waiting and self.framesReliable:
            self._resume_after_seek=False
            self.togglePlayback()
        if queued is not None:
            generation,serial=admitted.source_generation,self._step_serial
            QTimer.singleShot(0,lambda:self.stepFrame(queued) if not self._closed
                and generation==self._source_generation and serial==self._step_serial else None)

    def begin_loading(self) -> None:
        if self._closed:
            return
        self._priming = False
        self._prime_timer.stop()
        self._replace_source(None, new_generation=True)
        self._set_state("Loading")

    def set_source(self, source_path: str, *, keep_picture=False, poster=None, logical_source=None, audio_only=False) -> None:
        if self._closed:
            return
        source = Path(source_path)
        self._audio_only=bool(audio_only)
        # Establish immutable poster/source ownership before Qt can open or read
        # this URL. Proxy handoffs retain the original logical source's lock.
        if self.posters is not None:self.posters.activate(logical_source or source_path)
        new_generation = self._state != "Loading" or self._player is None
        self._priming = isinstance(self._player, QMediaPlayer) and not self._audio_only
        self._prime_requested = False
        if self._priming:
            self._set_state("Priming")
            self._prime_timer.start()
        self._replace_source(source, new_generation=new_generation, keep_picture=keep_picture, poster=poster)
        self._error_message = ""
        if not self._priming:
            self._set_state("Ready")

    def clear_source(self) -> None:
        if self._closed:
            return
        self._audio_only=False
        self._priming = False
        self._prime_timer.stop()
        self._replace_source(None, new_generation=True)
        self._set_state("Empty")

    def retire_for_image(self) -> None:
        """Revoke decoder ownership; no claim that GPU residency has retired."""
        position,duration=self.position,self._duration
        timeline,signature=self._timeline,self._timeline_signature
        offset=self._timeline_offset if self._origin_established else self._requested_offset_hint
        self._priming = False
        self._prime_timer.stop()
        if self.posters is not None:
            self.posters.drop_decoder_backing()
        self._replace_source(None,new_generation=True,retire_backend=True)
        # Only compact logical intent survives the explicit neutral transition.
        # Origin, decoded index and player stay revoked for frame-dependent Export.
        self._request_position=position;self._duration=duration
        self._timeline=timeline;self._timeline_signature=signature
        self._requested_offset_hint=offset
        self._set_state('Loading')
        self.changed.emit()

    def _replace_source(self, source: Path | None, *, new_generation: bool, image: bool = False, keep_picture=False, poster=None, retire_backend=False) -> None:
        self.sequence.clear()
        # Stop and detach first so a failed/replaced metadata request cannot keep
        # rendering or playing the previous current-media source.
        had_source = self._source is not None
        self._replacing_source = True
        try:
            sink = self._presentation_sink
            self._disconnect_frame_signal()
            self._disconnect_image_retirement()
            # Only an explicitly same-logical-source preview-tier handoff may
            # retain the current sink backing (one charged poster, no extra copy).
            if sink is not None:
                if poster is not None and poster.isValid():
                    # Same-source, source-locked native CPU backing. This never
                    # emits admission, anchors timing or authorizes Export.
                    sink.setVideoFrame(poster)
                elif not (keep_picture and had_source and not self._image_mode):
                    sink.setVideoFrame(QVideoFrame())
            if self._image_prepared is not None:
                self._image_prepared.release()
                self._image_prepared = None
                self._image_serial += 1
            self._image_mode = image
            self._image_ready = False
            self._cancel_step()
            self._cancel_exact_seek()
            if self._timeline_service is not None:
                if source is not None and not getattr(self, '_audio_only', False):
                    self._timeline_service.cancel(keep_scan=True)
                else:
                    self._timeline_service.cancel()
            self._timeline_generation+=1
            self._timeline=None
            self._indexing=False
            self._current_index=None
            self._first_presented_start=None
            self._first_presented_generation=None
            self._timeline_offset=None
            self._origin_generation=None
            self._origin_established=False
            self._desired_playing=False
            self._loop_active = False
            self._loop_seeking = False
            self._seek_waiting = False
            self._clock_seek = False
            self._presented_start = None
            self._step_failed = False
            if new_generation:
                self._navigation_feedback = ""
                self._advance_source_generation()
                if image or retire_backend:
                    if self._player is not None:
                        self._retire_player_backend()
                elif self._player is None or had_source:
                    self._replace_player_backend()
                else:
                    self._bind_player_signals()
            if self._player is not None:
                self._player.stop()
                self._player.setSource(QUrl())
            self._source = None
            self._position = 0
            self._request_position = None
            self._restore_session = None
            self._requested_offset_hint = None
            self._resume_after_seek = False
            self._duration = 0
            if source is not None:
                self._source = source
                self._backend_audio_only=getattr(self,'_audio_only',False)
                if self._backend_audio_only:self._player.setVideoSink(None)
                if isinstance(self._player, QMediaPlayer) and not self._priming and not getattr(self,'_audio_only',False):
                    self._priming = True
                    self._prime_requested = False
                    self._prime_timer.start()
                    self._set_state('Priming')
                self._player.setSource(QUrl.fromLocalFile(str(source)))
                self._player.setPosition(0)
            self._bind_frame_signal()
        finally:
            self._replacing_source = False
        if not image and self._retired_images:
            self._arm_image_retirement()
        self.changed.emit()

    def _advance_source_generation(self) -> None:
        self._source_generation += 1
        self._play_requested_generation = None
        self._played_generation = None

    def _configure_player(self, player: QMediaPlayer) -> None:
        player.setParent(self)
        self._staging_sink = QVideoSink(self)
        self._bind_frame_signal()
        self._backend_audio_only=getattr(self,'_audio_only',False)
        if not self._backend_audio_only:player.setVideoSink(self._staging_sink)
        player.setAudioOutput(self._audio)

    def _replace_player_backend(self) -> None:
        retired = self._player
        if retired is not None:
            self._retire_player_backend()
        replacement = self._player_factory()
        if replacement is retired:
            raise RuntimeError("Playback player factory reused a retired backend")
        self._player = replacement
        self._player_generation += 1
        self._configure_player(replacement)
        self._bind_player_signals()

    def _retire_player_backend(self) -> None:
        retired = self._player
        staging = self._staging_sink
        self._disconnect_frame_signal()
        self._disconnect_player_signals()
        # Qt's audio-only backend must be stopped before renderer detachment.
        # It has never owned a video publisher; callbacks are already revoked.
        if getattr(self,'_backend_audio_only',False):
            retired.stop();retired.setVideoSink(None)
        else:
            retired.setVideoSink(None);retired.stop()
        retired.setSource(QUrl())
        retired.setAudioOutput(None)
        retired.setParent(None)
        self._player_disposer(retired)
        _dispose_qt_object(staging)
        self._staging_sink = None
        self._player = None

    def _bind_player_signals(self) -> None:
        self._disconnect_player_signals()
        generation = self._source_generation
        backend = self._player
        handlers = {
            "playback_state": lambda state, current=generation, owner=backend: self._on_playback_state(
                current, owner, state
            ),
            "media_status": lambda status, current=generation, owner=backend: self._on_media_status(
                current, owner, status
            ),
            "position": lambda value, current=generation, owner=backend: self._on_position(
                current, owner, value
            ),
            "duration": lambda value, current=generation, owner=backend: self._on_duration(
                current, owner, value
            ),
            "error": lambda error, diagnostic, current=generation, owner=backend: self._on_error(
                current, owner, error, diagnostic
            ),
        }
        self._player.playbackStateChanged.connect(handlers["playback_state"])
        self._player.mediaStatusChanged.connect(handlers["media_status"])
        self._player.positionChanged.connect(handlers["position"])
        self._player.durationChanged.connect(handlers["duration"])
        self._player.errorOccurred.connect(handlers["error"])
        self._player_signal_handlers = handlers

    def _disconnect_player_signals(self) -> None:
        handlers = self._player_signal_handlers
        if not handlers:
            return
        connections = (
            (self._player.playbackStateChanged, handlers["playback_state"]),
            (self._player.mediaStatusChanged, handlers["media_status"]),
            (self._player.positionChanged, handlers["position"]),
            (self._player.durationChanged, handlers["duration"]),
            (self._player.errorOccurred, handlers["error"]),
        )
        for signal, handler in connections:
            try:
                signal.disconnect(handler)
            except (RuntimeError, TypeError):
                pass
        self._player_signal_handlers = {}

    def _accept_player_signal(self, generation: int, backend: QMediaPlayer) -> bool:
        return (
            not self._closed
            and not self._shutting_down
            and not self._replacing_source
            and generation == self._source_generation
            and backend is self._player
        )

    @Slot()
    def pauseForExternal(self) -> None:
        """Revoke pending play intent before another page uses audio/resources."""
        self._desired_playing=False
        self._play_requested_generation=None
        self._loop_active=False
        if self.sequence.item is not None:self.sequence.pause()
        elif self._player is not None:self._player.pause()

    def _configure_normal_loop(self) -> None:
        player = self._player
        if not isinstance(player, QMediaPlayer):
            return
        loops = (QMediaPlayer.Loops.Infinite if player.isSeekable() and not self._loop_active
                 else QMediaPlayer.Loops.Once)
        if player.loops() != loops:
            player.setLoops(loops)

    @Slot()
    def togglePlayback(self) -> None:
        if self.sequence.item is not None:
            self.sequence.toggle()
            return
        if not self.canPlay:
            return
        if self._exact_picture_seek is not None or self._exact_seek_failure:
            if not self._clock_seek:
                self._seek_waiting = False
                self._current_index = None
            self._resume_after_seek = False
            self._cancel_exact_seek()
        if self._state == "Playing":
            self._desired_playing = False
            self._loop_active = False
            self._player.pause()
            return
        if self._state == "Ended":
            self._player.setPosition(0)
            self._position = 0
        at_last_picture = self._timeline is not None and self._current_index == self._timeline.count - 1
        self._loop_active = (not at_last_picture and self._trim_enabled
                             and self._trim_in <= self._position < self._trim_out)
        self._configure_normal_loop()
        self._loop_seeking = False
        if (self._loop_active and self._presentation_sink is not None
                and (self._presented_start is None or not self._trim_contains_frame(self._presented_start)
                     or not self._presentation_sink.videoFrame().isValid())):
            # A paused seek's clock can also lead/lag its already visible frame.
            # Do not leave an excluded picture visible when arming the loop.
            self._presentation_sink.setVideoFrame(QVideoFrame())
        self._desired_playing = True
        if not self._seek_waiting:self._request_position=None
        self._play_requested_generation = self._source_generation
        self._player.play()

    @Slot(int)
    def seek(self, position_ms: int) -> None:
        self._seek(position_ms)

    def _seek(self, position_ms: int, *, restore_picture=False) -> int | None:
        if self.sequence.item is not None:
            self.sequence.seek(position_ms)
            return
        if not self.canPlay or self._duration <= 0:
            return
        target = max(0, min(self._duration, int(position_ms)))
        if (not restore_picture and not self._desired_playing and target == self.position
                and self.framesReliable and self._current_index == self.requestedFrameIndex):
            return  # This exact picture is already admitted; Qt may emit no new frame.
        self._restore_session=None
        self._resume_after_seek=False
        self._cancel_step()
        self._cancel_exact_seek()
        self._step_failed = False
        self._navigation_feedback = ""
        self._request_position = target
        self._position = target
        self._seek_waiting = True
        self._clock_seek = not self.timelineAvailable or not self._origin_established
        generation,backend=self._source_generation,self._player
        serial = self._step_serial
        decoder_target = self._decoder_seek_position(target)
        if restore_picture or not self._clock_seek:
            self._exact_picture_seek = _ExactPictureSeek(decoder_target, restore_picture)
            if self._timeline is not None:
                self._exact_seek_timer.start()
        exact_seek = self._exact_picture_seek
        self.changed.emit()
        if (not self._accept_player_signal(generation,backend) or serial != self._step_serial
                or self._request_position!=target):return
        if getattr(self,'_audio_only',False):
            backend.setPosition(target)
            self._seek_waiting=False
            if self._desired_playing:self._request_position=None
            return
        self._manual_navigation(target)
        if (self._accept_player_signal(generation,backend) and serial == self._step_serial
                and exact_seek is self._exact_picture_seek
                and self._request_position==target):backend.setPosition(decoder_target)
        return serial

    def _decoder_seek_position(self, logical_ms):
        # Qt/FFmpeg's AVI demuxer rounds an interior seek up to the next
        # packet. Use the indexed picture's start for that transport only.
        # Logical intent and strict decoded-PTS admission remain unchanged;
        # a backend miss can never authorize a different picture or Export.
        if (self._source is not None and self._source.suffix.lower()=='.avi'
                and self.timelineAvailable and self._origin_established):
            index=self._timeline.index_at_time(logical_ms*1000-self._timeline_offset)
            return max(0,(self._timeline.timestamps[index]+self._timeline_offset)//1000)
        return logical_ms

    @Slot(int)
    def seekRelative(self, delta_ms: int) -> None:
        if self.sequence.item is not None:
            self.sequence.seek(self.sequence.slot + int(delta_ms*self.sequence.item.sequence.rate/1000))
            return
        self.seek(self.position + int(delta_ms))

    @Slot(int)
    def stepFrame(self,direction:int) -> None:
        if self.sequence.item is not None:
            if direction in (-1,1): self.sequence.step(direction)
            return
        if direction not in (-1,1) or not self.canPlay: return
        if self._step_pending is not None:
            if self._queued_step is None: self._queued_step=direction
            return
        if not self.exactStepAvailable:
            self._navigation_feedback="Frame step unavailable"
            self.changed.emit()
            return
        if self.playing:
            self._desired_playing = False
            self._player.pause()
        target=max(0,min(self._timeline.count-1,self._current_index+direction))
        if target==self._current_index: return
        try:
            # On the accepted Qt path decoded timestamps and seek positions
            # share its source/container-relative domain. Preserve a nonzero
            # first-video timestamp (e.g. audio starts before video); subtracting
            # it again would seek into the preceding presentation picture.
            seek_ms=self._timeline.seek_ms(target,self._timeline_offset)
        except ValueError:
            self._step_did_not_settle()
            return
        self._step_pending=direction
        self._step_target_index=target
        self._step_expected_start=self._timeline.timestamps[target]+self._timeline_offset
        self._step_timer.start()
        self._request_position=seek_ms
        self._position=seek_ms
        self._seek_waiting=True
        self._clock_seek=False
        generation,backend,serial=self._source_generation,self._player,self._step_serial
        self.changed.emit()
        if not self._accept_player_signal(generation,backend) or serial!=self._step_serial:return
        self._manual_navigation(seek_ms)
        if self._accept_player_signal(generation,backend) and serial==self._step_serial:backend.setPosition(self._decoder_seek_position(seek_ms))

    @Slot(int)
    def setVolume(self, value: int) -> None:
        if self._closed:
            return
        percent = max(0, min(100, int(value)))
        if percent > 0:
            self._last_nonzero_volume = percent
        elif self.volume > 0:
            self._last_nonzero_volume = self.volume
        self._audio.setVolume(percent / 100.0)
        self._audio.setMuted(percent == 0)
        self.changed.emit()

    @Slot()
    def toggleMuted(self) -> None:
        if not self._closed:
            self.setVolume(0 if self.volume > 0 else self._last_nonzero_volume or 100)

    @Slot(QObject)
    def attachVideoSink(self, sink: QObject) -> None:
        if not self._closed and isinstance(sink, QVideoSink):
            self._video_output = None
            self._video_sink = sink
            self._bind_frame_signal()

    @Slot(QObject)
    def attachImageWindow(self,window):
        if not self._closed:
            # The persistent Viewer can be detached from its visual parent while
            # the empty/loading page is displayed. Its future window is explicit.
            self._image_window = window

    @Slot(QObject)
    def attachVideoOutput(self, output: QObject) -> None:
        if not self._closed:
            self._video_sink = None
            self._video_output = output
            self._bind_frame_signal()

    @Slot()
    def enterFullscreen(self) -> None:
        if self._closed or self._fullscreen or self._source is None:
            return
        self._fullscreen = True
        self._set_controls_visible(True)
        self._controls_zone = False
        self._controls_timer.setInterval(self._controls_idle_ms)
        self._restart_controls_timer()
        self.fullscreenChanged.emit()

    @Slot()
    def exitFullscreen(self) -> None:
        if not self._fullscreen:
            return
        self._fullscreen = False
        self._controls_timer.stop()
        self._controls_interactions = 0
        self._set_controls_visible(True)
        self.fullscreenChanged.emit()

    @Slot()
    def pointerActivity(self) -> None:
        if self._fullscreen and self._controls_zone:
            self._set_controls_visible(True)

    @Slot(bool)
    def setControlsZone(self, inside: bool) -> None:
        if not self._fullscreen:
            return
        if self._controls_zone == inside:
            return
        self._controls_zone = inside
        if inside:
            self._controls_timer.stop()
            self._set_controls_visible(True)
        elif self._controls_interactions == 0:
            self._controls_timer.setInterval(120)
            self._controls_timer.start()

    @Slot()
    def beginControlsInteraction(self) -> None:
        if not self._fullscreen:
            return
        self._controls_interactions += 1
        self._controls_timer.stop()
        self._set_controls_visible(True)

    @Slot()
    def endControlsInteraction(self) -> None:
        if not self._fullscreen:
            return
        self._controls_interactions = max(0, self._controls_interactions - 1)
        self._controls_timer.setInterval(120)
        self._restart_controls_timer()

    def _restart_controls_timer(self) -> None:
        if self._fullscreen and not self._controls_zone and self._controls_interactions == 0:
            self._controls_timer.start()

    def _hide_controls_after_idle(self) -> None:
        if self._fullscreen and not self._controls_zone and self._controls_interactions == 0:
            self._set_controls_visible(False)

    def _set_controls_visible(self, visible: bool) -> None:
        if self._controls_visible != visible:
            self._controls_visible = visible
            self.controlsVisibleChanged.emit()

    def _on_playback_state(self, generation: int, backend: QMediaPlayer, state) -> None:
        if not self._accept_player_signal(generation, backend):
            return
        if self._priming:
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            if (
                self._source is None
                or self._state == "Loading"
                or self._play_requested_generation != generation
            ):
                return
            self._played_generation = generation
            self._set_state("Playing")
        elif self._desired_playing:
            # Keep user intent across the explicit Trim/EOF fallback.
            return
        elif state == QMediaPlayer.PlaybackState.PausedState and self._source is not None:
            self._set_state("Paused")
        elif self._source is None:
            self._set_state("Empty")
        elif self._state not in ("Paused", "Ended", "Error", "Loading"):
            self._set_state("Ready")

    def _on_media_status(self, generation: int, backend: QMediaPlayer, status) -> None:
        if not self._accept_player_signal(generation, backend) or self._source is None:
            return
        if getattr(self,'_audio_only',False) and status == QMediaPlayer.MediaStatus.LoadedMedia:
            self._set_state('Ready');self._continue_restore()
        if self._priming and status == QMediaPlayer.MediaStatus.LoadedMedia and not self._prime_requested:
            self._prime_requested = True
            QTimer.singleShot(0, lambda: self._prime_source(generation, backend))
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._played_generation != generation:
                return
            if self._desired_playing:
                # Seekable whole-source playback normally wraps in Qt's demuxer.
                # An actual EOF still has the original bounded recovery path.
                target = self._trim_in if self._loop_active else 0
                if self._clock_seek:
                    self._clock_seek = self._seek_waiting = False
                    self._request_position = None
                backend.setPosition(target)
                backend.play()
            # A paused seek/step to the last picture must remain Paused.
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._set_error("This media could not be played.")

    def _prime_source(self, generation, backend):
        if self._accept_player_signal(generation, backend) and self._priming:
            backend.pause()
            backend.setPosition(0)

    def _on_position(self, generation: int, backend: QMediaPlayer, value: int) -> None:
        if (
            not self._accept_player_signal(generation, backend)
            or self._source is None
            or self._state == "Loading"
        ):
            return
        value = max(0, int(value))
        # A time-only seek issued before an exact frame map cannot wait for
        # an ordinal the decoder may already have passed. Resume live clock
        # feedback during playback; paused seeks and exact steps keep their
        # admitted-picture settlement and never infer a frame from this clock.
        if (self._clock_seek and self.playing and self._desired_playing
                and self._request_position is not None and value > self._request_position
                and (not self._loop_active or self._trim_in <= value < self._trim_out)):
            self._clock_seek = self._seek_waiting = False
            self._request_position = None
        if getattr(self,'_audio_only',False) and self._loop_seeking and self._trim_in<=value<self._trim_out:
            self._loop_seeking=False
        if getattr(self,'_audio_only',False) and self._request_position is not None and abs(value-self._request_position)<=1:
            self._seek_waiting=False
            if self._desired_playing:self._request_position=None
        if self._request_position is None and self._position != value:
            self._position = value
            self.changed.emit()
        if self._loop_active and self.playing and value >= self._trim_out and not self._loop_seeking:
            self._return_to_trim_in()

    def _return_to_trim_in(self):
        if not self._loop_seeking:
            self._loop_seeking = True
            self._player.setPosition(self._trim_in)

    def _on_duration(self, generation: int, backend: QMediaPlayer, value: int) -> None:
        if (
            not self._accept_player_signal(generation, backend)
            or self._source is None
            or self._state == "Loading"
        ):
            return
        value = max(0, int(value))
        if self._duration != value:
            self._duration = value
            self._continue_restore()
            self.changed.emit()

    def _on_error(
        self, generation: int, backend: QMediaPlayer, error, diagnostic: str
    ) -> None:
        if (
            not self._accept_player_signal(generation, backend)
            or self._source is None
            or self._state == "Loading"
            or error == QMediaPlayer.Error.NoError
        ):
            return
        self._logger.error("Qt Multimedia playback error %s: %s", error, diagnostic)
        self._set_error("This media could not be played.")

    def _set_error(self, message: str) -> None:
        self._desired_playing = False
        self._loop_active = False
        self._priming = False
        self._prime_timer.stop()
        self._cancel_step()
        self._cancel_exact_seek()
        self._step_failed = True
        self._navigation_feedback = "Frame step unavailable"
        self._error_message = message
        self._set_state("Error")

    def _set_state(self, state: str) -> None:
        if self._state != state:
            self._state = state
            self.changed.emit()

    def _notify_audio_change(self) -> None:
        if not self._closed and not self._shutting_down:
            self.changed.emit()

    def shutdown(self) -> None:
        self.sequence.clear()
        if self._shutdown_running:
            return
        if self._closed and not self._shutdown_pending:
            if self._shutdown_error is not None:
                raise self._shutdown_error
            return
        self._shutdown_running = True
        self._shutting_down = True
        if self._audio_follower is not None:
            self._audio_follower.shutdown()
        try:
            if self._shutdown_pending is None:
                # Reject queued callbacks immediately, without marking concrete
                # teardown complete before it has actually been attempted.
                self._advance_source_generation()
                self._timeline_generation += 1
                self._desired_playing = False
                self._priming = False
                self._loop_active = False
                sink = self._presentation_sink
                player = self._player
                staging = self._staging_sink
                self._shutdown_pending = [
                    ('image render acknowledgement', self._disconnect_image_retirement),
                    ("prime timer", self._prime_timer.stop),
                    ("controls timer", self._controls_timer.stop),
                    ("frame navigation", self._cancel_step),
                    ("exact picture seek", self._cancel_exact_seek),
                    ("frame callbacks", self._disconnect_frame_signal),
                    ("player callbacks", self._disconnect_player_signals),
                    # Let the native decoder drain while its sinks are still
                    # attached. Detaching first can stall Qt 6.11 after seeks.
                    ("player stop", player.stop) if player is not None else ("absent player stop", lambda: None),
                    ("media source", lambda: player.setSource(QUrl())) if player is not None else ("absent player source", lambda: None),
                    ("video sink", lambda: player.setVideoSink(None)) if player is not None else ("absent player sink", lambda: None),
                    ("retained video frame", lambda: sink.setVideoFrame(QVideoFrame()))
                        if sink is not None else ("absent viewer sink", lambda: None),
                    ("audio output", lambda: player.setAudioOutput(None)) if player is not None else ("absent player audio", lambda: None),
                    ("concrete player", lambda: self._player_disposer(player)) if player is not None else ("absent player", lambda: None),
                    ("staging sink", lambda: _dispose_qt_object(staging)),
                    ("audio volume callback", lambda: self._audio.volumeChanged.disconnect(
                        self._audio_signal_handlers["volume"])),
                    ("audio mute callback", lambda: self._audio.mutedChanged.disconnect(
                        self._audio_signal_handlers["muted"])),
                ]
                try:
                    if self._timeline_service is not None:
                        self._timeline_service.shutdown()
                except Exception as error:
                    # Preserve the service's original durable ownership failure;
                    # never clear its poison or skip the remaining Qt resources.
                    self._shutdown_error = error

            pending = []
            for name, cleanup in self._shutdown_pending:
                try:
                    cleanup()
                except Exception as error:
                    pending.append((name, cleanup))
                    if self._shutdown_error is None:
                        self._shutdown_error = error
                    else:
                        self._shutdown_secondary_errors.append((name, error))
                        self._shutdown_error.add_note(f"Playback teardown {name} also failed: {error!r}")
                    self._logger.exception("Playback teardown %s failed", name)
            # Only unsuccessful Qt actions are retained for a later shutdown.
            self._shutdown_pending = pending
            if not pending and self._shutdown_error is None and self.posters is not None:
                try:
                    self.posters.close()
                except Exception as error:
                    self._shutdown_pending = [("source-bound posters", self.posters.close)]
                    self._shutdown_error = error
            if not pending and self._shutdown_error is None:
                if self._image_prepared is not None:
                    self._image_prepared.release()
                    self._image_prepared = None
                for _, prepared in self._retired_images:
                    prepared.release()
                self._retired_images = []
            self._image_mode = False
            self._timeline = None
            self._timeline_signature = self._timeline_item_id = None
            self._indexing = False
            self._first_presented_start = self._first_presented_generation = None
            self._timeline_offset = self._origin_generation = None
            self._origin_established = False
            self._current_index = self._presented_start = None
            self._loop_seeking = False
            self._seek_waiting = False
            self._clock_seek = False
            self._trim_enabled = False
            self._trim_in = self._trim_out = 0
            self._trim_picture_bounds_us = (None, None)
            self._video_sink = self._video_output = None
            self._presentation_sink = self._staging_sink = None
            self._source = None
            self._position = self._duration = 0
            self._request_position = self._restore_session = None
            self._resume_after_seek = False
            self._navigation_feedback = self._error_message = ""
            self._state = "Empty"
            self._closed = True
            self.exitFullscreen()
            self.changed.emit()
        finally:
            self._shutdown_running = False
        if self._shutdown_error is not None:
            raise self._shutdown_error

def _format_time(milliseconds: int) -> str:
    value = max(0, int(milliseconds))
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def _dispose_qt_player(player: QMediaPlayer) -> None:
    _dispose_qt_object(player)


def _dispose_qt_object(obj: QObject) -> None:
    if obj is None:
        return
    obj.deleteLater()
    QCoreApplication.sendPostedEvents(obj, QEvent.Type.DeferredDelete)
