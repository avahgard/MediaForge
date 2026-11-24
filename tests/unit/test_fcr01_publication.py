"""FCR-01: real competing process, exact validated object and prior-destination bytes."""
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import pytest
from mediaforge.application.services.export import ExportService
from mediaforge.application.state.workspace import MediaWorkspaceSession
from mediaforge.features.media.export import ExportControl
from mediaforge.infrastructure.filesystem.export_output import ExportOutput
from mediaforge.infrastructure.filesystem.validated_file import ValidatedFile
from tests.unit.test_image_export import executor,make_sequence
from tests.unit.resize_support import metadata,finish
from mediaforge.application.services.image_export import capture

pytestmark=[pytest.mark.native,pytest.mark.integration]

# Same native substitution as the Independent Review; no mutation of the final path.
WRITER="from pathlib import Path;import sys,json;s=Path(sys.argv[1]);p=Path(sys.argv[2]);s.rename(p);s.write_bytes(b'UNVALIDATED PUBLICATION SWAP');print(json.dumps(dict(renamed=True,replaced=True)))"


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def setup(tmp_path,mode):
    runner=executor();source=runner.execute(capture(make_sequence(tmp_path)),ExportControl()).output
    source_hash=sha(source);workspace=MediaWorkspaceSession();item,_=workspace.add(metadata(source,128,96))
    item.output.folder=str(tmp_path);item.output.filename='legacy-result';service=ExportService(runner)
    before=None
    if mode=='replacement':
        assert service.start_request(service.prepare(item));job=finish(service);assert job.state=='Completed',job
        before=Path(job.result).read_bytes()
    return runner,source,source_hash,item,service,before


def writer(stage,saved):
    # Match both ends of the Windows text pipe. Parent -X utf8 alone leaves
    # the writer's localized OS error in CP1251; a failed decoder reader then
    # yields stderr=None and destroys the actual WinError refusal evidence.
    return subprocess.run([sys.executable,'-X','utf8','-B','-c',WRITER,str(stage),str(saved)],
                          capture_output=True,text=True,encoding='utf-8',errors='strict',timeout=10)


def decoded(runner,path):
    return runner._processes.run([str(runner._runtime.ffmpeg),'-nostdin','-v','error','-xerror',
        '-i',str(path),'-map','0:v:0','-an','-threads:v','1','-f','framemd5','-'],ExportControl(),10)


@pytest.mark.parametrize('mode,boundary',[
    ('new','publish'),('new','rename'),('replacement','publish'),
    ('replacement','rename'),('replacement','commit')])
def test_competitor_cannot_substitute_through_commit(tmp_path,monkeypatch,mode,boundary):
    runner,source,original,item,service,before=setup(tmp_path,mode)
    request=service.prepare(item,allow_replacement=mode=='replacement')
    original_publish=ExportOutput.publish;original_rename=ValidatedFile.rename;attacks=[];receipts=[]
    def attack(output):
        child=writer(output.staged,tmp_path/'validated-kept.mp4')
        attacks.append(dict(exit=child.returncode,stdout=child.stdout,stderr=child.stderr))
        (tmp_path/'writer-attempt.json').write_text(json.dumps(attacks,indent=2),encoding='utf-8')
        # Before rename the open owner denies DELETE; after transacted rename,
        # TxF additionally reserves the original name until commit/rollback.
        expected_error='WinError 6800' if boundary=='commit' else 'WinError 32'
        assert child.returncode!=0 and expected_error in child.stderr,attacks
        assert not (tmp_path/'validated-kept.mp4').exists()
    def publish(output,control):
        receipts.append(output._validated.expected)
        assert output._validated.prepared and output._validated.handle is not None
        if boundary=='publish':attack(output)
        elif boundary=='rename':
            def rename(held,*args,**kwargs):attack(output);return original_rename(held,*args,**kwargs)
            monkeypatch.setattr(ValidatedFile,'rename',rename)
        elif boundary=='commit':
            commit=output._guard.ktm.CommitTransaction
            def intercepted(tx):attack(output);return commit(tx)
            output._guard.ktm.CommitTransaction=intercepted
        return original_publish(output,control)
    monkeypatch.setattr(ExportOutput,'publish',publish)
    assert service.start_request(request);job=finish(service)
    assert job.state=='Completed',job
    assert len(attacks)==1 and sha(request.destination)==receipts[0].sha256==original
    plan=service.completed_result();assert plan and plan.job_id==job.job_id and plan.identity.files[0]==receipts[0]
    assert decoded(runner,request.destination)==decoded(runner,source)
    assert sha(source)==original and not service._processing_gate.busy and not service._processing_gate.failure
    assert not list(tmp_path.glob('.mediaforge-export-*'));service.shutdown()
    (tmp_path/'publication-proof.json').write_text(json.dumps(dict(mode=mode,boundary=boundary,attacks=attacks,
        completed=True,output_sha256=sha(request.destination),source_sha256=original,receipt_sha256=receipts[0].sha256,
        same_decoded_frames=True,prior_sha256=hashlib.sha256(before).hexdigest() if before else None),indent=2),encoding='utf-8')


@pytest.mark.parametrize('mode',['new','replacement'])
def test_swap_in_untrusted_handle_acquisition_gap_refuses_before_publish(tmp_path,monkeypatch,mode):
    _,source,original,item,service,before=setup(tmp_path,mode)
    request=service.prepare(item,allow_replacement=mode=='replacement')
    bind=ExportOutput.bind_validated;attacks=[]
    def swap(output,*args,**kwargs):
        child=writer(output.staged,tmp_path/'validated-kept.mp4');attacks.append(child.returncode)
        assert child.returncode==0,child.stderr
        return bind(output,*args,**kwargs)
    monkeypatch.setattr(ExportOutput,'bind_validated',swap)
    assert service.start_request(request);job=finish(service)
    assert attacks==[0] and job.state=='Failed' and job.error=='lifecycle_failed',job
    assert (request.destination.read_bytes() if request.destination.exists() else None)==before
    assert sha(tmp_path/'validated-kept.mp4')==original==sha(source)
    assert service.completed_result() is None and service._processing_gate.failure == 'export_resource_cleanup_failed'
    output, = service._control.retained_resources
    assert output.staged.read_bytes() == b'UNVALIDATED PUBLICATION SWAP'
    with pytest.raises(RuntimeError,match='cleanup'):
        service.shutdown()
    output.staging._close('partial_anchor')
    output.staging._close('directory_pin')
    output.staging.parents.close()


@pytest.mark.parametrize('mode,fault',[('new','cancel'),('new','collision'),
    ('replacement','cancel'),('replacement','rename'),('replacement','commit')])
def test_precommit_refusals_keep_previous_bytes(tmp_path,monkeypatch,mode,fault):
    _,source,original,item,service,before=setup(tmp_path,mode)
    request=service.prepare(item,allow_replacement=mode=='replacement')
    publish=ExportOutput.publish;expected=before
    def refuse(output,control):
        nonlocal expected
        if fault=='cancel':assert control.cancel()
        elif fault=='collision':
            expected=b'independent existing destination';request.destination.write_bytes(expected)
        elif fault=='rename':
            def denied(*_):ctypes.set_last_error(5);return 0
            output._validated.api.SetFileInformationByHandle=denied
        elif fault=='commit':output._guard.ktm.CommitTransaction=lambda _:0
        return publish(output,control)
    monkeypatch.setattr(ExportOutput,'publish',refuse)
    assert service.start_request(request);job=finish(service)
    assert job.state==('Cancelled' if fault=='cancel' else 'Failed'),job
    assert (request.destination.read_bytes() if request.destination.exists() else None)==expected
    assert service.completed_result() is None and sha(source)==original
    assert not service._processing_gate.busy and not service._processing_gate.failure
    assert not list(tmp_path.glob('.mediaforge-export-*'));service.shutdown()


def test_new_validated_video_does_not_require_txf(tmp_path,monkeypatch):
    _,source,original,item,service,_=setup(tmp_path,'new')
    import mediaforge.infrastructure.filesystem.export_output as module
    def unavailable(*_):raise OSError('controlled absent TxF')
    monkeypatch.setattr(module,'ConfirmedOutput',unavailable)
    assert service.start_request(service.prepare(item));job=finish(service)
    assert job.state=='Completed' and sha(job.result)==original==sha(source)
    assert service.completed_result() is not None;service.shutdown()


def test_failed_bound_handle_close_retains_owner_and_poison_after_valid_commit(tmp_path,monkeypatch):
    _,source,original,item,service,_=setup(tmp_path,'replacement')
    request=service.prepare(item,allow_replacement=True)
    held={};publish=ExportOutput.publish
    def fail_close(output,control):
        owner=output._validated;close=owner.api.CloseHandle
        held.update(owner=owner,close=close,output=output)
        def deny(handle):
            if handle==owner.handle:ctypes.set_last_error(5);return 0
            return close(handle)
        owner.api.CloseHandle=deny
        return publish(output,control)
    monkeypatch.setattr(ExportOutput,'publish',fail_close)
    try:
        assert service.start_request(request);job=finish(service)
        assert job.state=='Failed' and job.error=='lifecycle_failed',job
        assert service.completed_result() is None and service._processing_gate.failure
        assert f'Output was published to {request.destination} before cleanup failed.' in job.message
        assert held['owner'].handle is not None and held['output'] in service._control.retained_resources
        # The committed file is the validated object; use the protected handle's
        # expected receipt while normal path opens remain denied by DELETE ownership.
        assert held['owner'].expected.sha256==original==sha(source)
        with pytest.raises(RuntimeError):service.shutdown()
    finally:
        if held:
            held['owner'].api.CloseHandle=held['close'];held['output']._cleanup()
    assert sha(request.destination)==original and not list(tmp_path.glob('.mediaforge-export-*'))
