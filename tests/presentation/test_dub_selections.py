"""Dub option edits, destination planning and result ownership."""
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


SETUP = r'''
from pathlib import Path
from types import SimpleNamespace as NS
from PySide6.QtCore import QCoreApplication, QObject, Signal, QUrl
from mediaforge.application.services.dub import DubService, DubState
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.presentation.bridge.dub import DubViewModel
app = QCoreApplication([])
published = Path.cwd() / 'validated-output.wav'
published.write_bytes(b'previous validated output sentinel')
gate = MediaProcessingGate()
sessions = NS(close=lambda control: None, expiry_due=lambda: False, retry_eligible=lambda name: True)
service = DubService(None, NS(sessions=sessions), None, gate, ('aiden', 'ryan'))
service._source = NS(video=None, audio_tracks=(NS(index=0), NS(index=1)))
service._state = DubState(generation=7, state='completed', destination=str(published))
class Player(QObject):
    changed = Signal()
    def stop(self): pass
    def shutdown(self): pass
class Preview:
    busy = False
    def snapshot(self): return NS(state='empty', voice='', message='')
    def shutdown(self): pass
model = DubViewModel(service, Preview(), NS(allInstalled=True), player=Player())
model._timer.stop()
model._folder = str(Path.cwd())
model._mode = 'audio'
model._track = 0
model._source_generation = 7
model.refresh()
assert model.state == 'completed'
'''


@pytest.mark.parametrize('delta', [249847, None, True, -1, 3900*48000+1])
def test_elastic_warning_presents_only_current_measured_schedule_delta(run_python,delta):
    result=run_python(SETUP+f'\ndelta={delta!r}\n'+r'''
from dataclasses import replace
service.last_receipt={'speech':{'tutorial_v2':{'maximum_absolute_delta':delta}}}
service._state=replace(service._state,warnings=('phrase_timing_shifted',))
model.refresh()
assert 'redistributed' in model.warningsText
if type(delta) is int and 0<delta<=3900*48000:
    assert '5.205 s' in model.warningsText and 'source phrase estimates' in model.warningsText
else:
    assert 'Maximum measured' not in model.warningsText
model.setVoice('ryan');model.refresh()
assert model.warningsText==''
model.shutdown()
''')
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('value,shown',[('15239/10000',True),('3',True),('5',False),('NaN',False),(None,False),(True,False)])
def test_adaptive_warning_shows_only_bounded_measured_decimal_tempo(run_python,value,shown):
    result=run_python(SETUP+f'\nvalue={value!r}\nshown={shown!r}\n'+r'''
from dataclasses import replace
service.last_receipt={'speech':{'tutorial_v2':{'maximum_tempo':value}}}
service._state=replace(service._state,warnings=('speech_accelerated','tempo_above_1_6','tempo_above_2_0'))
model.refresh()
assert ('Maximum applied block tempo' in model.warningsText)==shown
assert '1.25' not in model.warningsText
assert 'audible review' in model.warningsText and 'not independently listening-qualified' in model.warningsText
if value=='15239/10000':assert '1.5239×' in model.warningsText
model.shutdown()
''')
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('edit', [
    "model.setVoice('ryan')",
    "model.setFormat('wav')",
    "model.setMode('video_audio_only')",
    "folder = Path.cwd() / 'another'; folder.mkdir(); assert model.chooseFolder(QUrl.fromLocalFile(str(folder)))",
    "model.selectTrack(1)",
])
def test_edits_reset_completed_action_without_touching_published_output(run_python, edit):
    result = run_python(SETUP + '\n' + edit + r'''
model.refresh()
assert model.state == 'ready', (model.state, service.snapshot())
assert model.operation['destination'] == ''
assert not model.openFolder()
assert published.read_bytes() == b'previous validated output sentinel'
assert not gate.busy and not gate.failure
model.shutdown()
''')
    assert result.returncode == 0, result.stdout + result.stderr


def test_output_mode_choice_survives_refresh_and_new_settings_are_presented(run_python):
    result = run_python(SETUP + r'''
model.setMode('video_audio_only')
model.refresh(); model.refresh()
assert model.state == 'ready'
assert model.outputMode == 'video_audio_only'
model.setVoice('ryan'); model.refresh()
assert model.voice == 'ryan' and model.outputMode == 'video_audio_only'
model.shutdown()
''')
    assert result.returncode == 0, result.stdout + result.stderr


def test_filename_admission_uses_the_qualified_route_and_protected_destination(run_python):
    fixtures = Path(__file__).resolve().parents[1] / 'unit'
    result = run_python(SETUP + f'\nimport sys; sys.path.insert(0, {str(fixtures)!r})\n' + r'''
from dataclasses import replace
from fractions import Fraction
from mediaforge.application.contracts.media import ActiveSourceRef
from mediaforge.infrastructure.filesystem.dub_source import destination_plan
from test_dub_contracts import source

descriptor = source()
physical = Path.cwd() / 'original.mp4'
physical.write_bytes(b'source identity sentinel')
info = physical.stat()
identity = replace(descriptor.identity, path=physical, device=info.st_dev, file_id=info.st_ino)
track = descriptor.audio_tracks[0]
descriptor = replace(descriptor, identity=identity,
                     audio_tracks=(replace(track, clock=replace(track.clock, end=Fraction(1))),))
service.destination_planner = destination_plan
dispatches = []
service._start = lambda stage, invoke: dispatches.append(stage) or True

def select(descriptor):
    ref = ActiveSourceRef('fixture', str(descriptor.identity.path), 'video', (0, 0, 0, 0))
    assert model.select_original(ref)
    service._source = descriptor
    service._state = DubState(service.snapshot().generation + 1, 'ready')
    model.refresh()

def names(expected):
    plan = service.plan(model.outputMode, model.outputFormat)
    assert model.filename == expected.removesuffix(plan.route.suffix), model.filename
    assert model.canStart, model.blocker
    assert service.destination(model.voice, model.folder, plan, model._filename_input(plan)).name == expected
    assert model.start() and dispatches[-1] == 'extract'

select(descriptor)
assert model.filename == 'original' and model.folder == str(physical.parent)
assert 'distinct' in model.blocker
assert model.setFilename('my_video')
names('my_video.mp4')
model.setMode('compatible_mp4')
names('my_video.mp4')
model.setMode('video_audio_only')
names('my_video.mp3')
model.setFormat('wav')
names('my_video.wav')
model.setFormat('mp3')
names('my_video.mp3')

other = Path.cwd()/'other'
other.mkdir()
manual = Path.cwd()/'manual'
manual.mkdir()
assert model.chooseFolder(QUrl.fromLocalFile(str(manual)))
mov = replace(descriptor, container='mov', extension='.mov', identity=replace(identity,path=other/'other.mov'),
              video=replace(descriptor.video, codec='prores', profile='Standard', pixel_format='yuv422p10le'))
select(mov)
assert model.folder == str(other)
other_manual = other/'manual'
other_manual.mkdir()
assert model.chooseFolder(QUrl.fromLocalFile(str(other_manual)))
assert model.setFilename('  неверно  ') and not model.canStart
assert model.setFilename('Мой ролик.черновик.2')
names('Мой ролик.черновик.2.mov')
assert model.setFilename('my_video.MOV')
names('my_video.mov')
model.setMode('compatible_mp4')
names('my_video.mp4')
assert model.setFilename('Мой ролик.версия.2')
for _ in range(2):
    select(descriptor)
    assert model.folder == str(manual)
    names('my_video.mp4')
    select(mov)
    assert model.folder == str(other_manual)
    names('Мой ролик.версия.2.mov')
select(descriptor)
assert model.folder == str(manual) and model.folderUrl.toLocalFile() == str(manual).replace('\\','/')
assert model.setFilename('my_video.mp4.mp4')
names('my_video.mp4')
select(replace(descriptor, extension='.MP4'))
assert model.setFilename('my_video.mp4')
names('my_video.MP4')
select(descriptor)
assert model.chooseFolder(QUrl.fromLocalFile(str(Path.cwd())))
before = len(dispatches)
for invalid in ('my_video.mov', 'audio.flac', 'bad:name', 'dir/name', 'CON.txt', 'name.',
                '.mp4', 'trail ', 'name\x01', 'я' * 256):
    assert model.setFilename(invalid)
    assert not model.canStart and not model.start(), invalid
assert len(dispatches) == before

assert model.setFilename('original')
assert 'distinct' in model.blocker and not model.canStart
occupied = Path.cwd() / 'occupied.mp4'
occupied.write_bytes(b'occupied sentinel')
assert model.setFilename('occupied')
assert 'never overwritten' in model.blocker and not model.start()
assert occupied.read_bytes() == b'occupied sentinel' and physical.read_bytes() == b'source identity sentinel'
assert len(dispatches) == before
assert model.setFilename('')
assert model.filename == 'original' and 'distinct' in model.blocker and not model.start()
select(mov)
select(descriptor)
assert model.filename == 'original'
model.shutdown()
''')
    assert result.returncode == 0, result.stdout + result.stderr


def test_reselecting_same_values_keeps_completed_action(run_python):
    result = run_python(SETUP + r'''
model.setVoice('aiden'); model.setFormat('mp3'); model.setMode('audio')
model.selectTrack(0)
assert model.chooseFolder(QUrl.fromLocalFile(str(Path.cwd())))
model.refresh()
assert model.state == 'completed' and model.operation['destination'] == str(published)
model.shutdown()
''')
    assert result.returncode == 0, result.stdout + result.stderr


def test_edited_failed_options_invalidate_retry_without_writing_checkpoints(run_python):
    result = run_python(SETUP + r'''
service._state = DubState(generation=8, state='failed')
service._retry = 'same-process-ticket'
service._retry_key = object()
model.refresh(); assert service.can_retry()
model.setVoice('ryan'); model.refresh()
assert not service.can_retry() and model.state == 'ready'
assert published.read_bytes() == b'previous validated output sentinel'
model.shutdown()
''')
    assert result.returncode == 0, result.stdout + result.stderr


def test_existing_qt_tick_retires_owned_idle_bytes_without_a_new_command(run_python):
    result=run_python(SETUP+r'''
import time
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
from mediaforge.features.dub.contracts import DubControl
paths=ProfilePaths.from_environment();paths.prepare();clock=[100000.,1000.]
store=SpeechSessions(paths,wall_clock=lambda:clock[0],monotonic_clock=lambda:clock[1])
with store.open(NS(retry_session=None,retain_private_recovery=True),{},DubControl()) as session:
    name=session.path.name;session.put_bytes('private.pcm',b'\x01\x00'*20)
service.speech.sessions=store;service._retry=name;service._retry_key=object()
service._state=DubState(generation=7,state='failed',message='Original failure.')
assert service.can_retry();clock[0]+=86401;assert not service.can_retry()
model._timer.setInterval(10);model._timer.start()
deadline=time.monotonic()+5
while (name in store._tickets or service.busy) and time.monotonic()<deadline:
    app.processEvents();time.sleep(.005)
assert name not in store._tickets and not (paths.sessions/name).exists()
assert not service.can_retry() and not gate.busy and not gate.failure
assert service.snapshot().generation==7 and service.snapshot().state=='failed'
assert published.read_bytes()==b'previous validated output sentinel'
model.shutdown()
''')
    assert result.returncode==0,result.stdout+result.stderr

