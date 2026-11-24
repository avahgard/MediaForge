"""Inventory/lease contracts with tiny synthetic assets, NOT real model inference."""
from dataclasses import replace
import hashlib
from pathlib import Path
import shutil
import threading

import pytest

from mediaforge.application.contracts.models import ModelMember,ModelSpec,ModelError,ModelState
from mediaforge.application.services.models import ModelService
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.models.store import PortableModelStore
from mediaforge.infrastructure.models.catalog import CANDIDATES


def setup(tmp_path,*,smoke=lambda *args:None):
    paths=ProfilePaths.for_test(tmp_path/'Portable Корень');store=PortableModelStore(paths)
    member=ModelMember('model/config.bin',9,hashlib.sha256(b'synthetic').hexdigest())
    spec=ModelSpec('test-model','revision-1',(member,),'test-runtime',True)
    service=ModelService((spec,),store,runtime_fingerprint='test-runtime',smoke=smoke)
    return paths,store,spec,service


def install_fixture(store,spec):
    path=store.location(spec)/'model/config.bin';path.parent.mkdir(parents=True);path.write_bytes(b'synthetic');return path


def test_candidates_never_self_approve_or_download(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'isolated');service=ModelService(CANDIDATES,PortableModelStore(paths),runtime_fingerprint='unqualified')
    assert len(service.list())==3 and all(s.state==ModelState.MISSING for s in service.list())
    assert all(not spec.approved and not spec.members for spec in CANDIDATES)
    assert not paths.root.exists()


@pytest.mark.native
def test_states_and_lifetime_lease_tamper_and_relocation(tmp_path):
    paths,store,spec,service=setup(tmp_path)
    assert service.snapshot(spec.model_id).state==ModelState.MISSING
    path=install_fixture(store,spec)
    assert service.snapshot(spec.model_id).state==ModelState.READY
    with service.acquire(spec.model_id) as guard:
        assert guard.path==store.location(spec)
        assert service.snapshot(spec.model_id).state==ModelState.IN_USE
        with pytest.raises(ModelError):service.assert_mutable(spec.model_id)
        with pytest.raises(PermissionError):path.write_bytes(b'TAMPERED!')
        with pytest.raises(PermissionError):store.location(spec).rename(paths.models/'moved')
    service.assert_mutable(spec.model_id)
    path.write_bytes(b'TAMPERED!')
    assert service.snapshot(spec.model_id).state==ModelState.CORRUPT
    path.write_bytes(b'synthetic')
    relocated=tmp_path/'relocated'
    assert paths.root.resolve().is_relative_to(tmp_path.resolve()) and relocated.resolve().is_relative_to(tmp_path.resolve())
    shutil.copytree(paths.root,relocated)
    other=ModelService((spec,),PortableModelStore(ProfilePaths.for_test(relocated)),runtime_fingerprint='test-runtime',smoke=lambda *a:None)
    assert other.snapshot(spec.model_id).state==ModelState.READY


@pytest.mark.parametrize('case',['extra','partial','directory','unqualified','runtime','smoke-missing','load-failure'])
@pytest.mark.native
def test_refusals(tmp_path,case):
    paths,store,spec,service=setup(tmp_path);path=install_fixture(store,spec)
    expected=ModelState.CORRUPT
    if case=='extra':(path.parent/'unexpected').write_bytes(b'x')
    elif case=='partial':path.unlink()
    elif case=='directory':(path.parent/'unexpected-dir').mkdir()
    elif case=='unqualified':
        service=ModelService((replace(spec,approved=False),),store,runtime_fingerprint='test-runtime',smoke=lambda *a:None);expected=ModelState.UNQUALIFIED
    elif case=='runtime':
        service=ModelService((spec,),store,runtime_fingerprint='wrong-runtime',smoke=lambda *a:None);expected=ModelState.INCOMPATIBLE
    elif case=='smoke-missing':
        service=ModelService((spec,),store,runtime_fingerprint='test-runtime');expected=ModelState.UNQUALIFIED
    else:
        service=ModelService((spec,),store,runtime_fingerprint='test-runtime',smoke=lambda *a:(_ for _ in ()).throw(ValueError('load failed')));expected=ModelState.INCOMPATIBLE
    assert service.snapshot(spec.model_id).state==expected


@pytest.mark.parametrize('name',['../model','/model','C:/model','model\\bad','NUL.bin','a/../b','a//b'])
def test_invalid_member_paths(name):
    with pytest.raises(ValueError):ModelMember(name,1,'a'*64)


@pytest.mark.native
def test_verifying_is_visible_and_concurrent_mutation_refuses(tmp_path):
    observations=[];holder={}
    def smoke(spec,guard):observations.append(holder['service'].snapshot(spec.model_id).state)
    _,store,spec,service=setup(tmp_path,smoke=smoke);holder['service']=service;install_fixture(store,spec)
    with service.acquire(spec.model_id):
        errors=[]
        def mutation():
            try:service.assert_mutable(spec.model_id)
            except ModelError as error:errors.append(error.code)
        thread=threading.Thread(target=mutation);thread.start();thread.join(2)
        assert not thread.is_alive() and errors==['in_use']
    assert observations==[ModelState.VERIFYING]


def test_cleanup_failure_is_retained_not_ready(tmp_path):
    class Guard:
        path=tmp_path
        def close(self):raise OSError('failed close')
    class Store:
        def exists(self,s):return True
        def verify(self,s):return Guard()
    _,_,spec,_=setup(tmp_path)
    service=ModelService((spec,),Store(),runtime_fingerprint='test-runtime',smoke=lambda *a:None)
    with pytest.raises(ModelError) as error:
        with service.acquire(spec.model_id):pass
    assert error.value.retained_resources
    assert service.snapshot(spec.model_id).state==ModelState.IN_USE
    with pytest.raises(ModelError):service.assert_mutable(spec.model_id)


@pytest.mark.native
def test_verification_visible_from_other_thread_and_failure_unwinds(tmp_path):
    entered=threading.Event();release=threading.Event();errors=[]
    def smoke(*args):
        entered.set();assert release.wait(2);raise ValueError('synthetic failed load')
    _,store,spec,service=setup(tmp_path,smoke=smoke);path=install_fixture(store,spec)
    def worker():
        try:
            with service.acquire(spec.model_id):pass
        except ModelError as error:errors.append(error.code)
    thread=threading.Thread(target=worker);thread.start();assert entered.wait(2)
    try:
        assert service.snapshot(spec.model_id).state==ModelState.VERIFYING
        with pytest.raises(ModelError):service.assert_mutable(spec.model_id)
    finally:release.set();thread.join(2)
    assert not thread.is_alive() and errors==['incompatible']
    path.write_bytes(b'synthetic')
    service.assert_mutable(spec.model_id)


def test_verification_cleanup_owner_from_store_is_preserved(tmp_path):
    owner=object()
    class Store:
        def exists(self,s):return True
        def verify(self,s):raise ModelError('lifecycle_failed','retained',retained_resources=(owner,))
    _,_,spec,_=setup(tmp_path)
    service=ModelService((spec,),Store(),runtime_fingerprint='test-runtime',smoke=lambda *a:None)
    with pytest.raises(ModelError):
        with service.acquire(spec.model_id):pass
    assert service.snapshot(spec.model_id).state==ModelState.IN_USE
    with pytest.raises(ModelError):service.assert_mutable(spec.model_id)


@pytest.mark.native
def test_external_model_hardlink_refuses_and_preserves_bytes(tmp_path):
    import os
    _,store,spec,service=setup(tmp_path);path=install_fixture(store,spec)
    outside=tmp_path/'outside.bin';os.link(path,outside)
    assert service.snapshot(spec.model_id).state==ModelState.CORRUPT
    assert outside.read_bytes()==b'synthetic'
    outside.unlink()
    assert service.snapshot(spec.model_id).state==ModelState.READY


@pytest.mark.integration
def test_optional_models_imports_are_lazy(run_python):
    result=run_python('''
import sys
from mediaforge.application.services.models import ModelService
from mediaforge.infrastructure.models.catalog import CANDIDATES
assert len(CANDIDATES)==3
assert not any(n.startswith(('PySide6','faster_whisper','ctranslate2','piper','sentencepiece','onnxruntime','huggingface_hub','av')) for n in sys.modules)
''')
    assert result.returncode==0,result.stderr
