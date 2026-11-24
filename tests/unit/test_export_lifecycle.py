import json
from fractions import Fraction
from pathlib import Path
import subprocess
from threading import Event

import pytest

from mediaforge.application.services.export import ExportService, capture
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.features.media.export import ExportControl, ExportError
from mediaforge.infrastructure.ffmpeg.export import FfmpegExportExecutor
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegRuntime
from mediaforge.infrastructure.filesystem.export_output import ExportOutput
from tests.unit.resize_support import Child, finish, payload
from tests.unit.test_export_plan import item_at


class Factory:
    def __init__(self, source, *, block=None, invalid=False, failure=None, race=None):
        self.source = source
        self.block = block
        self.invalid = invalid
        self.failure = failure
        self.race = race
        self.calls = []
        self.children = []
        self.entered = Event()

    def __call__(self, args, **kwargs):
        phase = len(self.calls)
        self.calls.append(args)
        assert kwargs["shell"] is False
        if "-show_streams" in args:
            p = payload(1024,768)
            p['streams'][0].update(time_base='1/12800' if args[-1] == str(self.source) else '1/64000',start_pts=0)
            if args[-1] != str(self.source) and self.invalid:
                p["streams"][0]["sample_aspect_ratio"] = "2:1"
            text = json.dumps(p)
            if args[-1] != str(self.source) and self.race:
                self.race.write_bytes(b"other writer")
        elif "-encoders" in args:
            text = " V..... libx264 H264\n V..... prores_ks ProRes\n"
        else:
            assert args[-1] == "fd:"
            kwargs["stdin"].write(b"encoded")
            text = ""
        child = Child(text, block=self.block == phase, code=1 if self.failure == phase else 0)
        self.children.append(child)
        if self.block == phase:
            self.entered.set()
        return child


def executor(tmp_path, factory):
    return FfmpegExportExecutor(FfmpegRuntime(tmp_path/"ffmpeg.exe",tmp_path/"ffprobe.exe"), process_factory=factory)


@pytest.mark.parametrize("phase", range(4))
def test_cancel_reaps_each_phase_and_releases_shared_gate(tmp_path, phase):
    _, item = item_at(tmp_path)
    item.output.codec = "h264"
    factory = Factory(capture(item).source, block=phase)
    gate = MediaProcessingGate()
    service = ExportService(executor(tmp_path,factory),gate=gate)
    assert service.start(item)
    assert factory.entered.wait(2)
    assert gate.busy
    assert service.cancel()
    assert finish(service).state == "Cancelled"
    assert factory.children[-1].poll() is not None
    assert len(factory.calls) == phase+1
    assert not gate.busy and not list(tmp_path.glob(".mediaforge-export-*"))
    assert not capture(item).destination.exists()
    service.shutdown()


def test_copy_byte_identity_and_no_encode_child(tmp_path):
    _, item = item_at(tmp_path)
    source = capture(item).source
    factory = Factory(source)
    result = executor(tmp_path,factory).execute(capture(item),ExportControl())
    assert result.output.read_bytes() == source.read_bytes()
    assert len(factory.calls) == 2 and all("-show_streams" in a for a in factory.calls)


@pytest.mark.parametrize("invalid,failure", [(True,None),(False,2)])
def test_failure_never_publishes(tmp_path, invalid, failure):
    _, item = item_at(tmp_path)
    item.output.codec = "h264"
    request = capture(item)
    factory = Factory(request.source,invalid=invalid,failure=failure)
    service = ExportService(executor(tmp_path,factory))
    assert service.start(item)
    assert finish(service).state == "Failed"
    assert not request.destination.exists()
    assert request.source.read_bytes() == b"original"
    assert not list(tmp_path.glob(".mediaforge-export-*"))


@pytest.mark.native
def test_publication_collision_race_preserves_other_writer(tmp_path):
    _, item = item_at(tmp_path)
    request = capture(item)
    factory = Factory(request.source,race=request.destination)
    with pytest.raises(ExportError,match="appeared"):
        executor(tmp_path,factory).execute(request,ExportControl())
    assert request.destination.read_bytes() == b"other writer"
    assert request.source.read_bytes() == b"original"


def test_busy_and_poison_admission_precedes_factory_and_staging(tmp_path):
    _, item = item_at(tmp_path)
    factory = Factory(capture(item).source)
    gate = MediaProcessingGate()
    owner = gate.acquire()
    service = ExportService(executor(tmp_path,factory),gate=gate)
    assert not service.start(item)
    gate.poison(owner,"controlled unreaped Trim")
    assert not service.start(item)
    assert service.snapshot().error == "media_processing_busy"
    assert not factory.calls and not list(tmp_path.glob(".mediaforge-export-*"))


def test_unreaped_export_poison_blocks_other_processors_and_shutdown(tmp_path):
    _, item = item_at(tmp_path)
    item.output.codec = "h264"
    factory = Factory(capture(item).source,block=2)
    gate = MediaProcessingGate()
    adapter = executor(tmp_path,factory)
    def cannot_stop(child):
        raise subprocess.TimeoutExpired("controlled",2)
    adapter._processes._stop = cannot_stop
    service = ExportService(adapter,gate=gate)
    assert service.start(item) and factory.entered.wait(2)
    assert service.cancel()
    assert finish(service).error == "lifecycle_failed"
    assert gate.busy and gate.acquire() is None
    assert list(Path(item.output.folder).glob(".mediaforge-export-*"))
    with pytest.raises(RuntimeError,match="termination"):
        service.shutdown()


def test_shutdown_cancels_real_owned_worker_contract(tmp_path):
    _, item = item_at(tmp_path)
    factory = Factory(capture(item).source,block=0)
    service = ExportService(executor(tmp_path,factory))
    assert service.start(item) and factory.entered.wait(2)
    service.shutdown()
    assert not service.busy and service.snapshot().state == "Cancelled"
    assert not service.start(item)


def test_sar_only_command_does_not_resample_and_trim_is_half_open(tmp_path):
    _, item = item_at(tmp_path)
    item.resize.enabled = True
    item.resize.pixel_aspect = "16:9"
    item.trim.enabled = True
    item.trim.in_ms, item.trim.out_ms = 400,1200
    request = capture(item)
    command = executor(tmp_path,Factory(request.source)).command(request,tmp_path/"stage.mp4",{"start_time":"0.25","time_base":"1/12800"},1)
    vf = command[command.index("-vf")+1]
    assert "settb=expr=1/64000,trim=start_pts=41600:end_pts=92800,setpts=PTS-(41600)" in vf
    assert "setsar=4/3:max=65535" in vf
    assert "scale=" not in vf and "crop=" not in vf
    assert "asetpts=PTS-(13/20)/TB" in command[command.index("-af")+1]
    assert command[command.index("-enc_time_base:v:0")+1] == "filter"
    assert command[command.index("-video_track_timescale")+1] == "64000"
    assert "-r" not in command and "passthrough" in command


def test_missing_runtime_and_changed_source_do_not_stage_or_publish(tmp_path):
    _, item = item_at(tmp_path)
    request = capture(item)
    with pytest.raises(ExportError,match="unavailable"):
        FfmpegExportExecutor(None).execute(request,ExportControl())
    request.source.write_bytes(b"changed source")
    factory = Factory(request.source)
    with pytest.raises(ExportError,match="changed"):
        executor(tmp_path,factory).execute(request,ExportControl())
    assert not factory.calls and not list(tmp_path.glob('.mediaforge-export-*'))


def test_no_encoder_substitution_when_inventory_lacks_prores(tmp_path):
    _, item = item_at(tmp_path)
    item.output.codec='prores'; item.output.container='mov'
    request=capture(item)
    base=Factory(request.source)
    def no_prores(args,**kwargs):
        child=base(args,**kwargs)
        if '-encoders' in args:
            child.text=' V..... libx264 H264\n'
        return child
    with pytest.raises(ExportError,match="No alternative"):
        executor(tmp_path,no_prores).execute(request,ExportControl())
    assert len(base.calls)==2 and not list(tmp_path.glob('.mediaforge-export-*'))


def test_video_clock_uses_integer_source_start_and_exact_millisecond_origin(tmp_path):
    from dataclasses import replace
    from mediaforge.infrastructure.ffmpeg.export import _video_clock
    _, item=item_at(tmp_path)
    request=replace(capture(item),trim=(410,810))
    start,end,scale=_video_clock(request,{'time_base':'1/90000','start_pts':3003,'start_time':'0.033367'})
    assert scale==90000 and start*scale==39903 and end*scale==75903
    # A decimal display field must not replace the exact stream start ticks.
    assert start==Fraction(3003,90000)+Fraction(410,1000)
    with pytest.raises(ExportError,match='exact source/edit'):
        _video_clock(request,{'time_base':'1/2147483647','start_pts':0})


def test_muxer_cannot_silently_publish_a_coarser_time_base(tmp_path):
    _,item=item_at(tmp_path);item.output.codec='h264'
    request=capture(item);base=Factory(request.source)
    def coarse_mux(args,**kw):
        child=base(args,**kw)
        if '-show_streams' in args and args[-1]!=str(request.source):
            data=json.loads(child.text);data['streams'][0]['time_base']='1/25';child.text=json.dumps(data)
        return child
    with pytest.raises(ExportError,match='Container time base'):
        executor(tmp_path,coarse_mux).execute(request,ExportControl())
    assert not request.destination.exists() and not list(tmp_path.glob('.mediaforge-export-*'))
