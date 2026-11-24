from types import SimpleNamespace as NS
import hashlib
import pytest
from mediaforge.infrastructure.models import dub_worker
from mediaforge.infrastructure.models.dub_runtime import ENTRY,ENTRY_SHA256,DELEGATE_SHA256

def test_exact_c3_entry_and_delegate_pins():
    assert hashlib.sha256(ENTRY.read_bytes()).hexdigest()==ENTRY_SHA256
    assert hashlib.sha256(ENTRY.with_name('worker.py').read_bytes()).hexdigest()==DELEGATE_SHA256

@pytest.mark.parametrize('version,product,expected',[((10,0,26100),1,'11'),((10,0,19045),1,'10'),((10,0,26100),3,'2025Server')])
def test_truthful_native_version_mapping(version,product,expected):
    assert dub_worker.native_facts(NS(platform_version=version,product_type=product),host='win32',width=8)['release']==expected

@pytest.mark.parametrize('version,product,host,width',[(None,1,'win32',8),((10,0,26100),0,'win32',8),((10,1,0),1,'win32',8),((4,0,1),1,'win32',8),((10,0,26100),1,'linux',8),((10,0,26100),1,'win32',4)])
def test_unsupported_native_facts_fail_closed(version,product,host,width):
    with pytest.raises(ValueError,match='native_platform_unavailable'):
        dub_worker.native_facts(NS(platform_version=version,product_type=product),host=host,width=width)

def test_delegate_tamper_executes_no_unverified_bytes(tmp_path,monkeypatch):
    entry=tmp_path/'dub_worker.py';entry.write_bytes(b'not executed')
    marker=tmp_path/'sentinel';marker.write_bytes(b'unchanged')
    (tmp_path/'worker.py').write_text('raise RuntimeError("UNVERIFIED EXECUTION")',encoding='utf-8')
    monkeypatch.setattr(dub_worker,'__file__',str(entry))
    with pytest.raises(ValueError,match='runtime_integrity'):dub_worker.verified_delegate()
    assert marker.read_bytes()==b'unchanged'
