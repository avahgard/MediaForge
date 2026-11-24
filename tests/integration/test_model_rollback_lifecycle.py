"""Permanent native model transaction contention/retirement/crash coverage."""
from dataclasses import replace
from pathlib import Path
import ctypes as C,hashlib,importlib.util,json,os,subprocess,sys
import pytest
from mediaforge.application.contracts.models import ModelError,ModelMember,ModelSpec,ModelState
from mediaforge.application.services.models import ModelService
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.models.store import PortableModelStore
from mediaforge.infrastructure.models.owned import Ownership
from mediaforge.infrastructure.models.installation_lifecycle import Installation,pending_path

pytestmark=[pytest.mark.native,pytest.mark.integration]
HELPER=Path(__file__).resolve().parents[1]/'helpers/model_transaction_actor.py'
_spec=importlib.util.spec_from_file_location('transaction_actor',HELPER)
_actor=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_actor);token=_actor.token

@pytest.fixture(scope='module',autouse=True)
def ordinary_medium_token():
    actual=token()
    assert actual==dict(integrity='S-1-16-8192',elevated=False),actual
    return actual

def fixture(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'Portable Модели/Data/v2');store=PortableModelStore(paths)
    spec=ModelSpec('synthetic','revision-1',(ModelMember('model/weights.bin',3,hashlib.sha256(b'abc').hexdigest()),),'runtime',True)
    service=ModelService((spec,),store,runtime_fingerprint='runtime',smoke=lambda *a:None)
    source=tmp_path/'source';(source/'model').mkdir(parents=True);(source/'model/weights.bin').write_bytes(b'abc')
    paths.prepare();return paths,store,spec,service,source

def launch(paths,*arguments):
    env=paths.child_environment({k:os.environ[k] for k in ('SYSTEMROOT','WINDIR') if k in os.environ})
    return subprocess.Popen([sys.executable,'-I','-B',str(HELPER),*map(str,arguments)],stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=paths.temporary,env=env)

def finish(child,*,release=False,expected=0):
    try:
        out,err=child.communicate(b'release\n' if release else b'',timeout=30)
        assert child.returncode==expected,err.decode(errors='replace')
        return out
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)
        for stream in (child.stdin,child.stdout,child.stderr):
            if stream is not None and not stream.closed:stream.close()
        child._handle.Close()

def blocked(tmp_path,monkeypatch,*,failure='corrupt'):
    paths,store,spec,service,source=fixture(tmp_path);service.install(spec,source);new=replace(spec,revision='revision-2')
    rename=Ownership.rename;actors=[];calls=[];armed=True
    def observed(owner,handle,target):
        nonlocal armed
        native=owner.api.SetFileInformationByHandle
        def call(h,kind,data,size):
            result=native(h,kind,data,size);error=C.get_last_error()
            calls.append(dict(target=str(target),kind=kind,result=int(result),last_error=error));C.set_last_error(error);return result
        owner.api.SetFileInformationByHandle=call
        try:rename(owner,handle,target)
        finally:owner.api.SetFileInformationByHandle=native
        if target==store.location(new) and armed:
            armed=False # only the first attempted install is faulted; explicit reinstall is a control
            if failure=='corrupt':(target/'model/weights.bin').write_bytes(b'xyz')
            actor=launch(paths,'hold',target/'model');actors.append(actor)
            observation=json.loads(actor.stdout.readline())
            assert observation['ready'] and observation['token']==token()
    monkeypatch.setattr(Ownership,'rename',observed)
    if failure=='cancelled':service._smoke=lambda *a:(_ for _ in ()).throw(ModelError('cancelled','Controlled cancellation.'))
    try:
        with pytest.raises(ModelError) as refused:service.install(new,source)
        assert refused.value.code=='lifecycle_failed'
        assert refused.value.retained_resources==service._retained[spec.model_id]
        assert any(r['kind']==22 and not r['result'] and r['last_error']==5 for r in calls)
        txn=next(o for o in service._retained[spec.model_id] if isinstance(o,Installation))
        assert txn.original_error.code==failure and txn.rollback_error.code=='storage_refused'
        assert txn.handle in txn.owner.handles and txn.marker in txn.owner.handles
        assert txn.key(txn.owner.info(txn.handle,directory=True))==txn.identity
        assert service.spec(spec.model_id)==spec and service.snapshot(spec.model_id).state==ModelState.IN_USE
        assert (store.location(spec)/'model/weights.bin').read_bytes()==b'abc'
        receipt=dict(token=token(),native_calls=calls,identity=txn.identity,original=txn.original_error.code,secondary=str(txn.rollback_error),retained=len(service._retained[spec.model_id]))
        (tmp_path/'blocked-receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    except BaseException:
        for actor in actors:finish(actor,release=True)
        # Preserve any failed transaction; never pretend the test failed cleanly.
        raise
    return paths,store,spec,new,service,source,txn,actors

@pytest.mark.parametrize('failure',['corrupt','cancelled'])
def test_real_windows5_retains_objects_reservation_and_explicitly_retires(tmp_path,monkeypatch,failure):
    paths,store,spec,new,service,source,txn,actors=blocked(tmp_path,monkeypatch,failure=failure)
    try:
        child=launch(paths,'probe',paths.root,source);probe=json.loads(finish(child));assert probe==dict(state='in_use',remove='in_use',install='in_use')
        with pytest.raises(ModelError):service.remove(spec.model_id)
        with pytest.raises(ModelError):service.install(new,source)
        with pytest.raises(ModelError):service.retire_pending(spec.model_id)
        assert txn.handle in txn.owner.handles and txn.marker is not None
    finally:
        for actor in actors:finish(actor,release=True)
    results=service.retire_pending(spec.model_id)
    assert results[0]==txn.directory and txn.finished and not txn.owner.handles and not txn.owner.parents.handles
    assert not pending_path(store,new).exists() and not store.location(new).exists()
    assert (txn.directory/'model/weights.bin').read_bytes()==(b'xyz' if failure=='corrupt' else b'abc')
    service._smoke=lambda *a:None
    assert service.snapshot(spec.model_id).state==ModelState.READY
    service.install(new,source);assert service.snapshot(spec.model_id).state==ModelState.READY
    service.shutdown()

def test_shutdown_failure_keeps_capabilities_and_can_be_explicitly_finished(tmp_path,monkeypatch):
    paths,store,spec,new,service,source,txn,actors=blocked(tmp_path,monkeypatch)
    try:
        with pytest.raises(ModelError) as error:service.shutdown()
        assert error.value.retained_resources and txn.marker is not None
        with pytest.raises(ModelError) as refused:
            with service.acquire(spec.model_id):pass
        assert refused.value.retained_resources
    finally:
        for actor in actors:finish(actor,release=True)
    service.shutdown();service.shutdown()
    assert txn.finished and not service._retained and not pending_path(store,new).exists()
    fresh=ModelService((spec,),store,runtime_fingerprint='runtime',smoke=lambda *a:None)
    assert fresh.snapshot(spec.model_id).state==ModelState.READY

def test_native_close_failure_does_not_discard_retirement_records(tmp_path,monkeypatch):
    paths,store,spec,new,service,source,txn,actors=blocked(tmp_path,monkeypatch)
    for actor in actors:finish(actor,release=True)
    close=txn.owner.api.CloseHandle
    def failed(h):
        if h==txn.handle:C.set_last_error(5);return 0
        return close(h)
    txn.owner.api.CloseHandle=failed
    with pytest.raises(ModelError):service.retire_pending(spec.model_id)
    assert txn in service._retained[spec.model_id] and txn.handle in txn.owner.handles
    assert txn.key(txn.owner.info(txn.handle,directory=True))==txn.identity
    txn.owner.api.CloseHandle=close;service.retire_pending(spec.model_id)
    assert txn.finished and not service._retained

def test_pending_marker_close_failure_retains_exact_handle_until_confirmation(tmp_path,monkeypatch):
    paths,store,spec,new,service,source,txn,actors=blocked(tmp_path,monkeypatch)
    for actor in actors:finish(actor,release=True)
    close=txn.owner.api.CloseHandle;marker=txn.marker
    def failed(h):
        if h==marker:C.set_last_error(5);return 0
        return close(h)
    txn.owner.api.CloseHandle=failed
    with pytest.raises(ModelError):service.retire_pending(spec.model_id)
    assert txn.marker_pending and txn.marker==marker and marker in txn.owner.handles
    assert txn.key(txn.owner.info(marker,links=0))==txn.marker_identity
    txn.owner.api.CloseHandle=close;service.retire_pending(spec.model_id)
    assert txn.finished and not pending_path(store,new).exists()

def test_failed_native_guard_close_remains_inside_install_controller(tmp_path,monkeypatch):
    paths,store,spec,service,source=fixture(tmp_path);service.install(spec,source)
    new=replace(spec,revision='revision-2');original=Installation.close_guard;controllers=[]
    def fail(transaction):
        if transaction.where()==os.path.normcase(str(transaction.target)) and transaction.guard is not None:
            controllers.append(transaction)
            raise ModelError('lifecycle_failed','Injected native guard cleanup failure.',retained_resources=(transaction.guard,))
        return original(transaction)
    monkeypatch.setattr(Installation,'close_guard',fail)
    with pytest.raises(ModelError):service.install(new,source)
    txn=controllers[0]
    assert service._retained[spec.model_id][0] is txn
    assert txn.guard.locks.handles and txn.handle in txn.owner.handles
    monkeypatch.setattr(Installation,'close_guard',original);service.retire_pending(spec.model_id)
    assert txn.finished and not txn.guard and not store.location(new).exists()
    assert service.snapshot(spec.model_id).state==ModelState.READY

def test_quarantine_path_collision_never_replaces_an_unknown_object(tmp_path,monkeypatch):
    paths,store,spec,new,service,source,txn,actors=blocked(tmp_path,monkeypatch)
    for actor in actors:finish(actor,release=True)
    # The test actor owns a separate native object at the intended quarantine
    # name; production must not overwrite or remove it by that pathname.
    txn.directory.mkdir();(txn.directory/'sentinel').write_bytes(b'untouched')
    with Ownership() as actor:
        handle=actor.open(txn.directory,0x10080,3,3,directory=True)
        with pytest.raises(ModelError):service.retire_pending(spec.model_id)
        assert (txn.directory/'sentinel').read_bytes()==b'untouched'
        assert txn.handle in txn.owner.handles and txn.marker is not None
        moved=paths.downloads/'actor-owned-evidence';actor.rename(handle,moved)
    service.retire_pending(spec.model_id)
    assert (moved/'sentinel').read_bytes()==b'untouched' and txn.finished

def test_identity_mismatch_keeps_authority_until_explicit_correct_object_retirement(tmp_path,monkeypatch):
    paths,store,spec,new,service,source,txn,actors=blocked(tmp_path,monkeypatch)
    for actor in actors:finish(actor,release=True)
    original=txn.identity;txn.identity=(0,0,0)
    with pytest.raises(ModelError):service.retire_pending(spec.model_id)
    assert txn in service._retained[spec.model_id] and txn.marker is not None
    txn.identity=original;service.retire_pending(spec.model_id)
    assert txn.finished

def test_reservation_close_failure_retains_last_owner_and_healthy_registry(tmp_path,monkeypatch):
    paths,store,spec,service,source=fixture(tmp_path);service.install(spec,source)
    new=replace(spec,revision='revision-2');reserve=store.reserve;holders=[]
    def observed(s):
        owner=reserve(s);holders.append(owner);native=owner.api.CloseHandle;h=owner.handles[0]
        def close(handle):
            if handle==h:C.set_last_error(5);return 0
            return native(handle)
        owner.api.CloseHandle=close;owner._test_restore=native;return owner
    monkeypatch.setattr(store,'reserve',observed)
    with pytest.raises(ModelError):service.install(new,source)
    owner=holders[0];assert owner in service._retained[spec.model_id] and owner.handles
    assert service.spec(spec.model_id)==spec and (store.location(spec)/'model/weights.bin').read_bytes()==b'abc'
    owner.api.CloseHandle=owner._test_restore;service.retire_pending(spec.model_id)
    monkeypatch.setattr(store,'reserve',reserve)
    assert service.snapshot(spec.model_id).state==ModelState.READY

def test_shutdown_refuses_active_lease_and_post_shutdown_use(tmp_path):
    _,store,spec,service,source=fixture(tmp_path);service.install(spec,source)
    with service.acquire(spec.model_id):
        with pytest.raises(ModelError) as refused:service.shutdown()
        assert refused.value.code=='in_use'
    service.shutdown()
    with pytest.raises(ModelError) as refused:service.snapshot(spec.model_id)
    assert refused.value.code=='lifecycle_failed'

def test_unknown_pending_marker_is_negative_only_and_never_removed(tmp_path):
    paths,store,spec,service,source=fixture(tmp_path);service.install(spec,source)
    marker=pending_path(store,spec);marker.write_bytes(b'untrusted content')
    assert service.snapshot(spec.model_id).state==ModelState.MISSING
    with pytest.raises(ModelError) as refused:service.remove(spec.model_id)
    assert refused.value.code=='incomplete' and marker.read_bytes()==b'untrusted content'
    service.shutdown();assert marker.read_bytes()==b'untrusted content'

@pytest.mark.parametrize('operation',['verify','mutation'])
def test_unreadable_pending_namespace_is_truthful_storage_failure(tmp_path,monkeypatch,operation):
    paths,store,spec,service,source=fixture(tmp_path);service.install(spec,source)
    method='lstat' if operation=='verify' else 'iterdir';original=getattr(Path,method)
    refused=pending_path(store,spec) if operation=='verify' else paths.manifests
    def deny(path,*args,**kwargs):
        if path==refused:raise PermissionError(5,'Injected inspection refusal.')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,method,deny)
    with pytest.raises(ModelError) as error:
        if operation=='verify':service.snapshot(spec.model_id)
        else:service.remove(spec.model_id)
    assert error.value.code=='storage_refused' and not error.value.retained_resources
    assert not service._retained and not service._active and not service._mutating
    monkeypatch.setattr(Path,method,original)
    assert service.snapshot(spec.model_id).state==ModelState.READY
    service.shutdown()

@pytest.mark.parametrize('mode',['crash-after-activation','crash-retained'])
def test_real_process_death_orphan_is_not_promoted_and_healthy_revision_survives(tmp_path,mode):
    paths,store,spec,service,source=fixture(tmp_path)
    child=launch(paths,mode,paths.root,source);out=finish(child,expected=23);assert out
    new=replace(spec,revision='revision-2')
    assert pending_path(store,new).exists() and (store.location(new)/'model/weights.bin').read_bytes()==b'abc'
    assert service.snapshot(spec.model_id).state==ModelState.READY # OS reservation released on death
    other=ModelService((new,),store,runtime_fingerprint='runtime',smoke=lambda *a:None)
    assert other.snapshot(spec.model_id).state==ModelState.MISSING
    with pytest.raises(ModelError) as mutate:other.remove(spec.model_id)
    assert mutate.value.code=='incomplete'
    assert pending_path(store,new).exists() # no adoption/automatic cleanup
    # The process changed no DACL; ordinary portable relocation still works.
    original=tmp_path/'Portable Модели';target=tmp_path/'Relocated Юникод'
    assert original.resolve().is_relative_to(tmp_path.resolve()) and target.resolve().is_relative_to(tmp_path.resolve())
    original.rename(target)
    moved=PortableModelStore(ProfilePaths.for_test(target/'Data/v2'))
    fresh=ModelService((new,),moved,runtime_fingerprint='runtime',smoke=lambda *a:None)
    assert fresh.snapshot(spec.model_id).state==ModelState.MISSING
