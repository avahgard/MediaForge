import logging
import pytest
from PySide6.QtCore import QCoreApplication, QObject, Signal
from mediaforge.presentation.bridge.playback import PlaybackController
from mediaforge.presentation.bridge.default_audio import DefaultAudioDeviceFollower
from test_playback_controller import FakePlayer, FakeAudio


class Device:
    def __init__(self, identity): self.identity=identity
    def id(self): return self.identity
    def isNull(self): return not self.identity


class Devices(QObject):
    audioOutputsChanged=Signal()
    def __init__(self): super().__init__();self.current=Device(b'A');self.queries=0
    def defaultAudioOutput(self): self.queries+=1;return self.current
    def change(self,identity): self.current=Device(identity);self.audioOutputsChanged.emit()


class Audio(FakeAudio):
    def __init__(self): super().__init__();self.applied=[];self.fail=False
    def setDevice(self,device):
        if self.fail: raise RuntimeError('test device failure')
        self.applied.append(device.id())


@pytest.fixture
def app(): return QCoreApplication.instance() or QCoreApplication([])


@pytest.mark.parametrize('playing',[False,True])
def test_default_changes_preserve_persistent_output_and_all_transport_intent(app,tmp_path,playing):
    audio=Audio();devices=Devices();players=[]
    def create():
        p=FakePlayer();players.append(p);return p
    view=PlaybackController(create(),audio,audio_devices=devices,player_factory=create,player_disposer=lambda p:setattr(p,'retired',True))
    assert audio.applied==[b'A']
    view.set_source(str(tmp_path/'first.mp4'));view._player.durationChanged.emit(10000)
    view.setVolume(37);view.toggleMuted();view.seek(2500);view.configure_trim_loop(True,1000,8000)
    view.enterFullscreen()
    if playing: view.togglePlayback()
    state=(view._player,view._audio,view._source,view.position,view._desired_playing,view.volume,view.muted,view.loopMode,view.fullscreen)
    events=list(view._player.events)
    devices.change(b'A');assert audio.applied==[b'A']
    devices.change(b'B');assert audio.applied==[b'A',b'B']
    assert state==(view._player,view._audio,view._source,view.position,view._desired_playing,view.volume,view.muted,view.loopMode,view.fullscreen)
    assert view._player.events==events and len(players)==1
    devices.change(b'');assert view._audio_follower.pending and audio.applied==[b'A',b'B']
    devices.change(b'B');assert not view._audio_follower.pending and audio.applied==[b'A',b'B',b'B']
    view.begin_loading();view.set_source(str(tmp_path/'second.mov'));second=view._player
    devices.change(b'A');assert view._player is second and len(players)==2 and players[0].retired
    assert second.audio is audio and view._audio is audio
    follower=view._audio_follower;view.shutdown();calls=list(audio.applied);queries=devices.queries
    devices.change(b'B');follower.refresh()
    assert audio.applied==calls and devices.queries==queries


def test_device_failure_is_bounded_and_later_notification_retries(app,caplog):
    audio=Audio();devices=Devices();follower=DefaultAudioDeviceFollower(audio,logging.getLogger('test-audio'),devices=devices)
    audio.fail=True
    devices.change(b'B')
    assert follower.pending and audio.applied==[b'A']
    assert len(caplog.records)==1
    audio.fail=False;devices.change(b'B')
    assert not follower.pending and audio.applied==[b'A',b'B']
    follower.shutdown()


def test_exceptional_playback_shutdown_disables_follower_before_service_failure(app):
    class Service:
        def shutdown(self): raise RuntimeError('owned timeline failure')
    audio=Audio();devices=Devices();view=PlaybackController(FakePlayer(),audio,audio_devices=devices,timeline_service=Service())
    with pytest.raises(RuntimeError,match='owned timeline failure'): view.shutdown()
    before=list(audio.applied);devices.change(b'B');view._audio_follower.refresh()
    assert audio.applied==before and view._shutdown_error.args==('owned timeline failure',)
