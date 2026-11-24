from array import array
from pathlib import Path
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QImage,QColor
from PySide6.QtMultimedia import QVideoFrame,QVideoSink
from mediaforge.application.contracts.source_timeline import SourcePresentationTimeline
from mediaforge.presentation.bridge.video_posters import VideoPosters
from mediaforge.infrastructure.filesystem.image_output import ReadLocks


def owner(path):
    locks=ReadLocks()
    try:locks.add(path);return locks
    except BaseException:locks.close();raise


def picture(color):
    image=QImage(96,64,QImage.Format_RGB32);image.fill(QColor(color))
    frame=QVideoFrame(image);frame.setStartTime(40000)
    return frame


@pytest.mark.native
def test_posters_are_source_locked_bounded_and_exact_ordinal(tmp_path):
    app=QCoreApplication.instance() or QCoreApplication([])
    cache=VideoPosters(owner)
    clock=SourcePresentationTimeline.from_timestamps(array('q',[1000000,1040000,1100000]),40000)
    paths=[]
    try:
        for i in range(3):
            path=tmp_path/f'{i}.mov';path.write_bytes(bytes([i])*32);paths.append(path)
            cache.activate(path)
            frame=picture(['red','green','blue'][i])
            cache.remember(cache.active,frame,1,clock,-1000000)
            assert cache.picture(path,50)==frame  # VFR interval, not rounded FPS.
            assert cache.picture(path,0) is None and cache.picture(path,100) is None
            with pytest.raises(OSError):path.open('wb')
            assert len(cache.entries)<=2 and cache.reserved<=cache.byte_limit
        assert cache.picture(paths[0],50) is None
        paths[0].write_bytes(b'changed after eviction')
        assert cache.picture(paths[0],50) is None
        cache.activate(paths[2]);assert len(cache.entries)==2  # Duplicate logical rows share ownership.
    finally:cache.close()
    assert cache.reserved==0 and not cache.entries
    for p in paths:p.write_bytes(b'released')


def test_poster_is_not_current_decoder_admission(tmp_path):
    from test_playback_controller import FakePlayer,FakeAudio
    from mediaforge.presentation.bridge.playback import PlaybackController
    app=QCoreApplication.instance() or QCoreApplication([])
    view=PlaybackController(FakePlayer(),FakeAudio(),player_disposer=lambda _:None)
    sink=QVideoSink();view.attachVideoSink(sink)
    frame=picture('red');old=tmp_path/'old.mov';new=tmp_path/'new.mov'
    try:
        view.set_source(str(old));retired=view._admission_receiver
        view.set_source(str(new),poster=frame)
        assert sink.videoFrame()==frame
        assert not view.framesReliable and view._presented_start is None and view._current_index is None
        retired.receive(picture('blue'))
        assert sink.videoFrame()==frame and not view.framesReliable
    finally:view.shutdown()


def test_source_is_locked_before_player_reads_url(tmp_path):
    from test_playback_controller import FakePlayer,FakeAudio
    from mediaforge.presentation.bridge.playback import PlaybackController
    app=QCoreApplication.instance() or QCoreApplication([])
    path=tmp_path/'source.mov';path.write_bytes(b'original')
    cache=VideoPosters(owner)
    class Player(FakePlayer):
        def setSource(self,url):
            if url.isLocalFile():
                with pytest.raises(OSError):path.open('wb')
            super().setSource(url)
    view=PlaybackController(Player(),FakeAudio(),posters=cache,player_factory=Player,player_disposer=lambda _:None)
    try:view.set_source(str(path))
    finally:view.shutdown()
    assert not cache.entries
    assert path.read_bytes()==b'original'


def test_restored_logical_hint_never_grants_decoder_reliability(tmp_path):
    from test_playback_controller import FakePlayer,FakeAudio
    from mediaforge.presentation.bridge.playback import PlaybackController
    app=QCoreApplication.instance() or QCoreApplication([])
    view=PlaybackController(FakePlayer(),FakeAudio(),player_disposer=lambda _:None)
    try:
        view.set_source(str(tmp_path/'video.mov'))
        view._timeline=SourcePresentationTimeline.from_timestamps(array('q',[1000000,1040000,1100000]),40000)
        view.restore_session(50,request_offset=-1000000)
        assert view.frameText=='Frame 2 / 3' and not view.framesReliable
        assert not view._origin_established and view._current_index is None
        view.clear_source()
        assert view.requestedFrameIndex==-1 and view._requested_offset_hint is None
    finally:view.shutdown()


def test_uncertain_poster_cleanup_keeps_owner_reachable(tmp_path):
    class Guard:
        broken=True
        def close(self):
            if self.broken:raise RuntimeError('owned lock not released')
    path=tmp_path/'a.mov';path.write_bytes(b'a');guard=Guard();cache=VideoPosters(lambda _:guard)
    cache.activate(path)
    with pytest.raises(RuntimeError,match='owned lock'):cache.close()
    assert len(cache.entries)==1
    guard.broken=False;cache.close();assert not cache.entries


def test_refused_candidate_preserves_current_poster_ownership(tmp_path):
    path=tmp_path/'valid.mov';path.write_bytes(b'valid')
    blocked=tmp_path/'busy.mov';blocked.write_bytes(b'busy')
    def open_source(candidate):
        if str(candidate)==str(blocked):raise OSError('foreign writer prevents source lock')
        return owner(candidate)
    cache=VideoPosters(open_source)
    try:
        cache.activate(path);active=cache.active
        with pytest.raises(OSError,match='foreign writer'):cache.activate(blocked)
        assert cache.active==active and len(cache.entries)==1
        timeline=SourcePresentationTimeline.from_timestamps(array('q',[0,40000]),40000)
        frame=picture('green');cache.remember(active,frame,1,timeline)
        assert cache.picture(path,40)==frame
        with pytest.raises(OSError):path.open('wb')
    finally:cache.close()


def test_hardware_nv12_poster_copy_preserves_planes_and_requires_admission(tmp_path):
    from PySide6.QtMultimedia import QVideoFrameFormat
    from PySide6.QtCore import QSize
    class HardwareFrame(QVideoFrame):
        # Controlled branch coverage; actual hardware is separately tested in GUI.
        def handleType(self):return QVideoFrame.RhiTextureHandle
    path=tmp_path/'hardware.mov';path.write_bytes(b'locked source');charges=[]
    cache=VideoPosters(owner,charges.append);cache.activate(path)
    frame=HardwareFrame(QVideoFrameFormat(QSize(96,64),QVideoFrameFormat.Format_NV12));frame.setStartTime(40000)
    assert frame.map(QVideoFrame.WriteOnly)
    for i in range(frame.planeCount()):frame.bits(i)[:]=bytes([71+i*50])*frame.mappedBytes(i)
    frame.unmap();clock=SourcePresentationTimeline.from_timestamps(array('q',[0,40000]),40000)
    try:
        cache.remember(cache.active,frame,1,clock)
        assert cache.picture(path,40) is None and not charges
        cache.remember(cache.active,frame,1,clock,capture_gpu=True)
        poster=cache.picture(path,40);assert charges==[16*96*64+2*1024**2]
        assert poster.isValid() and poster.handleType()==QVideoFrame.NoHandle and poster.pixelFormat()==frame.pixelFormat()
        assert poster.startTime()==40000 and cache.reserved<=cache.byte_limit
        assert poster.map(QVideoFrame.ReadOnly)
        for i in range(poster.planeCount()):assert set(bytes(poster.bits(i)))=={71+i*50}
        poster.unmap()
        assert frame.map(QVideoFrame.WriteOnly);frame.bits(0)[:]=bytes([11])*frame.mappedBytes(0);frame.unmap()
        assert poster.map(QVideoFrame.ReadOnly);assert set(bytes(poster.bits(0)))=={71};poster.unmap()
    finally:cache.close()


@pytest.mark.parametrize('refused', [False, True])
def test_source_switch_releases_shared_nohandle_decoder_backing(tmp_path, refused):
    from PySide6.QtCore import QSize
    from PySide6.QtMultimedia import QVideoFrameFormat

    paths = [tmp_path/'one.mov', tmp_path/'two.mov']
    for path in paths:
        path.write_bytes(b'protected')
    checks = []
    def admit(amount):
        checks.append(amount)
        if refused:
            raise ValueError('Renderer is over its admitted limit')
    cache = VideoPosters(owner, admit)
    frame = QVideoFrame(QVideoFrameFormat(QSize(96,64), QVideoFrameFormat.Format_NV12))
    frame.setStartTime(40000)
    assert frame.handleType() == QVideoFrame.NoHandle and frame.map(QVideoFrame.WriteOnly)
    for i in range(frame.planeCount()):
        frame.bits(i)[:] = bytes([50+i])*frame.mappedBytes(i)
    frame.unmap()
    clock = SourcePresentationTimeline.from_timestamps(array('q', [0,40000]), 40000)
    try:
        cache.activate(paths[0])
        cache.remember(cache.active, frame, 1, clock)
        assert cache.picture(paths[0],40) == frame and not checks
        cache.activate(paths[1])
        assert checks == [16*96*64+2*1024**2]
        poster = cache.picture(paths[0],40)
        if refused:
            assert poster is None and cache.reserved == 0
        else:
            assert poster.isValid() and poster.pixelFormat() == frame.pixelFormat()
            assert frame.map(QVideoFrame.WriteOnly)
            frame.bits(0)[:] = bytes([17])*frame.mappedBytes(0)
            frame.unmap()
            assert poster.map(QVideoFrame.ReadOnly)
            assert set(bytes(poster.bits(0))) == {50}
            poster.unmap()
        with pytest.raises(OSError):
            paths[0].open('wb')
    finally:
        cache.close()


def test_video_availability_distinguishes_poster_and_decoder_truth(tmp_path):
    from test_playback_controller import FakePlayer,FakeAudio
    from mediaforge.presentation.bridge.playback import PlaybackController
    app=QCoreApplication.instance() or QCoreApplication([])
    view=PlaybackController(FakePlayer(),FakeAudio(),player_disposer=lambda _:None);sink=QVideoSink();view.attachVideoSink(sink)
    try:
        assert not view.videoPictureAvailable
        view.set_source(str(tmp_path/'first.mov'));assert not view.videoPictureAvailable
        view.set_source(str(tmp_path/'second.mov'),poster=picture('red'))
        assert view.videoPictureAvailable and not view.framesReliable
        with pytest.raises(ValueError):view.export_picture_snapshot()
        view.clear_source();assert not view.videoPictureAvailable
    finally:view.shutdown()


def test_canceled_gpu_capture_does_not_recreate_a_retired_entry(tmp_path):
    from PySide6.QtMultimedia import QVideoFrameFormat
    from PySide6.QtCore import QSize
    class HardwareFrame(QVideoFrame):
        def handleType(self):return QVideoFrame.RhiTextureHandle
    path=tmp_path/'retired.mov';path.write_bytes(b'original')
    cache=VideoPosters(owner);cache.activate(path);signature=cache.active
    frame=HardwareFrame(QVideoFrameFormat(QSize(96,64),QVideoFrameFormat.Format_NV12))
    clock=SourcePresentationTimeline.from_timestamps(array('q',[0]),40000)
    cache.admit_readback=lambda _:cache.close()
    cache.remember(signature,frame,0,clock,capture_gpu=True)
    assert cache.active is None and not cache.entries and cache.reserved==0
    path.write_bytes(b'lock safely released')
