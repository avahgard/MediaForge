"""Readiness-barrier regressions; native decoder/image assertions stay separate."""

from pathlib import Path

import pytest


HELPER_DIR = str(Path(__file__).resolve().parents[1])
SETUP = r'''
from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtMultimedia import QVideoFrame, QVideoSink
from native_workspace_regressions import DecodedFrameSettlement
app = QGuiApplication([])
class Presentation(QObject):
    changed = Signal()
    _presented_start = None
    frameText = 'pending'
view = Presentation()
sink = QVideoSink()
barrier = DecodedFrameSettlement(sink, view, lambda frame: 'Frame 2 / 75')
image = QImage(16, 16, QImage.Format_RGB32)
image.fill(0xff112233)
frame = QVideoFrame(image)
frame.setStartTime(40000)
frame.setEndTime(80000)
def observe(start, label='Frame 2 / 75'):
    view._presented_start = start
    view.frameText = label
    view.changed.emit()
'''


def run_barrier(run_python, code):
    result = run_python("import sys\nsys.path.insert(0, " + repr(HELPER_DIR) + ")\n" + SETUP + code)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_barrier_waits_for_late_decoded_delivery_and_late_presentation(run_python):
    run_barrier(run_python, r'''
revision = barrier.revision
QTimer.singleShot(220, lambda: sink.setVideoFrame(frame))
QTimer.singleShot(440, lambda: observe(40000))
actual = barrier.wait_for('delayed delivery/observation', after_revision=revision, timeout_ms=1000)
assert actual == sink.videoFrame() and actual.startTime() == 40000
assert actual.toImage().pixelColor(0, 0).name() == '#112233'
assert view.frameText == 'Frame 2 / 75'
barrier.close()
''')


@pytest.mark.integration
def test_barrier_times_out_without_a_decoded_frame_despite_clock_progress(run_python):
    run_barrier(run_python, r'''
view.position = 99999
QTimer.singleShot(0, lambda: observe(40000))
try:
    barrier.wait_for('missing decode', timeout_ms=100)
except AssertionError as error:
    assert 'Timeout: decoded/presentation settlement' in str(error) and 'sink_valid=False' in str(error)
else:
    raise AssertionError('Position/presentation alone fabricated readiness')
barrier.close()
''')


@pytest.mark.integration
def test_barrier_times_out_if_the_presentation_observation_never_catches_up(run_python):
    run_barrier(run_python, r'''
QTimer.singleShot(0, lambda: sink.setVideoFrame(frame))
QTimer.singleShot(0, lambda: observe(0))
try:
    barrier.wait_for('missing observation', timeout_ms=100)
except AssertionError as error:
    assert 'sink_start=40000' in str(error) and 'observed_start=0' in str(error)
else:
    raise AssertionError('Sink delivery alone fabricated readiness')
barrier.close()
''')


@pytest.mark.integration
def test_barrier_rejects_wrong_label_before_success_without_waiting_for_healing(run_python):
    run_barrier(run_python, r'''
QTimer.singleShot(0, lambda: sink.setVideoFrame(frame))
QTimer.singleShot(0, lambda: observe(40000, 'Frame 777 / 75'))
healed = []
def otherwise_heal_and_accept(frame):
    healed.append(True)
    observe(40000)
    return True
try:
    # Validation must fail before even evaluating a predicate that would heal
    # and accept the bad state; no scheduling delay establishes this ordering.
    barrier.wait_for('coherent frame with incorrect public label', otherwise_heal_and_accept, timeout_ms=1000)
except AssertionError as error:
    assert 'Incorrect public frame label' in str(error)
    assert "expected='Frame 2 / 75'" in str(error) and "actual='Frame 777 / 75'" in str(error)
    assert 'sink_start=40000' in str(error) and 'observed_start=40000' in str(error)
else:
    raise AssertionError('Incorrect label satisfied public readiness contract')
assert sink.videoFrame().startTime() == view._presented_start == 40000
assert view.frameText == 'Frame 777 / 75' and not healed
# The separate native snapshot retains its original strict label assertion.
barrier.close()
''')
