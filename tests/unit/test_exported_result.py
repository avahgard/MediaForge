"""Completed output is an explicit fresh import, never an Export-success shortcut."""
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import gc
import pytest
from mediaforge.application.contracts.exported_result import ExportedResult
from mediaforge.application.contracts.image_export import NumberedOutput
from mediaforge.application.contracts.media import OpenMediaResult
from mediaforge.application.contracts.export import ExportJob
from mediaforge.application.services.export import ExportService
from mediaforge.application.services.images import ImageSourceService
from mediaforge.application.services.media import OpenMediaService
from mediaforge.application.services.exported_result import result_path
from mediaforge.features.media.export import ExportError
from mediaforge.infrastructure.images.reader import QtImageReader
from tests.unit.test_image_preview import Resources,write_image
from tests.unit.test_image_export import make_sequence
from tests.unit.resize_support import finish,metadata
from mediaforge.application.services.image_export import capture
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.filesystem.export_identity import capture_output
from mediaforge.features.media.export import ExportControl,ExportResult
from mediaforge.infrastructure.ffmpeg.export import FfmpegExportExecutor


def trusted(plan):
    path=Path(plan.path)
    paths=[path/plan.numbering.name(i) for i in range(plan.numbering.count)] if plan.kind=='sequence' else [path]
    with ReadLocks() as locks:
        for p in paths:locks.add(p)
        identity=capture_output(paths,[p.name for p in paths],path,ExportControl())
    return replace(plan,identity=identity,job_id=1)


def bound_service(plan):
    service=ImageSourceService(QtImageReader(),resources=Resources())
    owner=ExportService(FfmpegExportExecutor(None,image_service=service))
    owner._completed_result=plan;owner._job=ExportJob(1,'Completed',result=plan.path)
    service.result_preparer=owner
    return service


def test_completed_descriptor_is_frozen_and_requires_released_ownership(tmp_path):
    item=make_sequence(tmp_path);request=capture(item);entered=Event();release=Event()
    class Executor:
        def execute(self,r,c):
            entered.set();assert release.wait(2);r.destination.write_bytes(b'controlled')
            with ReadLocks() as locks:
                locks.add(r.destination)
                return ExportResult(r.destination,capture_output([r.destination],[r.destination.name],r.destination,c))
    service=ExportService(Executor());assert service.completed_result() is None
    assert service.start_request(request) and entered.wait(2);assert service.completed_result() is None
    release.set();assert finish(service).state=='Completed'
    result=service.completed_result();assert result.kind=='video' and result.rate==Fraction(24000,1001)
    item.sequence.rate=Fraction(30);assert service.completed_result()==result
    owner=service._processing_gate.acquire();assert service.completed_result() is None
    service._processing_gate.release(owner);assert service.completed_result()==result
    service.shutdown();assert service.completed_result() is None


@pytest.mark.parametrize('cancelled',[False,True])
def test_failed_or_cancelled_job_has_no_result_activation(tmp_path,cancelled):
    request=capture(make_sequence(tmp_path))
    class Executor:
        def execute(self,r,c):raise ExportError('cancelled' if cancelled else 'publication_unavailable','controlled')
    service=ExportService(Executor());assert service.start_request(request)
    assert finish(service).state==('Cancelled' if cancelled else 'Failed')
    assert service.completed_result() is None;service.shutdown()


def test_exported_audio_has_explicit_transport_result(tmp_path):
    request=SimpleNamespace(width=128,height=96)
    audio=ExportService._result_description(request,tmp_path/'sound.wav')
    assert (audio.kind,audio.width,audio.height,audio.format)==('audio',0,0,'wav')
    request=capture(make_sequence(tmp_path))
    assert ExportService._result_description(replace(request,container='gif'),tmp_path/'image.gif') is None


def test_single_numbered_result_is_prepared_as_still_and_preserves_source(tmp_path):
    path=tmp_path/'result_0001.png';write_image(path);before=path.read_bytes()
    plan=trusted(ExportedResult(str(path),'still',96,64,'png'));service=bound_service(plan)
    value=service.prepare_exported_preview(plan,'1:2',Event(),(800,560))
    assert value.raster.plan.width==48 and value.metadata.source_path==str(path)
    value.release();assert path.read_bytes()==before
    invalid=replace(plan,width=100);service.result_preparer._completed_result=invalid
    with pytest.raises(ValueError,match='geometry'):
        service.prepare_exported_preview(invalid,'Auto',Event(),(800,560))


def sequence_result(tmp_path):
    folder=tmp_path/'result';folder.mkdir();layout=NumberedOutput('result_','','.png',7,4,3)
    for i in range(3):write_image(folder/layout.name(i))
    return trusted(ExportedResult(str(folder),'sequence',96,64,'png',layout,Fraction(30000,1001)))


def test_result_manifest_has_exact_numbering_fps_and_all_signatures(tmp_path):
    result=sequence_result(tmp_path);service=bound_service(result)
    prepared=service.discover_exported_sequence(result,Event());m=prepared.manifest
    assert m.numbers==(7,8,9) and all(v.signature.sha256 for v in m.members)
    assert result.rate==Fraction(30000,1001) and m.count==3
    prepared.release();del m;gc.collect();assert service.budget.reserved==service.budget.overhead


@pytest.mark.parametrize('fault',['gap','extra','corrupt','geometry','numbering','cancel'])
def test_changed_result_manifest_refuses_before_activation(tmp_path,fault):
    result=sequence_result(tmp_path);folder=Path(result.path);cancel=Event()
    if fault=='gap':(folder/result.numbering.name(1)).unlink()
    if fault=='extra':(folder/'unrelated.txt').write_text('unexpected')
    if fault=='corrupt':(folder/result.numbering.name(2)).write_bytes(b'corrupt')
    if fault=='geometry':result=replace(result,width=100)
    if fault=='numbering':result=replace(result,numbering=replace(result.numbering,start=6))
    if fault=='cancel':cancel.set()
    service=bound_service(result)
    with pytest.raises((ValueError,OSError,ExportError)):service.discover_exported_sequence(result,cancel)


def test_result_path_rejects_wrong_shape_missing_relative_and_stream(tmp_path):
    path=tmp_path/'result.png';write_image(path);plan=ExportedResult(str(path),'still',96,64,'png')
    assert result_path(plan)==path
    for bad in ['relative.png',str(tmp_path/'missing.png'),str(tmp_path),str(path)+':stream']:
        with pytest.raises((ValueError,OSError)):result_path(replace(plan,path=bad))


@pytest.mark.parametrize('fault',['none','geometry','payload','changed','missing'])
def test_video_result_revalidates_before_returning_candidate(tmp_path,fault):
    path=tmp_path/'result.mp4';path.write_bytes(b'controlled video')
    plan=ExportedResult(str(path),'video',640,360,'mp4')
    guard=SimpleNamespace(check=lambda:None,close=lambda:None)
    service=OpenMediaService(None,result_preparer=SimpleNamespace(prepare_result=lambda *_:guard))
    def probe(value):
        if fault=='changed':path.write_bytes(b'changed length')
        return OpenMediaResult(metadata=replace(metadata(path),width=100 if fault=='geometry' else 640,format_name='png_pipe' if fault=='payload' else 'mov,mp4'))
    service.open_media=probe
    if fault=='missing':path.unlink()
    result=service.open_exported_result(plan)
    assert result.succeeded==(fault=='none')
    if result.guard:result.guard.close()


def test_cancelled_result_discovery_cannot_open_dialog_or_activate():
    from PySide6.QtCore import QCoreApplication
    from mediaforge.presentation.bridge.image_preview import ImagePreviewController
    from tests.unit.test_image_preview import wait_until
    app=QCoreApplication.instance() or QCoreApplication([])
    entered=Event();release=Event();released=[];discovered=[];painted=[]
    class Service:
        resources=Resources()
        def discover_exported_sequence(self,*args):
            entered.set();assert release.wait(2)
            return SimpleNamespace(release=lambda:released.append(True))
    controller=ImagePreviewController(Service(),lambda *args:painted.append(args))
    controller._backend=controller.qualified_backend
    controller.exportedDiscovered.connect(lambda *args:discovered.append(args))
    try:
        result=ExportedResult('D:/result','sequence',96,64,'png')
        assert controller.request(result.path,discover=True,exported=result)
        assert entered.wait(2);controller.cancel();release.set()
        wait_until(lambda:not controller.busy)
        assert released==[True] and not discovered and not painted
    finally:release.set();controller.shutdown()
