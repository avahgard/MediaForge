"""Harmless isolated 3-byte model/OS-ownership actor, never a provider worker."""
from pathlib import Path
from dataclasses import replace
import hashlib,json,os,sys
import ctypes as C
from ctypes import wintypes as W
from mediaforge.application.contracts.models import ModelError,ModelMember,ModelSpec
from mediaforge.application.services.models import ModelService
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.models.store import PortableModelStore
from mediaforge.infrastructure.models.owned import Ownership

def token():
    """Actual native token observation, not a command-line integrity claim."""
    k=C.WinDLL('kernel32',use_last_error=True);a=C.WinDLL('advapi32',use_last_error=True)
    k.GetCurrentProcess.restype=W.HANDLE;k.CloseHandle.argtypes=[W.HANDLE];k.LocalFree.argtypes=[C.c_void_p]
    a.OpenProcessToken.argtypes=[W.HANDLE,W.DWORD,C.POINTER(W.HANDLE)];a.OpenProcessToken.restype=W.BOOL
    a.GetTokenInformation.argtypes=[W.HANDLE,C.c_int,C.c_void_p,W.DWORD,C.POINTER(W.DWORD)];a.GetTokenInformation.restype=W.BOOL
    a.ConvertSidToStringSidW.argtypes=[C.c_void_p,C.POINTER(W.LPWSTR)];a.ConvertSidToStringSidW.restype=W.BOOL
    handle=W.HANDLE();assert a.OpenProcessToken(k.GetCurrentProcess(),8,C.byref(handle))
    try:
        needed=W.DWORD();a.GetTokenInformation(handle,25,None,0,C.byref(needed));buffer=C.create_string_buffer(needed.value)
        assert a.GetTokenInformation(handle,25,buffer,len(buffer),C.byref(needed))
        text=W.LPWSTR();assert a.ConvertSidToStringSidW(C.cast(buffer,C.POINTER(C.c_void_p))[0],C.byref(text))
        try:sid=text.value
        finally:k.LocalFree(text)
        elevated=W.DWORD();assert a.GetTokenInformation(handle,20,C.byref(elevated),4,C.byref(needed))
        return dict(integrity=sid,elevated=bool(elevated.value))
    finally:k.CloseHandle(handle)

def setup(root,revision='revision-1'):
    paths=ProfilePaths.for_test(Path(root));store=PortableModelStore(paths)
    spec=ModelSpec('synthetic',revision,(ModelMember('model/weights.bin',3,hashlib.sha256(b'abc').hexdigest()),),'runtime',True)
    return paths,store,spec,ModelService((spec,),store,runtime_fingerprint='runtime',smoke=lambda *a:None)

def emit(value):print(json.dumps(value,ensure_ascii=True),flush=True)
if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='hold':
        with ReadLocks() as locks:
            h=locks.add(Path(sys.argv[2]),True);emit(dict(ready=True,handle=int(h),access=0x80,share=3,token=token()));sys.stdin.readline()
    elif mode=='probe':
        paths,store,spec,service=setup(sys.argv[2]);out=dict(state=service.snapshot(spec.model_id).state.value)
        for name,operation in [('remove',lambda:service.remove(spec.model_id)),
                ('install',lambda:service.install(replace(spec,revision='revision-2'),Path(sys.argv[3])))]:
            try:operation();out[name]='UNEXPECTED_SUCCESS'
            except ModelError as error:out[name]=error.code
        emit(out)
    elif mode in ('crash-after-activation','crash-retained'):
        paths,store,spec,service=setup(sys.argv[2]);source=Path(sys.argv[3]);service.install(spec,source)
        new=replace(spec,revision='revision-2');original=Ownership.rename;actor=ReadLocks()
        def rename(owner,handle,target):
            original(owner,handle,target)
            if target==store.location(new):
                if mode=='crash-after-activation':emit(dict(activated=True));os._exit(23)
                actor.add(target/'model',True)
        Ownership.rename=rename
        if mode=='crash-retained':
            service._smoke=lambda *a:(_ for _ in ()).throw(ModelError('cancelled','Controlled cancelled native smoke.'))
        try:service.install(new,source)
        except ModelError as error:emit(dict(code=error.code,retained=len(error.retained_resources),registered=service.spec(spec.model_id).revision));os._exit(23)
        raise RuntimeError('Crash witness did not reach its intended boundary.')
