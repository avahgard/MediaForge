"""Slot transport inside the accepted PlaybackController, with no media player."""
import time
from fractions import Fraction
from PySide6.QtCore import QObject, QTimer, Qt


class SequenceTransport(QObject):
    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.item = None
        self.request_frame = None
        self.cancel_request = None
        self.playing = False
        self.awaiting = False
        self.loop = False
        self.slot = 0
        self.pending_slot = None
        self.deadline = Fraction(0)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.requested = self.delivered = self.late = 0

    @property
    def period(self): return Fraction(1_000_000_000,1) / self.item.sequence.rate

    def bind(self, item, callback, slot, cancel=None):
        self.clear()
        self.item, self.request_frame, self.slot = item, callback, slot
        self.cancel_request = cancel
        self.requested = self.delivered = self.late = 0
        item.sequence.current_slot = slot
        self.controller._state = 'Paused'
        self.controller.changed.emit()

    def clear(self):
        self.timer.stop()
        self.item = self.request_frame = None
        self.cancel_request = None
        self.playing = self.awaiting = self.loop = False
        self.pending_slot = None

    def pause(self):
        self.playing = False
        self.timer.stop()
        if self.awaiting and self.cancel_request is not None: self.cancel_request()
        self.awaiting = False
        self.pending_slot = None
        self.controller._state = 'Paused'
        self.controller.changed.emit()

    def toggle(self):
        if self.playing:
            self.pause()
        elif self.item and self.item.sequence.confirmed:
            s = self.item.sequence
            self.loop = self.item.trim.enabled and s.in_slot <= self.slot < s.out_slot
            self.playing = True
            self.deadline = Fraction(time.monotonic_ns()) + self.period
            self.controller._state = 'Playing'
            self.schedule()
            self.controller.changed.emit()

    def schedule(self):
        if not self.playing or self.awaiting: return
        delta = max(Fraction(0), self.deadline-time.monotonic_ns()) / 1_000_000
        self.timer.start(max(1, (delta.numerator+delta.denominator-1)//delta.denominator))

    def tick(self):
        if not self.playing or self.item is None: return
        if self.awaiting: return
        s = self.item.sequence
        end = s.out_slot if self.loop else self.item.metadata.source.manifest.count
        target = self.slot + 1
        if target >= end: target = s.in_slot if self.loop else 0
        self.request(target)

    def request(self, slot):
        if self.item is None: return
        self.awaiting = True
        self.pending_slot = slot
        self.requested += 1
        # Logical input acknowledges immediately. Displayed identity stays slot
        # until a generation-admitted publication; export uses current_slot.
        self.controller.changed.emit()
        if not self.request_frame(slot): self.failed()

    def seek(self, slot):
        if self.item is None: return
        slot = max(0,min(self.item.metadata.source.manifest.count-1,int(slot)))
        if self.loop and not self.item.sequence.in_slot <= slot < self.item.sequence.out_slot: self.loop = False
        self.timer.stop()
        self.deadline = Fraction(time.monotonic_ns()) + self.period
        self.request(slot)

    def step(self, direction):
        base = self.pending_slot if self.awaiting and not self.playing else self.slot
        self.pause()
        self.seek(base+direction)

    def admitted(self, slot):
        self.slot = slot
        self.item.sequence.current_slot = slot
        self.awaiting = False
        self.pending_slot = None
        self.delivered += 1
        now = time.monotonic_ns()
        if self.playing:
            if now > self.deadline: self.late += 1
            # No fabricated or skipped frames and no catch-up decode burst.
            # Deadlines are exact rationals; late decode slows first-pass playback.
            self.deadline = max(self.deadline + self.period, Fraction(now))
            self.controller._state = 'Playing'
            self.schedule()
        else: self.controller._state = 'Paused'
        self.controller.changed.emit()

    def failed(self):
        self.awaiting = False
        if self.item is not None: self.pause()
