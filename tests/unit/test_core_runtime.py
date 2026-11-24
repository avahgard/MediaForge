import hashlib
from pathlib import Path
from types import SimpleNamespace as NS
import pytest

from mediaforge.infrastructure.models import core_runtime


def fixture(tmp_path):
    root=tmp_path/'Portable core';(root/'Core/Python').mkdir(parents=True);(root/'external').mkdir()
    source=root/'Core/Python/known.dll';source.write_bytes(b'harmless unexecuted inventory fixture')
    raw=(hashlib.sha256(source.read_bytes()).hexdigest()+'\t'+str(source.stat().st_size)+'\tCore/Python/known.dll\n').encode()
    (root/'CoreManifest.tsv').write_bytes(raw)
    return root,hashlib.sha256(raw).hexdigest(),source


@pytest.mark.native
def test_core_guard_keeps_same_file_objects_read_only_for_entire_lifetime(tmp_path):
    root,digest,source=fixture(tmp_path)
    guard=core_runtime.CoreRuntime(root,digest)
    try:
        assert guard.native_ids and guard.locks.handles
        with pytest.raises(OSError):source.write_bytes(b'replacement')
        with pytest.raises(OSError):source.rename(source.with_name('renamed.dll'))
    finally:guard.close()
    assert source.read_bytes()==b'harmless unexecuted inventory fixture'


@pytest.mark.native
def test_unlisted_base_native_is_not_enrolled_by_portable_factory(tmp_path,monkeypatch):
    root,digest,source=fixture(tmp_path);guard=core_runtime.CoreRuntime(root,digest);closed=[]
    def factory(runtime):
        return NS(allowed=dict(runtime.native_ids,unlisted_object='untrusted.dll'),close=lambda:closed.append(True))
    monkeypatch.setattr(core_runtime,'NativeGate',factory)
    try:
        with pytest.raises(RuntimeError,match='Unlisted private Python TCB'):guard.native(None)
        assert closed
    finally:guard.close()


@pytest.mark.native
@pytest.mark.parametrize('extra',['file','directory'])
def test_unknown_core_members_refuse_without_adoption(tmp_path,extra):
    root,digest,_=fixture(tmp_path)
    if extra=='file':(root/'Core/extra.txt').write_bytes(b'unknown')
    else:(root/'Core/extra-dir').mkdir()
    with pytest.raises(RuntimeError,match='Unlisted portable core'):
        core_runtime.CoreRuntime(root,digest)


@pytest.mark.native
def test_core_guard_refuses_same_size_modified_member(tmp_path):
    root,digest,source=fixture(tmp_path)
    source.write_bytes(b'x'*source.stat().st_size)
    with pytest.raises(RuntimeError,match='Portable core hash differs'):
        core_runtime.CoreRuntime(root,digest)
