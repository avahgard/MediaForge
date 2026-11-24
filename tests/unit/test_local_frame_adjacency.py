from fractions import Fraction
from threading import Event
from pathlib import Path
import subprocess
import pytest
from mediaforge.application.contracts.frame_step import AdjacentFrameRequest, LocalProbeControl
from mediaforge.application.services.frame_step import AdjacentFrameService
from mediaforge.infrastructure.ffmpeg.local_adjacency import FfprobeLocalAdjacency, LOCAL_PACKET_LIMIT


def request(index=5000, direction=1, rate=Fraction(60), count=10800):
    return AdjacentFrameRequest('D:/video with spaces/Кириллица.mp4', str(rate), str(1/(rate*512)), count,
                                round(index*1_000_000/rate), direction)


@pytest.mark.parametrize('rate', [Fraction(25), Fraction(30), Fraction(60), Fraction(24000,1001)])
@pytest.mark.parametrize('direction', [-1, 1])
def test_local_adjacency_is_independent_of_source_length(rate, direction):
    req = request(direction=direction, rate=rate)
    packets = [{'pts': n*512, 'duration': 512} for n in (5002, 4998, 5001, 4999, 5000)]
    result = FfprobeLocalAdjacency.prove(req, packets)
    assert result.direction == direction and result.observations == 5
    assert abs(result.start_us - round((5000+direction)*1_000_000/rate)) <= 1
    assert result.start_us < result.seek_ms*1000 < result.end_us


@pytest.mark.parametrize('problem', ['missing-adjacent', 'gap', 'duration', 'wrong-anchor', 'over-bound', 'duplicate'])
def test_insufficient_or_contradictory_local_proof_fails_closed(problem):
    req = request()
    packets = [{'pts': n*512, 'duration': 512} for n in range(4998, 5003)]
    if problem == 'missing-adjacent': packets = packets[:3]
    if problem == 'gap': packets[3]['pts'] += 256
    if problem == 'duration': packets[3]['duration'] = 1024
    if problem == 'wrong-anchor': req = request(index=5100)
    if problem == 'over-bound': packets *= LOCAL_PACKET_LIMIT
    if problem == 'duplicate': packets.append(packets[3])
    assert FfprobeLocalAdjacency.prove(req, packets) is None


def test_cancel_before_spawn_has_zero_factory_calls():
    control = LocalProbeControl()
    control.cancel()
    calls = []
    with pytest.raises(RuntimeError, match='before spawn'):
        control.spawn(lambda *a, **kw: calls.append(a), ['probe'])
    assert calls == []


def test_shifted_local_neighborhood_does_not_claim_an_ordinal():
    req=AdjacentFrameRequest('shifted.mp4','25','1/12800',300,6440000,0,12000000)
    packets=[{'pts':n,'duration':512} for n in (81408,81920,82432,82944,83456)]
    proof=FfprobeLocalAdjacency.prove(req,packets)
    assert proof is not None and proof.start_us==6440000
    assert proof.previous_us==6400000 and proof.next_us==6480000
    assert not hasattr(proof,'index')


def test_probe_timeout_reaps_owned_process_and_uses_fixed_local_first_video_window(monkeypatch):
    clock = iter(n/10 for n in range(100))
    monkeypatch.setattr('mediaforge.infrastructure.ffmpeg.local_adjacency.time.monotonic',lambda:next(clock))
    class Process:
        returncode = None
        def communicate(self, timeout):
            assert 0 < timeout <= .1
            raise subprocess.TimeoutExpired('probe', timeout)
        def poll(self): return self.returncode
        def terminate(self): self.terminated = True
        def wait(self, timeout): self.returncode = -1
    process, commands = Process(), []
    def factory(command, **options):
        commands.append(command)
        assert options['shell'] is False
        return process
    control = LocalProbeControl()
    assert FfprobeLocalAdjacency(Path('authenticated.exe'), process_factory=factory).resolve(request(), control) is None
    command = commands[0]
    assert command[command.index('-select_streams')+1] == 'v:0'
    window = command[command.index('-read_intervals')+1]
    assert float(window.split('%')[0]) > 80 and window.endswith('%+#512')
    assert process.terminated and control._process is None


def test_application_admits_one_probe_and_cancel_suppresses_stale_completion():
    entered, released, completed = Event(), Event(), []
    class Probe:
        def resolve(self, req, control):
            entered.set()
            assert released.wait(2)
            return object()
    service = AdjacentFrameService(Probe())
    try:
        assert service.start(request(), completed.append)
        assert entered.wait(2)
        assert not service.start(request(), completed.append)
        service.cancel()
        released.set()
    finally:
        released.set()
        service.shutdown()
    assert completed == []


def test_cancelled_owner_notifies_one_replacement_only_after_reaping():
    entered,released,idle=Event(),Event(),Event()
    class Probe:
        def resolve(self,request,control):
            entered.set()
            assert released.wait(2)
            return None
    service=AdjacentFrameService(Probe())
    try:
        assert service.start(request(),lambda result:None) and entered.wait(2)
        service.cancel()
        service.when_idle(idle.set)
        assert not idle.is_set()
        released.set()
        assert idle.wait(2) and service._current is None
    finally:
        released.set();service.shutdown()


def test_unreaped_probe_remains_owned_and_prevents_new_work():
    class Process:
        def poll(self): return None
        def terminate(self): pass
        def kill(self): pass
        def wait(self, timeout): raise subprocess.TimeoutExpired('unreaped',timeout)
    control, process = LocalProbeControl(), Process()
    control.spawn(lambda *a,**kw:process,['probe'])
    with pytest.raises(subprocess.TimeoutExpired): control.cancel()
    with pytest.raises(RuntimeError,match='live process'): control.release(process)
    assert control.owns_process and control._process is process
    completed = Event()
    class Probe:
        def resolve(self, request, job_control):
            job_control.spawn(lambda *a,**kw:process,['probe'])
            raise RuntimeError('cleanup failed')
    service = AdjacentFrameService(Probe())
    assert service.start(request(),lambda result:completed.set())
    assert completed.wait(2)
    assert not service.start(request(),lambda result:None)
    with pytest.raises(subprocess.TimeoutExpired): service.shutdown()
    assert service._current.owns_process
