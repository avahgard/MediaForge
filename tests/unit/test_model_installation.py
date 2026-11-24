"""Tiny native transaction fixtures; no model weights or network provisioning."""
from dataclasses import replace
import hashlib
import io
import threading
import zipfile

import pytest

from mediaforge.application.contracts.models import ModelError,ModelMember,ModelSpec,ModelState
from mediaforge.application.services.models import ModelService
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.models.store import PortableModelStore
from mediaforge.infrastructure.models.owned import Ownership

pytestmark=pytest.mark.native


def fixture(tmp_path,smoke=lambda *a:None):
    paths=ProfilePaths.for_test(tmp_path/'Portable Модели/Data/v2');store=PortableModelStore(paths)
    spec=ModelSpec('synthetic','revision-1',(ModelMember('model/weights.bin',3,hashlib.sha256(b'abc').hexdigest()),),'runtime',True)
    service=ModelService((spec,),store,runtime_fingerprint='runtime',smoke=smoke)
    source=tmp_path/'source';(source/'model').mkdir(parents=True);(source/'model/weights.bin').write_bytes(b'abc')
    return paths,store,spec,service,source


def test_install_activate_remove_corrupt_reinstall(tmp_path):
    paths,store,spec,service,source=fixture(tmp_path)
    service.install(spec,source)
    assert service.snapshot(spec.model_id).state==ModelState.READY
    with pytest.raises(ModelError,match='Immutable revision'):service.install(spec,source)
    file=store.location(spec)/'model/weights.bin';file.write_bytes(b'xyz')
    assert service.snapshot(spec.model_id).state==ModelState.CORRUPT
    retired=service.remove(spec.model_id)
    assert (retired/'model/weights.bin').read_bytes()==b'xyz'
    assert service.snapshot(spec.model_id).state==ModelState.MISSING
    service.install(spec,source)
    assert file.read_bytes()==b'abc'


def test_mutation_excludes_leases_and_other_instances(tmp_path):
    _,store,spec,service,source=fixture(tmp_path);service.install(spec,source)
    other=ModelService((spec,),store,runtime_fingerprint='runtime',smoke=lambda *a:None)
    with service.acquire(spec.model_id):
        with pytest.raises(ModelError):service.remove(spec.model_id)
        with pytest.raises(ModelError):
            with other.acquire(spec.model_id):pass
        with pytest.raises(ModelError):other.remove(spec.model_id)
    with service.mutation(spec.model_id):
        assert service.snapshot(spec.model_id).state==ModelState.VERIFYING
        with pytest.raises(ModelError):
            with service.acquire(spec.model_id):pass


@pytest.mark.parametrize('failure',['native-smoke','activate','substitution'])
def test_failed_new_revision_preserves_old_and_rolls_back(tmp_path,monkeypatch,failure):
    _,store,spec,service,source=fixture(tmp_path);service.install(spec,source)
    new=replace(spec,revision='revision-2')
    original=Ownership.rename
    def rename(owner,handle,target):
        if target==store.location(new) and failure=='activate':raise ModelError('injected','Injected activation refusal')
        original(owner,handle,target)
        if target==store.location(new) and failure=='substitution':(target/'model/weights.bin').write_bytes(b'xyz')
    monkeypatch.setattr(Ownership,'rename',rename)
    if failure=='native-smoke':service._smoke=lambda *a:(_ for _ in ()).throw(ModelError('load_failed','Injected native load failure'))
    with pytest.raises(ModelError):service.install(new,source)
    service._smoke=lambda *a:None
    assert service.spec(spec.model_id)==spec
    assert service.snapshot(spec.model_id).state==ModelState.READY
    assert not store.location(new).exists()
    assert (store.location(spec)/'model/weights.bin').read_bytes()==b'abc'


@pytest.mark.parametrize('bad',['extra','missing','traversal','symlink','digest','partial'])
def test_bad_archives_are_not_installed(tmp_path,bad):
    _,store,spec,service,_=fixture(tmp_path)
    path=tmp_path/'source.zip'
    if bad=='partial':path.write_bytes(b'not a zip')
    else:
        with zipfile.ZipFile(path,'w') as archive:
            if bad!='missing':archive.writestr('model/weights.bin',b'abc')
            if bad=='extra':archive.writestr('extra.bin',b'x')
            if bad=='traversal':archive.writestr('../outside',b'x')
            if bad=='symlink':
                row=zipfile.ZipInfo('link');row.external_attr=0o120777<<16;archive.writestr(row,b'outside')
    with pytest.raises(ModelError):
        service.install(spec,path,archive_size=path.stat().st_size,archive_sha256='0'*64 if bad=='digest' else hashlib.sha256(path.read_bytes()).hexdigest())
    assert not store.location(spec).exists()
    assert service.snapshot(spec.model_id).state==ModelState.MISSING


def test_staged_hardlink_race_cannot_modify_sentinel(tmp_path,monkeypatch):
    _,store,spec,service,source=fixture(tmp_path)
    sentinel=tmp_path/'outside';sentinel.write_bytes(b'unchanged')
    original=Ownership.open
    def open_stage(owner,path,*args,**kwargs):
        if path.name.startswith('.member-'):
            import os
            os.link(sentinel,path)
        return original(owner,path,*args,**kwargs)
    monkeypatch.setattr(Ownership,'open',open_stage)
    with pytest.raises(ModelError):service.install(spec,source)
    assert sentinel.read_bytes()==b'unchanged'
    assert not store.location(spec).exists()


def test_native_cleanup_failure_is_retained(tmp_path,monkeypatch):
    _,store,spec,service,source=fixture(tmp_path)
    service.install(spec,source)
    owner=object()
    def reserve(s):raise ModelError('lifecycle_failed','Injected ownership failure',retained_resources=(owner,))
    monkeypatch.setattr(store,'reserve',reserve)
    with pytest.raises(ModelError):service.remove(spec.model_id)
    assert service.snapshot(spec.model_id).state==ModelState.IN_USE


def test_registration_cannot_override_an_active_or_unqualified_revision(tmp_path):
    _,_,spec,service,source=fixture(tmp_path);service.install(spec,source)
    with service.acquire(spec.model_id):
        with pytest.raises(ModelError):service.register(replace(spec,revision='new'))
    with pytest.raises(ModelError):service.register(replace(spec,approved=False))
    with pytest.raises(ModelError):service.register(replace(spec,runtime_fingerprint='unreviewed'))
    service.register(replace(spec,revision='new'))
    assert service.snapshot(spec.model_id).state==ModelState.MISSING


def test_native_reservation_acquisition_failure_is_typed(tmp_path,monkeypatch):
    _,store,spec,service,source=fixture(tmp_path);service.install(spec,source)
    import mediaforge.infrastructure.models.owned as native
    def failed(*args):raise OSError('Injected native acquisition failure')
    monkeypatch.setattr(native,'ModelMutex',failed)
    with pytest.raises(ModelError) as error:
        with service.acquire(spec.model_id):pass
    assert error.value.code=='storage_refused'
    assert not error.value.retained_resources
