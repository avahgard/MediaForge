"""FC-01 authoritative receipt, protected readiness and stale/cancel race gates."""
from dataclasses import replace
from pathlib import Path
from threading import Event
import os
import pytest
from PySide6.QtCore import QObject,Signal
from mediaforge.application.contracts.export import ExportJob
from mediaforge.application.contracts.exported_result import ExportedResult
from mediaforge.application.services.export import ExportService
from mediaforge.application.services.images import ImageSourceService
from mediaforge.features.media.export import ExportControl,ExportError
from mediaforge.infrastructure.ffmpeg.export import FfmpegExportExecutor
from mediaforge.infrastructure.images.reader import QtImageReader
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.filesystem.export_identity import capture_output
from tests.unit.test_exported_result import trusted,bound_service,sequence_result
from tests.unit.test_image_preview import write_image,Resources
from tests.unit.test_image_export import make_sequence,executor
from mediaforge.application.services.image_export import capture


def test_changed_valid_png_before_first_open_not_its_own_expected_signature(tmp_path):
    import struct,zlib
    def png(rgb):
        def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
        raw=(b'\0'+bytes(rgb)*128)*96
        return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',128,96,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw,0))+chunk(b'IEND',b'')
    path=tmp_path/'result.png';path.write_bytes(png((200,100,80)))
    plan=trusted(ExportedResult(str(path),'still',128,96,'png'));service=bound_service(plan)
    before=path.stat();path.write_bytes(png((5,10,240)))
    # Even restoring timestamps cannot turn current bytes into expected authority.
    os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
    after=path.stat();assert (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)==(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)
    assert QtImageReader().probe(str(path)).facts.width==128
    with pytest.raises(ExportError,match='changed'):service.prepare_exported_preview(plan,'Auto',Event(),(800,560))
    assert not service.result_preparer._processing_gate.busy


def test_every_changed_sequence_member_is_checked_before_first_frame(tmp_path):
    plan=sequence_result(tmp_path);service=bound_service(plan)
    last=Path(plan.path)/plan.numbering.name(2);write_image(last,(96,64),True)
    with pytest.raises(ExportError,match='changed'):service.prepare_exported_sequence_preview(plan,None,0,'Auto',Event(),(800,560))
    assert not service.result_preparer._processing_gate.busy


def test_locks_cover_probe_decode_gui_handoff_and_alias_writes(tmp_path):
    path=tmp_path/'result.png';write_image(path);plan=trusted(ExportedResult(str(path),'still',96,64,'png'))
    alias=tmp_path/'alias.png';os.link(path,alias);service=bound_service(plan)
    prepared=service.prepare_exported_preview(plan,'Auto',Event(),(800,560))
    assert service.result_preparer._processing_gate.busy
    for action in (lambda:path.write_bytes(b'changed'),lambda:alias.write_bytes(b'changed'),lambda:path.rename(tmp_path/'moved.png')):
        with pytest.raises(OSError):action()
    prepared.source_guard.check();prepared.close_source();prepared.release()
    assert not service.result_preparer._processing_gate.busy
    path.write_bytes(b'after activation locks may close')


@pytest.mark.parametrize('fault',['cancel','new_same_path_job','shutdown'])
def test_prepared_picture_checks_result_generation_before_activation(tmp_path,fault):
    path=tmp_path/'result.png';write_image(path);plan=trusted(ExportedResult(str(path),'still',96,64,'png'));service=bound_service(plan);cancel=Event()
    prepared=service.prepare_exported_preview(plan,'Auto',cancel,(800,560));owner=service.result_preparer
    if fault=='cancel':cancel.set()
    elif fault=='new_same_path_job':owner._job=replace(owner._job,job_id=2);owner._completed_result=replace(plan,job_id=2)
    else:owner.shutdown()
    with pytest.raises(ExportError):prepared.source_guard.check()
    prepared.release();assert not owner._processing_gate.busy


def test_uncertain_read_lock_cleanup_keeps_owner_and_poisons_gate(tmp_path):
    path=tmp_path/'result.png';write_image(path);plan=trusted(ExportedResult(str(path),'still',96,64,'png'));service=bound_service(plan)
    held=service.result_preparer.prepare_result(plan);close=held.native.close
    def refuse():raise OSError('controlled handle cleanup refusal')
    held.native.close=refuse
    with pytest.raises(OSError):held.close()
    assert service.result_preparer._processing_gate.failure and held in service.result_preparer._result_failures
    assert service.result_preparer.completed_result() is None
    # Test-owned real handles are reconciled without clearing the poisoned gate.
    held.native.close=close;close();assert service.result_preparer._processing_gate.failure


def test_result_button_rejects_stale_same_path_job_id():
    from mediaforge.presentation.bridge.export import ExportViewModel
    class Owner(QObject):
        workspaceChanged=Signal();canOpen=True
        def openExportedResult(self,plan):opened.append(plan);return True
    first=ExportedResult('D:/result.png','still',96,64,'png',job_id=8)
    second=replace(first,job_id=9);opened=[];parent=Owner()
    use=type('Use',(),dict(busy=False,completed_result=lambda _:second))()
    vm=ExportViewModel(use,None,None,parent);vm._job=ExportJob(8,'Completed',result=first.path)
    assert not vm.canOpenResult and not vm.openExportedResult() and not opened
    vm._job=replace(vm._job,job_id=9);assert vm.openExportedResult() and opened==[second]


@pytest.mark.native
@pytest.mark.integration
def test_real_validated_export_receipt_exists_before_commit_and_survives_links(tmp_path,monkeypatch):
    import mediaforge.infrastructure.filesystem.export_identity as identities
    item=make_sequence(tmp_path);item.output.kind='sequence';item.output.container='png';request=capture(item)
    original=identities.capture_output;captured=[]
    def checked(paths,names,*args,**kwargs):
        for path in paths:
            with pytest.raises(OSError):path.write_bytes(b'precommit corrupt')
        receipt=original(paths,names,*args,**kwargs);captured.append(receipt);return receipt
    monkeypatch.setattr(identities,'capture_output',checked)
    result=executor().execute(request,ExportControl())
    assert result.identity is captured[0] and tuple(v.name for v in result.identity.files)==tuple(request.numbering.name(i) for i in range(request.count))
    from mediaforge.infrastructure.filesystem.export_identity import file_identity
    for identity in result.identity.files:assert file_identity(result.output/identity.name,identity.name,ExportControl())==identity


@pytest.mark.native
@pytest.mark.integration
def test_video_readiness_refuses_trusted_but_undecodable_payload(tmp_path):
    import subprocess
    item=make_sequence(tmp_path);request=capture(item);runner=executor();result=runner.execute(request,ExportControl());path=result.output
    raw=bytearray(path.read_bytes());marker=raw.index(b'mdat');size=int.from_bytes(raw[marker-4:marker],'big');raw[marker+4:marker-4+size]=bytes(size-8);path.write_bytes(raw)
    plan=trusted(ExportedResult(str(path),'video',128,96,'mp4'))
    # Deliberately seed the bad hash only to test readiness independently of
    # identity rejection. Real Export never receives this artificial receipt.
    owner=ExportService(runner);owner._job=ExportJob(1,'Completed',result=plan.path);owner._completed_result=plan
    with pytest.raises(ExportError):owner.prepare_result(plan)
    assert not owner._processing_gate.busy and not owner._result_preparations
    assert runner._processes._unreaped_process is None


@pytest.mark.native
@pytest.mark.integration
def test_accepted_video_new_file_and_confirmed_replacement_keep_trusted_receipts(tmp_path):
    from tests.unit.resize_support import metadata,finish
    from mediaforge.application.state.workspace import MediaWorkspaceSession
    movie=executor().execute(capture(make_sequence(tmp_path)),ExportControl()).output
    workspace=MediaWorkspaceSession();item,_=workspace.add(metadata(movie,128,96))
    item.output.folder=str(movie.parent);item.output.filename='legacy-video'
    service=ExportService(executor());request=service.prepare(item)
    assert service.start_request(request) and finish(service).state=='Completed'
    first=service.completed_result();assert first.identity and first.job_id==1
    held=service.prepare_result(first)
    with pytest.raises(OSError):Path(first.path).write_bytes(b'changed')
    held.close()
    replacement=service.prepare(item,allow_replacement=True);assert replacement.replacement is not None
    assert service.start_request(replacement) and finish(service).state=='Completed'
    second=service.completed_result();assert second.job_id==2 and second.identity.files!=first.identity.files
    with pytest.raises(ExportError,match='no longer'):service.prepare_result(first)
    held=service.prepare_result(second);held.check();held.close();service.shutdown()


@pytest.mark.parametrize('action',['cancel','shutdown'])
def test_result_preparation_child_is_cancelled_and_reaped(tmp_path,action):
    from threading import Thread
    from tests.unit.resize_support import Child
    from mediaforge.infrastructure.ffmpeg.runtime import FfmpegRuntime
    path=tmp_path/'controlled.mp4';path.write_bytes(b'unit child ownership fixture')
    plan=trusted(ExportedResult(str(path),'video',128,96,'mp4'));entered=Event();children=[];errors=[]
    def factory(*args,**kwargs):
        child=Child('',block=True);children.append(child);entered.set();return child
    runner=FfmpegExportExecutor(FfmpegRuntime(tmp_path/'ffmpeg.exe',tmp_path/'ffprobe.exe'),process_factory=factory)
    owner=ExportService(runner);owner._job=ExportJob(1,'Completed',result=plan.path);owner._completed_result=plan
    def work():
        try:owner.prepare_result(plan)
        except BaseException as error:errors.append(error)
    thread=Thread(target=work);thread.start();assert entered.wait(2)
    if action=='cancel':owner.cancel_result_preparation()
    else:owner.shutdown()
    thread.join(5);assert not thread.is_alive() and errors
    assert children[0].poll() is not None and 'terminate' in children[0].actions
    assert runner._processes._unreaped_process is None and not owner._processing_gate.busy
    assert not owner._result_preparations


def test_result_job_cannot_change_inside_activation_commit(tmp_path):
    from threading import Thread
    path=tmp_path/'result.png';write_image(path);plan=trusted(ExportedResult(str(path),'still',96,64,'png'));service=bound_service(plan);owner=service.result_preparer
    held=owner.prepare_result(plan);attempted=Event();changed=Event()
    def change():
        attempted.set()
        with owner._lock:owner._job=replace(owner._job,job_id=2);changed.set()
    with held.activating():
        thread=Thread(target=change);thread.start();assert attempted.wait(1)
        assert not changed.wait(.05);held.check()
    thread.join(2);assert changed.is_set()
    with pytest.raises(ExportError):held.check()
    held.close()


@pytest.mark.native
@pytest.mark.integration
def test_receipt_has_its_own_bounded_lifetime_and_refusal_precedes_lock_allocation(tmp_path,monkeypatch):
    import gc
    runner=executor();budget=runner._image_service.budget;base=budget.reserved
    result=runner.execute(capture(make_sequence(tmp_path)),ExportControl())
    # Movie is one retained identity; the processing/control reservation ended.
    assert budget.reserved==base+4096
    identity=result.identity;del result;gc.collect();assert budget.reserved==base+4096
    del identity;gc.collect();assert budget.reserved==base
    path=tmp_path/'result.png';write_image(path);plan=trusted(ExportedResult(str(path),'still',96,64,'png'))
    service=bound_service(plan);budget=service.budget
    block=budget.reserve_peak(budget.limit-budget.reserved-4*1024**2)
    # Control/receipt data follows the current host-derived admission rather
    # than the superseded fixed 256 MiB total-ledger ceiling. Simulate exactly
    # four MiB of available logical headroom; the full receipt needs more.
    budget.control_admission=lambda amount:budget.reserved+4*1024**2
    def forbidden(*args):raise AssertionError('Locks allocated before resource refusal')
    monkeypatch.setattr(ReadLocks,'parents',forbidden)
    monkeypatch.setattr(ReadLocks,'add',forbidden)
    with pytest.raises(ValueError,match='admitted live CPU'):service.result_preparer.prepare_result(plan)
    assert not service.result_preparer._processing_gate.busy and not service.result_preparer._result_preparations
    budget.release(block)


def test_excessive_result_path_metadata_refuses_before_filesystem_walk(tmp_path):
    path=tmp_path/'result.png';write_image(path);plan=trusted(ExportedResult(str(path),'still',96,64,'png'))
    deep='D:/'+('/'.join(['p']*129))+'/result.png'
    plan=replace(plan,path=deep,identity=replace(plan.identity,path=deep));service=bound_service(plan)
    with pytest.raises(ExportError,match='metadata'):service.result_preparer.prepare_result(plan)
    assert not service.result_preparer._processing_gate.busy
