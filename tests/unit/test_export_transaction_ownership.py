"""Real NTFS failure-return controls with explicit final ownership of test handles."""
import ctypes
import json
import time
from pathlib import Path
import pytest

from mediaforge.application.services.export import ExportService
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.features.media.export import ExportControl, ExportError, ExportResult
from mediaforge.infrastructure.filesystem.confirmed_output import ConfirmedOutput
from mediaforge.infrastructure.filesystem.export_output import ExportOutput
from tests.unit.test_export_plan import item_at


@pytest.mark.native
@pytest.mark.parametrize('fault', ['both_false','rollback_false','tx_close_false','file_close_false',
                                  'ancestor_close_false','commit_false','committed_tx_close_false','cancelled_both_false'])
def test_real_transaction_cleanup_outcome_and_gate(tmp_path,monkeypatch,fault):
    _,item=item_at(tmp_path)
    item.output.filename = 'existing separate export'
    target=Path(item.output.folder)/(item.output.filename+'.mp4');target.write_bytes(b'previous destination')
    held={};events=[];gate=MediaProcessingGate()
    import mediaforge.infrastructure.filesystem.export_output as module
    def injected(path,approved):
        guard=ConfirmedOutput(path,approved)
        close=guard.api.CloseHandle;rollback=guard.ktm.RollbackTransaction;move=guard.api.MoveFileTransactedW
        held.update(guard=guard,close=close,rollback=rollback)
        def fail_close(handle):
            match=(handle==guard.transaction and fault in ('both_false','tx_close_false','committed_tx_close_false','cancelled_both_false'))
            match|=bool(guard.handles and handle==guard.handles[-1] and fault=='file_close_false')
            match|=bool(guard.handles and handle==guard.handles[0] and fault=='ancestor_close_false')
            if match: events.append(('close_false',int(handle)));ctypes.set_last_error(5);return 0
            return close(handle)
        def fail_rollback(tx):
            if fault in ('both_false','rollback_false','cancelled_both_false'):
                events.append(('rollback_false',int(tx)));ctypes.set_last_error(5);return 0
            return rollback(tx)
        def watch_move(*args):events.append(('move',));return move(*args)
        guard.api.CloseHandle=fail_close;guard.ktm.RollbackTransaction=fail_rollback;guard.api.MoveFileTransactedW=watch_move
        if fault=='commit_false':guard.ktm.CommitTransaction=lambda _: 0
        return guard
    monkeypatch.setattr(module,'ConfirmedOutput',injected)
    class Executor:
        def execute(self,request,control):
            with ExportOutput(request.source,target,replacement=request.replacement) as output:
                held['output']=output
                with output.staging.descriptor() as partial:
                    partial.write(b'validated candidate')
                if fault.startswith('cancelled'):
                    assert control.cancel();control.check()
                if fault in ('file_close_false','commit_false','committed_tx_close_false'):
                    return ExportResult(output.publish(control))
                raise ExportError('process_failed','controlled encoder failure')
    service=ExportService(Executor(),gate=gate)
    try:
        request=service.prepare(item, allow_replacement=True);assert service.start_request(request)
        end=time.monotonic()+5
        while service.busy and time.monotonic()<end:time.sleep(.01)
        assert not service.busy
        guard=held['guard'];job=service.snapshot();output=held['output']
        unresolved=fault not in ('rollback_false','commit_false')
        blocked=False
        try:
            with target.open('r+b'):pass
        except OSError:blocked=True
        row=dict(fault=fault,state=job.state,error=job.error,outcome=guard.outcome,transaction=guard.transaction,
                 handles=list(guard.handles),gate_poisoned=gate.busy,writer_blocked=blocked,
                 staging_retained=output.directory is not None,events=events,cleanup_errors=guard.cleanup_errors)
        (tmp_path/'ownership-proof.json').write_text(json.dumps(row,indent=2),encoding='utf-8')
        assert request.source.read_bytes()==b'original'
        assert target.read_bytes()==(b'validated candidate' if fault=='committed_tx_close_false' else b'previous destination')
        assert not job.result  # Cleanup failure after commit is never reported as Completed.
        if unresolved:
            assert job.error=='lifecycle_failed' and job.state=='Failed' and gate.busy
            assert guard.unresolved and service._control.retained_resources==(output,)
            assert output.directory.is_dir() and 'retained' in job.message
            with pytest.raises(RuntimeError,match='cleanup'):service.shutdown()
            assert not service.start_request(request)
            assert service._control.retained_resources==(output,)
        else:
            assert not guard.unresolved and not gate.busy and not list(tmp_path.glob('.mediaforge-export-*'))
            service.shutdown()
        if fault=='rollback_false':assert guard.outcome=='rolled_back_by_last_close' and not blocked
        if fault in ('both_false','cancelled_both_false'):assert guard.transaction is not None and blocked
        if fault=='tx_close_false':assert guard.outcome=='rolled_back' and not blocked
        if fault=='file_close_false':assert guard.handles and ('move',) not in events
    finally:
        # Deliberately retained resources belong to this test. Restore REAL APIs,
        # close only still-owned handles, roll back/close our own sole transaction.
        if 'guard' in held:
            guard=held['guard'];guard.api.CloseHandle=held['close'];guard.ktm.RollbackTransaction=held['rollback']
            for handle in guard.handles[:]:
                assert held['close'](handle);guard.handles.remove(handle)
            if guard.transaction is not None:
                if not guard.committed and guard.outcome!='rolled_back':assert held['rollback'](guard.transaction)
                assert held['close'](guard.transaction);guard.transaction=None
            assert not guard.unresolved
            if 'output' in held:
                held['output']._cleanup()


def test_failed_close_does_not_discard_handle_or_attempt_move():
    events=[]
    class API:
        def CloseHandle(self,handle):events.append(('failed_close',handle));return 0
        def MoveFileTransactedW(self,*_):raise AssertionError('Move after failed close')
    guard=object.__new__(ConfirmedOutput);guard.target=Path('test-only fake target');guard.handles=[111]
    guard.api=API();guard.check=lambda:None
    with pytest.raises(OSError):guard.publish(Path('test-only fake staged'))
    assert guard.handles==[111] and events==[('failed_close',111)]


@pytest.mark.native
@pytest.mark.parametrize('error',[OSError,AttributeError])
def test_new_file_and_unsupported_replacement_without_txf(tmp_path,monkeypatch,error):
    def unavailable(*_):raise error('controlled unavailable TxF')
    import mediaforge.infrastructure.filesystem.export_output as module
    monkeypatch.setattr(module,'ConfirmedOutput',unavailable)
    source=tmp_path/'source';source.write_bytes(b'source');target=tmp_path/'new.mp4'
    with ExportOutput(source,target) as output:
        with output.staging.descriptor() as partial:
            partial.write(b'valid')
        assert output.publish(ExportControl())==target
    assert target.read_bytes()==b'valid'
    with pytest.raises(ExportError,match='new filename'):
        with ExportOutput(source,target,replacement=(1,2,3,4)):pass
    assert target.read_bytes()==b'valid' and source.read_bytes()==b'source'
    assert not list(tmp_path.glob('.mediaforge-export-*'))


def test_staging_cleanup_failure_retains_owner_and_poison(tmp_path):
    _,item=item_at(tmp_path);held={};gate=MediaProcessingGate()
    class Executor:
        def execute(self,request,control):
            with ExportOutput(request.source,request.destination) as output:
                held.update(output=output,directory=output.directory)
                (output.directory/'foreign').write_bytes(b'external evidence')
                raise ExportError('process_failed','controlled encode failure')
    service=ExportService(Executor(),gate=gate)
    assert service.start(item)
    end=time.monotonic()+3
    while service.busy and time.monotonic()<end:time.sleep(.01)
    assert service.snapshot().error=='lifecycle_failed' and gate.busy
    assert service._control.retained_resources==(held['output'],) and held['directory'].is_dir()
    with pytest.raises(RuntimeError,match='cleanup'):service.shutdown()
    assert (held['directory']/'foreign').read_bytes()==b'external evidence'
    assert not held['output'].staged.exists()
    staging=held['output'].staging
    staging._close('directory_anchor')
    staging.parents.close()


@pytest.mark.native
@pytest.mark.integration
def test_real_encoder_failure_with_unresolved_transaction(tmp_path,monkeypatch):
    import hashlib,subprocess
    from mediaforge.application.services.media import OpenMediaService
    from mediaforge.application.state.workspace import MediaWorkspaceSession
    from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe
    from mediaforge.infrastructure.ffmpeg.export import FfmpegExportExecutor
    from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
    runtime=resolve_ffmpeg_runtime();source=tmp_path/'real source.mp4'
    subprocess.run([str(runtime.ffmpeg),'-v','error','-n','-f','lavfi','-i','color=s=160x120:r=25:d=0.4',
                    '-c:v','libx264',str(source)],capture_output=True,check=True,timeout=20)
    m=OpenMediaService(FfprobeMediaMetadataProbe(runtime.ffprobe)).open_media(str(source)).metadata
    item=MediaWorkspaceSession().add(m)[0];item.output.codec='h264'
    item.output.filename = 'existing separate export'
    target=tmp_path/(item.output.filename+'.mp4');target.write_bytes(b'previous output');held={};children=[]
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    import mediaforge.infrastructure.filesystem.export_output as module
    def injected(path,approved):
        guard=ConfirmedOutput(path,approved);close=guard.api.CloseHandle;rollback=guard.ktm.RollbackTransaction
        held.update(guard=guard,close=close,rollback=rollback)
        guard.ktm.RollbackTransaction=lambda _:0
        guard.api.CloseHandle=lambda h:0 if h==guard.transaction else close(h)
        return guard
    monkeypatch.setattr(module,'ConfirmedOutput',injected)
    def factory(args,**kwargs):
        args=list(args)
        if '-vf' in args:args[args.index('-vf')+1]='invalid_f01_controlled_filter'
        child=subprocess.Popen(args,**kwargs);children.append(child);return child
    gate=MediaProcessingGate();service=ExportService(FfmpegExportExecutor(runtime,process_factory=factory),gate=gate)
    try:
        assert service.start_request(service.prepare(item, allow_replacement=True))
        end=time.monotonic()+10
        while service.busy and time.monotonic()<end:time.sleep(.01)
        assert not service.busy and service.snapshot().error=='lifecycle_failed' and gate.busy
        assert all(p.poll() is not None for p in children)
        assert target.read_bytes()==b'previous output' and hashlib.sha256(source.read_bytes()).hexdigest()==digest
        output=service._control.retained_resources[0]
        assert output.directory.is_dir() and output._guard.transaction is not None
        (tmp_path/'real-fault-proof.json').write_text(json.dumps(dict(error=service.snapshot().error,
            gate_poisoned=gate.busy,outcome=output._guard.outcome,transaction=output._guard.transaction,
            staging=str(output.directory),children_reaped=len(children),source_sha256=digest),indent=2),encoding='utf-8')
        with pytest.raises(RuntimeError,match='cleanup'):service.shutdown()
    finally:
        if 'guard' in held:
            guard=held['guard']
            for handle in guard.handles[:]:assert held['close'](handle);guard.handles.remove(handle)
            if guard.transaction is not None:
                assert held['rollback'](guard.transaction);assert held['close'](guard.transaction);guard.transaction=None
            output._cleanup()
