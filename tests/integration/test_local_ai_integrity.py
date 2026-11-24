"""Native namespace/loader adversaries; tiny owned fixtures, no real-runtime edits."""
from pathlib import Path
import ctypes as C
import hashlib
import json
import os
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.application.contracts.models import ModelError,ModelMember,ModelSpec,ModelState
from mediaforge.application.services.models import ModelService
from mediaforge.features.media.processing_control import ProcessingControl
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.models.providers import WorkerRuntime
from mediaforge.infrastructure.models.store import PortableModelStore
from mediaforge.infrastructure.models.process import run_worker
from mediaforge.infrastructure.models.native_guard import NativeGate

pytestmark=[pytest.mark.native,pytest.mark.integration]


def benign_dll(dependency=None):
    """Own PE32+ fixture, no compiler/download: DllMain sets a private flag.

    Export marker() returns the flag. No imports except the optional second
    fixture's marker; no filesystem/network/process operations in machine code.
    """
    header=bytearray(512);header[:2]=b'MZ';struct.pack_into('<I',header,0x3c,0x80)
    header[0x80:0x84]=b'PE\0\0';struct.pack_into('<HHIIIHH',header,0x84,0x8664,3,0,0,0,240,0x2022)
    optional=bytearray(240)
    struct.pack_into('<H',optional,0,0x20b)
    struct.pack_into('<III',optional,4,512,1024,0)
    struct.pack_into('<IIQII',optional,16,0x1000,0x1000,0x180000000,4096,512)
    struct.pack_into('<HH',optional,40,6,0);struct.pack_into('<HH',optional,48,6,0)
    struct.pack_into('<II',optional,56,0x4000,512);struct.pack_into('<HH',optional,68,3,0x140)
    struct.pack_into('<QQQQII',optional,72,1048576,4096,1048576,4096,0,16)
    struct.pack_into('<II',optional,112,0x2000,0x80)
    struct.pack_into('<II',optional,152,0x2180,12)
    if dependency:
        struct.pack_into('<II',optional,120,0x2080,40)
        struct.pack_into('<II',optional,208,0x20d0,16)
    header[0x98:0x188]=optional
    for offset,name,rva,raw,flags in [(0x188,b'.text',0x1000,512,0x60000020),(0x1b0,b'.rdata',0x2000,1024,0x40000040),(0x1d8,b'.data',0x3000,1536,0xc0000040)]:
        struct.pack_into('<8sIIIIIIHHI',header,offset,name,512,rva,512,raw,0,0,0,0,flags)
    text=bytearray(512)
    text[:16]=b'\xc7\x05'+struct.pack('<i',0x3000-0x100a)+b'\x01\0\0\0\xb8\x01\0\0\0\xc3'
    text[0x20:0x27]=b'\x8b\x05'+struct.pack('<i',0x3000-0x1026)+b'\xc3'
    data=bytearray(512)
    struct.pack_into('<IIHHIIIIIII',data,0,0,0,0,0,0x2050,1,1,1,0x2040,0x2044,0x2048)
    struct.pack_into('<IIH',data,0x40,0x1020,0x2060,0)
    data[0x50:0x5c]=b'fixture.dll\0';data[0x60:0x67]=b'marker\0'
    if dependency:
        struct.pack_into('<IIIII',data,0x80,0x20c0,0,0,0x20b0,0x20d0)
        raw=dependency.encode('ascii')+b'\0';assert len(raw)<=16
        data[0xb0:0xb0+len(raw)]=raw
        struct.pack_into('<Q',data,0xc0,0x20e0);struct.pack_into('<Q',data,0xd0,0x20e0)
        data[0xe0:0xe9]=b'\0\0marker\0'
    struct.pack_into('<IIHH',data,0x180,0x1000,12,0,0)
    return bytes(header+text+data+bytearray(512))


def fixture(tmp_path,members=None):
    paths=ProfilePaths.for_test(tmp_path/'Portable Модель Space/Data/v2');paths.prepare()
    site=paths.root/'runtime/site';site.mkdir(parents=True)
    members=members or {'trusted.py':b'TRUSTED=True\n','pack/__init__.py':b''}
    for name,data in members.items():
        file=site/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(data)
    manifest=paths.manifests/'runtime.json'
    rows=[dict(name=n,size=len(v),sha256=hashlib.sha256(v).hexdigest()) for n,v in members.items()]
    manifest.write_text(json.dumps(rows,separators=(',',':')))
    return paths,WorkerRuntime(Path(sys._base_executable),site,manifest,hashlib.sha256(manifest.read_bytes()).hexdigest())


def control():return ProcessingControl(lambda:LocalAiError('cancelled','Native cancellation.'))


DRIVER="""import importlib.util,json,sys
s=importlib.util.spec_from_file_location('trusted_worker',sys.argv[1]);w=importlib.util.module_from_spec(s);s.loader.exec_module(w)
p,origins=w.install_runtime(sys.argv[2],sys.argv[3],sys.argv[4])
def audit(event,args):
 if event=='exec':p.check_exec(args[0])
sys.addaudithook(audit)
exec(sys.argv[5])
"""


def launch(paths,runtime,body,**options):
    with runtime.acquire(paths) as lease:
        return run_worker([sys._base_executable,'-I','-S','-B','-c',DRIVER,str(runtime.entry),str(runtime.site),str(runtime.manifest),runtime.manifest_sha256,body],
            paths=paths,environment=paths.child_environment(),control=options.pop('control',control()),runtime_guard=lease,**options)


def test_original_logging_insertion_nested_package_and_pyd_refuse(tmp_path):
    paths,runtime=fixture(tmp_path)
    def actor(pid):
        (runtime.site/'logging.py').write_bytes(b'raise AssertionError("UNAUTHORIZED LOGGING")\n')
        (runtime.site/'pack/inserted.py').write_bytes(b'raise AssertionError("UNAUTHORIZED NESTED")\n')
        (runtime.site/'native_inserted.pyd').write_bytes(benign_dll())
    body="""import logging,pack
from pathlib import Path
assert not Path(logging.__file__).is_relative_to(Path(sys.argv[2]))
blocked=[]
for name in ('pack.inserted','native_inserted'):
 try:__import__(name)
 except ModuleNotFoundError:blocked.append(name)
print(json.dumps(dict(ok=True,blocked=blocked,logging=logging.__file__,origins=origins)))
"""
    result=launch(paths,runtime,body,on_started=actor)
    assert result['blocked']==['pack.inserted','native_inserted']
    assert all('logging.py' not in x['path'] or 'runtime' not in x['path'] for x in result['origins'])


def test_listed_python_replace_and_directory_substitution_are_denied(tmp_path):
    paths,runtime=fixture(tmp_path)
    sentinel=tmp_path/'outside';sentinel.write_bytes(b'UNCHANGED')
    def actor(pid):
        for action in (lambda:(runtime.site/'trusted.py').write_bytes(b'EVIL=True'),
                       lambda:(runtime.site/'trusted.py').unlink(),
                       lambda:runtime.site.rename(runtime.site.with_name('replacement'))):
            with pytest.raises(PermissionError):action()
    result=launch(paths,runtime,"import trusted;assert trusted.TRUSTED;print(json.dumps({'ok':True,'path':trusted.__file__}))",on_started=actor)
    assert Path(result['path'])==runtime.site/'trusted.py' and sentinel.read_bytes()==b'UNCHANGED'


def test_native_directory_junction_and_hardlinked_member_refuse(tmp_path):
    paths,runtime=fixture(tmp_path)
    target=runtime.site.with_name('outside-runtime-target');runtime.site.rename(target)
    command=Path(os.environ['SYSTEMROOT'])/'System32/cmd.exe'
    p=subprocess.run([str(command),'/c','mklink','/J',str(runtime.site),str(target)],
        capture_output=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    assert p.returncode==0,p.stderr
    try:
        with pytest.raises(ModelError):
            with runtime.acquire(paths):raise AssertionError('Junction accepted')
        assert (target/'trusted.py').read_bytes()==b'TRUSTED=True\n'
    finally:os.rmdir(runtime.site)  # exact newly created junction only, not its target
    target.rename(runtime.site)
    outside=tmp_path/'outside-member-alias';os.link(runtime.site/'trusted.py',outside)
    with pytest.raises(ModelError):
        with runtime.acquire(paths):raise AssertionError('Hardlinked runtime member accepted')
    assert outside.read_bytes()==b'TRUSTED=True\n'


@pytest.mark.parametrize('dependency',[False,True])
def test_native_image_and_dependency_refused_before_entrypoint(tmp_path,monkeypatch,dependency):
    members={'allowed.dll':benign_dll('dep.dll' if dependency else None)} if dependency else None
    paths,runtime=fixture(tmp_path,members)
    observed=[];original=NativeGate._image
    def image(gate,process,file,base):
        # Observe the actual debuggee mapping while Windows has it stopped.
        if file:
            name=C.create_unicode_buffer(32768)
            assert gate.api.GetFinalPathNameByHandleW(file,name,len(name),0)
            if Path(name.value.removeprefix('\\\\?\\')).name in ('inserted.dll','dep.dll','allowed.dll'):
                read=gate.api.ReadProcessMemory;read.argtypes=[C.c_void_p,C.c_void_p,C.c_void_p,C.c_size_t,C.c_void_p];read.restype=C.c_int
                value=C.c_uint32();count=C.c_size_t()
                assert read(int(process._handle),base+0x3000,C.byref(value),4,C.byref(count)) and count.value==4
                observed.append((Path(name.value).name,value.value))
        return original(gate,process,file,base)
    monkeypatch.setattr(NativeGate,'_image',image)
    def actor(pid):(runtime.site/('dep.dll' if dependency else 'inserted.dll')).write_bytes(benign_dll())
    filename='allowed.dll' if dependency else 'inserted.dll'
    body=f"import ctypes;d=ctypes.WinDLL({str(runtime.site/filename)!r});print(json.dumps(dict(ok=True,flag=d.marker())))"
    with pytest.raises(LocalAiError) as error:launch(paths,runtime,body,on_started=actor)
    assert error.value.code=='runtime_integrity'
    assert observed and all(flag==0 for _,flag in observed)
    assert observed[-1][0] in ('inserted.dll','dep.dll')


def test_benign_native_fixture_positive_and_manifest_load(tmp_path):
    paths,runtime=fixture(tmp_path,{'allowed.dll':benign_dll()})
    # Independent ordinary loader proves this is a working DLL with executed
    # DllMain, not a malformed PE that would refuse regardless of protection.
    code=f"import ctypes,json;d=ctypes.WinDLL({str(runtime.site/'allowed.dll')!r});print(d.marker())"
    p=subprocess.run([sys._base_executable,'-I','-S','-B','-c',code],capture_output=True,text=True,timeout=10)
    assert p.returncode==0 and p.stdout.strip()=='1',p.stderr
    result=launch(paths,runtime,code.replace('print(d.marker())',"print(json.dumps(dict(ok=True,flag=d.marker())))"))
    assert result['flag']==1
    assert any(Path(r['path']).name=='allowed.dll' and r['authority']=='owned' for r in result['metrics']['native_images'])


@pytest.mark.parametrize('bad',[{},[{'name':'x.py','size':1,'sha256':'bad'}],[],[{'name':'../x.py','size':0,'sha256':'0'*64}]])
def test_malformed_inventory_refuses_and_releases(tmp_path,bad):
    paths,runtime=fixture(tmp_path)
    runtime.manifest.write_text(json.dumps(bad))
    runtime=WorkerRuntime(runtime.python,runtime.site,runtime.manifest,hashlib.sha256(runtime.manifest.read_bytes()).hexdigest())
    with pytest.raises(LocalAiError):
        with runtime.acquire(paths):pass
    runtime.site.rename(runtime.site.with_name('released'))


@pytest.mark.parametrize('mode',['import-failure','cancel','terminate'])
def test_failed_or_cancelled_worker_releases_and_restarts(tmp_path,mode):
    paths,runtime=fixture(tmp_path);c=control()
    body="import not_in_manifest" if mode=='import-failure' else "import time;time.sleep(30)"
    def started(pid):
        if mode=='cancel':c.cancel()
        if mode=='terminate':
            k=C.WinDLL('kernel32');k.OpenProcess.argtypes=[C.c_uint,C.c_int,C.c_uint];k.OpenProcess.restype=C.c_void_p
            h=k.OpenProcess(1,False,pid);assert h
            k.TerminateProcess.argtypes=[C.c_void_p,C.c_uint];k.CloseHandle.argtypes=[C.c_void_p]
            try:assert k.TerminateProcess(h,23)
            finally:assert k.CloseHandle(h)
    with pytest.raises(LocalAiError):launch(paths,runtime,body,control=c,on_started=started)
    assert not c.cleanup_failed
    assert launch(paths,runtime,"import trusted;print(json.dumps({'ok':True}))")['ok']
    moved=paths.root.with_name('Relocated Ю Space');paths.root.rename(moved);moved.rename(paths.root)


def test_missing_native_capability_refuses_before_spawn(tmp_path,monkeypatch):
    paths,runtime=fixture(tmp_path)
    import mediaforge.infrastructure.models.process as module
    def missing(*args):raise LocalAiError('native_protection_unavailable','Synthetic unavailable debug API')
    monkeypatch.setattr(module,'NativeGate',missing)
    with pytest.raises(LocalAiError,match='unavailable'):launch(paths,runtime,"raise AssertionError('must not run')")
    runtime.site.rename(runtime.site.with_name('released'))


def test_partial_native_handle_duplication_failure_releases_objects(tmp_path,monkeypatch):
    paths,runtime=fixture(tmp_path)
    import mediaforge.infrastructure.models.native_guard as module
    original=C.WinDLL;calls=[]
    def library(*args,**options):
        api=original(*args,**options)
        if args[0]=='kernel32':
            duplicate=api.DuplicateHandle
            # The fault wrapper receives the production signature attributes;
            # its real delegate still needs pointer-width-safe marshalling.
            duplicate.argtypes=[C.c_void_p,C.c_void_p,C.c_void_p,C.c_void_p,C.c_uint,C.c_int,C.c_uint]
            duplicate.restype=C.c_int
            def fail(*values):
                calls.append(True)
                if len(calls)==3:C.set_last_error(5);return False
                return duplicate(*values)
            api.DuplicateHandle=fail
        return api
    with runtime.acquire(paths) as lease:
        with monkeypatch.context() as patch:
            patch.setattr(module.C,'WinDLL',library)
            with pytest.raises(LocalAiError) as error:NativeGate(lease)
            assert error.value.code=='native_protection_unavailable' and not error.value.retained_resources
    assert len(calls)==3
    runtime.site.rename(runtime.site.with_name('released'))


def test_cross_process_reservation_reports_in_use_then_reauthenticates(tmp_path):
    paths,runtime=fixture(tmp_path)
    spec=ModelSpec('synthetic','one',(ModelMember('weights.bin',3,hashlib.sha256(b'abc').hexdigest()),),'runtime',True)
    store=PortableModelStore(paths);service=ModelService((spec,),store,runtime_fingerprint='runtime',smoke=lambda *a:None)
    folder=paths.models/'synthetic/one';folder.mkdir(parents=True);(folder/'weights.bin').write_bytes(b'abc')
    script="""import ctypes as C,hashlib,sys,os
from pathlib import Path
from mediaforge.application.contracts.models import ModelSpec,ModelMember
from mediaforge.application.services.models import ModelService
from mediaforge.infrastructure.models.store import PortableModelStore
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
k=C.WinDLL('kernel32');a=C.WinDLL('advapi32');k.GetCurrentProcess.restype=C.c_void_p
a.OpenProcessToken.argtypes=[C.c_void_p,C.c_uint,C.c_void_p];a.GetTokenInformation.argtypes=[C.c_void_p,C.c_uint,C.c_void_p,C.c_uint,C.c_void_p]
t=C.c_void_p();assert a.OpenProcessToken(k.GetCurrentProcess(),8,C.byref(t))
e=C.c_uint();n=C.c_uint();assert a.GetTokenInformation(t,20,C.byref(e),4,C.byref(n));assert e.value==0
k.CloseHandle.argtypes=[C.c_void_p];assert k.CloseHandle(t)
s=ModelSpec('synthetic','one',(ModelMember('weights.bin',3,hashlib.sha256(b'abc').hexdigest()),),'runtime',True)
m=ModelService((s,),PortableModelStore(ProfilePaths.for_test(Path(sys.argv[1]))),runtime_fingerprint='runtime',smoke=lambda *a:None)
with m.acquire('synthetic'):
 print('HELD ORDINARY',flush=True);sys.stdin.readline()
os._exit(23)
"""
    p=subprocess.Popen([sys.executable,'-u','-c',script,str(paths.root)],env=paths.child_environment(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert p.stdout.readline().strip()=='HELD ORDINARY'
        assert service.snapshot('synthetic').state==ModelState.IN_USE
        for action in (lambda:service.remove('synthetic'),lambda:service.acquire('synthetic').__enter__()):
            with pytest.raises(ModelError) as error:action()
            assert error.value.code=='in_use'
        assert (folder/'weights.bin').read_bytes()==b'abc'
    finally:
        p.stdin.write('\n');p.stdin.flush();p.communicate(timeout=10)
    assert p.returncode==23 and service.snapshot('synthetic').state==ModelState.READY
    (folder/'weights.bin').write_bytes(b'xyz')
    assert service.snapshot('synthetic').state==ModelState.CORRUPT
    (folder/'weights.bin').unlink()
    assert service.snapshot('synthetic').state==ModelState.CORRUPT


def test_permission_fault_is_not_in_use_or_corruption(tmp_path,monkeypatch):
    paths,_=fixture(tmp_path)
    spec=ModelSpec('synthetic','one',(ModelMember('weights.bin',3,hashlib.sha256(b'abc').hexdigest()),),'runtime',True)
    store=PortableModelStore(paths);service=ModelService((spec,),store,runtime_fingerprint='runtime',smoke=lambda *a:None)
    folder=paths.models/'synthetic/one';folder.mkdir(parents=True);(folder/'weights.bin').write_bytes(b'abc')
    def deny(*args):raise ModelError('storage_refused','Windows access denied 5, no sharing evidence')
    monkeypatch.setattr(store,'reserve',deny)
    with pytest.raises(ModelError) as error:service.snapshot('synthetic')
    assert error.value.code=='storage_refused'


@pytest.mark.parametrize('api_name',['GetFileInformationByHandle','GetFileInformationByHandleEx'])
def test_failed_native_identity_query_is_storage_failure(tmp_path,monkeypatch,api_name):
    paths,_=fixture(tmp_path)
    spec=ModelSpec('synthetic','one',(ModelMember('weights.bin',3,hashlib.sha256(b'abc').hexdigest()),),'runtime',True)
    store=PortableModelStore(paths);service=ModelService((spec,),store,runtime_fingerprint='runtime',smoke=lambda *a:None)
    folder=paths.models/'synthetic/one';folder.mkdir(parents=True);(folder/'weights.bin').write_bytes(b'abc')
    import mediaforge.infrastructure.models.store as module
    original=module.Guard
    def guard(path):
        result=original(path)
        def fail(*args):C.set_last_error(5);return False
        setattr(result.locks.api,api_name,fail)
        return result
    monkeypatch.setattr(module,'Guard',guard)
    with pytest.raises(ModelError) as error:service.snapshot('synthetic')
    assert error.value.code=='storage_refused' and not error.value.retained_resources
    monkeypatch.setattr(module,'Guard',original)
    assert service.snapshot('synthetic').state==ModelState.READY


def test_unlisted_bytecode_and_direct_loader_cannot_execute(tmp_path):
    import importlib._bootstrap_external as bytecode
    paths,runtime=fixture(tmp_path)
    def actor(pid):
        cache=runtime.site/'__pycache__';cache.mkdir()
        source=runtime.site/'trusted.py'
        code=compile('raise AssertionError("UNVERIFIED BYTECODE")',str(source),'exec')
        (cache/'trusted.cpython-312.pyc').write_bytes(bytecode._code_to_timestamp_pyc(code,int(source.stat().st_mtime),source.stat().st_size))
        (runtime.site/'direct.py').write_bytes(b'raise AssertionError("UNLISTED DIRECT CODE")')
    body="""import trusted,importlib.util
assert trusted.TRUSTED
s=importlib.util.spec_from_file_location('direct',sys.argv[2]+'/direct.py')
m=importlib.util.module_from_spec(s)
try:s.loader.exec_module(m)
except PermissionError:pass
else:raise AssertionError('Direct unverified source was executed')
print(json.dumps(dict(ok=True)))
"""
    assert launch(paths,runtime,body,on_started=actor)['ok']


def test_owner_process_death_releases_debuggee_and_runtime_for_restart(tmp_path):
    paths,runtime=fixture(tmp_path)
    supervisor="""import os,sys
from pathlib import Path
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.models.providers import WorkerRuntime
from mediaforge.infrastructure.models.process import run_worker
from mediaforge.features.media.processing_control import ProcessingControl
from mediaforge.application.contracts.local_ai import LocalAiError
p=ProfilePaths.for_test(Path(sys.argv[1]))
r=WorkerRuntime(Path(sys._base_executable),Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4])
def started(pid):
 print(pid,flush=True);sys.stdin.readline();os._exit(23)
with r.acquire(p) as lease:
 run_worker([sys._base_executable,'-I','-S','-B','-c','import time;time.sleep(30)'],paths=p,environment=p.child_environment(),control=ProcessingControl(lambda:LocalAiError('cancelled','cancel')),runtime_guard=lease,on_started=started)
"""
    p=subprocess.Popen([sys.executable,'-u','-c',supervisor,str(paths.root),str(runtime.site),str(runtime.manifest),runtime.manifest_sha256],env=paths.child_environment(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    k=C.WinDLL('kernel32');k.OpenProcess.argtypes=[C.c_uint,C.c_int,C.c_uint];k.OpenProcess.restype=C.c_void_p
    k.WaitForSingleObject.argtypes=[C.c_void_p,C.c_uint];k.CloseHandle.argtypes=[C.c_void_p]
    handle=None
    try:
        line=p.stdout.readline().strip();assert line.isdigit(),p.stderr.read()
        handle=k.OpenProcess(0x100000,False,int(line));assert handle
        p.stdin.write('\n');p.stdin.flush();p.communicate(timeout=10)
        assert p.returncode==23 and k.WaitForSingleObject(handle,5000)==0
    finally:
        if handle:assert k.CloseHandle(handle)
        if p.poll() is None:p.kill();p.communicate(timeout=10)
    assert launch(paths,runtime,"import trusted;print(json.dumps(dict(ok=True)))")['ok']
    moved=paths.root.with_name('After Crash Ю Space');paths.root.rename(moved);moved.rename(paths.root)


def test_actual_permission_denial_is_not_ownership_or_corruption(tmp_path):
    paths,_=fixture(tmp_path)
    spec=ModelSpec('synthetic','one',(ModelMember('weights.bin',3,hashlib.sha256(b'abc').hexdigest()),),'runtime',True)
    store=PortableModelStore(paths);service=ModelService((spec,),store,runtime_fingerprint='runtime',smoke=lambda *a:None)
    folder=paths.models/'synthetic/one';folder.mkdir(parents=True);(folder/'weights.bin').write_bytes(b'abc')
    lock=paths.manifests/'.synthetic.lock';lock.write_bytes(b'')
    a=C.WinDLL('advapi32',use_last_error=True);k=C.WinDLL('kernel32',use_last_error=True)
    a.GetFileSecurityW.argtypes=[C.c_wchar_p,C.c_uint,C.c_void_p,C.c_uint,C.c_void_p]
    a.SetFileSecurityW.argtypes=[C.c_wchar_p,C.c_uint,C.c_void_p]
    a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[C.c_wchar_p,C.c_uint,C.c_void_p,C.c_void_p]
    k.LocalFree.argtypes=[C.c_void_p]
    needed=C.c_uint();a.GetFileSecurityW(str(lock),4,None,0,C.byref(needed));assert needed.value
    original=C.create_string_buffer(needed.value);assert a.GetFileSecurityW(str(lock),4,original,len(original),C.byref(needed))
    deny=C.c_void_p();assert a.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:(D;;GR;;;WD)(A;;FA;;;OW)',1,C.byref(deny),None)
    try:
        assert a.SetFileSecurityW(str(lock),4,deny)
        with pytest.raises(ModelError) as error:service.snapshot('synthetic')
        assert error.value.code=='storage_refused'
    finally:
        assert a.SetFileSecurityW(str(lock),4,original),'Synthetic DACL restoration failed; original record retained in test frame'
        k.LocalFree(deny)
    assert service.snapshot('synthetic').state==ModelState.READY


def test_concurrent_ordinary_actor_cannot_add_importable_code(tmp_path):
    paths,runtime=fixture(tmp_path)
    actor_code="import pathlib,sys,time;time.sleep(.2);pathlib.Path(sys.argv[1]).write_text('raise AssertionError(\"UNAUTHORIZED\")')"
    actors=[]
    def started(pid):
        actors.append(subprocess.Popen([sys._base_executable,'-I','-S','-B','-c',actor_code,str(runtime.site/'pack/active.py')],env=paths.child_environment()))
    body="""import time,importlib
from pathlib import Path
deadline=time.monotonic()+2;attempts=0
while time.monotonic()<deadline:
 try:importlib.import_module('pack.active')
 except ModuleNotFoundError:attempts+=1
 time.sleep(.01)
assert Path(sys.argv[2]+'/pack/active.py').is_file() and attempts
print(json.dumps(dict(ok=True,attempts=attempts)))
"""
    try:assert launch(paths,runtime,body,on_started=started)['attempts']>0
    finally:
        for actor in actors:
            try:actor.wait(timeout=5)
            except subprocess.TimeoutExpired:actor.kill();actor.wait(timeout=5)
            assert actor.returncode==0


def test_preexisting_writable_mapping_refuses_runtime_ownership(tmp_path):
    import mmap
    paths,runtime=fixture(tmp_path,{'allowed.dll':benign_dll()})
    file=runtime.site/'allowed.dll'
    with file.open('r+b') as stream:actor=mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_WRITE)
    try:
        with pytest.raises(ModelError) as error:
            with runtime.acquire(paths):raise AssertionError('Writable section was accepted')
        assert error.value.code=='storage_refused' and not error.value.retained_resources
    finally:actor.close()
    assert launch(paths,runtime,"print(json.dumps(dict(ok=True)))")['ok']


def test_hot_image_section_relocates_without_content_or_permission_changes(tmp_path):
    paths,runtime=fixture(tmp_path,{'allowed.dll':benign_dll()})
    file=runtime.site/'allowed.dll';before=(file.read_bytes(),file.stat().st_mtime_ns,file.stat().st_mode)
    # A real ordinary load leaves Windows' image section cache warm after exit.
    p=subprocess.run([sys._base_executable,'-I','-S','-B','-c',
        'import ctypes,sys;d=ctypes.WinDLL(sys.argv[1]);assert d.marker()==1',str(file)],capture_output=True,timeout=10)
    assert p.returncode==0,p.stderr
    moved=paths.root.with_name('Hot Relocated Ю Space');paths.root.rename(moved)
    paths=ProfilePaths.for_test(moved)
    runtime=WorkerRuntime(runtime.python,moved/'runtime/site',moved/'manifests/runtime.json',runtime.manifest_sha256)
    file=runtime.site/'allowed.dll'
    for _ in range(2):
        result=launch(paths,runtime,f'import ctypes;d=ctypes.WinDLL({str(file)!r});print(json.dumps(dict(ok=True,flag=d.marker())))')
        assert result['flag']==1
        assert any(Path(row['path'])==file and row['authority']=='owned' for row in result['metrics']['native_images'])
    assert (file.read_bytes(),file.stat().st_mtime_ns,file.stat().st_mode)==before
    moved.rename(moved.with_name('Released After Relocation'))


def test_image_gate_stays_attached_with_normal_heap(tmp_path):
    paths,runtime=fixture(tmp_path)
    body="import ctypes,os;print(json.dumps(dict(ok=True,debugger=bool(ctypes.WinDLL('kernel32').IsDebuggerPresent()),heap=os.environ.get('_NO_DEBUG_HEAP'))))"
    result=launch(paths,runtime,body)
    assert result['debugger'] and result['heap']=='1' and result['metrics']['debug_events']>0


def test_member_writer_without_model_reservation_is_storage_failure(tmp_path):
    paths,_=fixture(tmp_path)
    spec=ModelSpec('synthetic','one',(ModelMember('weights.bin',3,hashlib.sha256(b'abc').hexdigest()),),'runtime',True)
    store=PortableModelStore(paths);service=ModelService((spec,),store,runtime_fingerprint='runtime',smoke=lambda *a:None)
    folder=paths.models/'synthetic/one';folder.mkdir(parents=True);file=folder/'weights.bin';file.write_bytes(b'abc')
    k=C.WinDLL('kernel32',use_last_error=True)
    k.CreateFileW.argtypes=[C.c_wchar_p,C.c_uint,C.c_uint,C.c_void_p,C.c_uint,C.c_uint,C.c_void_p];k.CreateFileW.restype=C.c_void_p
    k.CloseHandle.argtypes=[C.c_void_p]
    writer=k.CreateFileW(str(file),0xc0000000,3,None,3,0,None);assert writer!=C.c_void_p(-1).value
    try:
        with pytest.raises(ModelError) as error:service.snapshot('synthetic')
        assert error.value.code=='storage_refused'
    finally:assert k.CloseHandle(writer)
    assert service.snapshot('synthetic').state==ModelState.READY


@pytest.mark.parametrize('failure',['continue','image-close','retained-close'])
def test_native_admission_failure_cleanup_is_truthful(tmp_path,monkeypatch,failure):
    paths,runtime=fixture(tmp_path);c=control();captured=[]
    original=NativeGate._image
    def image(gate,process,file,base):
        result=original(gate,process,file,base)
        if not captured:
            captured.append(gate)
            name='ContinueDebugEvent' if failure=='continue' else 'CloseHandle'
            native=getattr(gate.api,name);gate._test_restore=(name,native)
            calls=[]
            def refuse(*args):
                if (failure=='continue' or args[0]==file) and (not calls or failure=='retained-close'):
                    calls.append(True);C.set_last_error(5);return False
                return native(*args)
            setattr(gate.api,name,refuse)
        return result
    monkeypatch.setattr(NativeGate,'_image',image)
    try:
        with pytest.raises(LocalAiError) as error:launch(paths,runtime,"raise AssertionError('must not execute')",control=c)
        assert captured
        if failure=='retained-close':
            assert c.cleanup_failed and error.value.retained_resources
            # The outer authentication context has unwound. Retained native
            # ownership must still hold the exact runtime objects/ancestors,
            # rather than retaining only stale FileID records.
            with pytest.raises(PermissionError):(runtime.site/'trusted.py').write_bytes(b'REPLACED')
        else:
            assert error.value.code=='native_protection_unavailable'
            assert not c.cleanup_failed and not error.value.retained_resources
    finally:
        for gate in captured:
            name,native=gate._test_restore;setattr(gate.api,name,native)
            gate.close()
    runtime.site.rename(runtime.site.with_name('released'))
