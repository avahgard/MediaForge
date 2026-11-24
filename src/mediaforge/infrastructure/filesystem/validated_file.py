"""Windows exact-object publication, retaining one read/delete owner through commit.

The read-only validation handle must close before acquiring DELETE access. That
handoff is untrusted: compare the frozen receipt AGAIN through the acquired handle.
The acquired handle denies writes/deletes and FileRenameInfo consumes that handle,
never the mutable staging name. Replacement uses a handle in the existing TxF.
https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle
"""
import ctypes
from ctypes import wintypes as W
import hashlib
import os
from pathlib import Path
import stat
from mediaforge.features.media.export import ExportError,OutputFileIdentity


class _Rename(ctypes.Structure):
    _fields_=[('replace',W.BOOLEAN),('root',W.HANDLE),('length',W.DWORD),('name',W.WCHAR*1)]


class ValidatedFile:
    def __init__(self,staged,target,expected,transaction=None,budget=None):
        self.staged=Path(staged);self.target=Path(target);self.expected=expected
        self.transaction=transaction;self.budget=budget;self.lease=None
        self.handle=None;self.duplicate=None;self.fd=None;self.parents=None;self.prepared=False
        if os.name!='nt':raise ExportError('publication_unavailable','Protected video publication requires Windows file handles.')
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        self.api.CreateFileW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,ctypes.c_void_p,W.DWORD,W.DWORD,W.HANDLE]
        self.api.CreateFileW.restype=W.HANDLE
        if transaction is not None:
            self.api.CreateFileTransactedW.argtypes=self.api.CreateFileW.argtypes+[W.HANDLE,ctypes.c_void_p,ctypes.c_void_p]
            self.api.CreateFileTransactedW.restype=W.HANDLE
        self.api.GetCurrentProcess.restype=W.HANDLE
        self.api.DuplicateHandle.argtypes=[W.HANDLE,W.HANDLE,W.HANDLE,ctypes.POINTER(W.HANDLE),W.DWORD,W.BOOL,W.DWORD]
        self.api.DuplicateHandle.restype=W.BOOL
        self.api.CloseHandle.argtypes=[W.HANDLE];self.api.CloseHandle.restype=W.BOOL
        self.api.SetFileInformationByHandle.argtypes=[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD]
        self.api.SetFileInformationByHandle.restype=W.BOOL

    def prepare(self,control):
        from mediaforge.infrastructure.filesystem.image_output import ReadLocks,_Tag
        import msvcrt
        control.check()
        # Bound new ownership/path/hash scratch under the existing CPU ledger.
        if len(self.staged.parts)>128 or len(str(self.staged).encode('utf-8'))>2048:
            raise ExportError('publication_unavailable','Protected publication path exceeds bounded handle metadata; use a shorter output folder.')
        if self.budget:self.lease=self.budget.reserve_peak(4*1024**2)
        self.parents=ReadLocks();self.parents.parents(self.staged.parent)
        args=(str(self.staged),0x80010000,1,None,3,0x00200000,None)  # READ|DELETE, share READ only; no-follow.
        handle=(self.api.CreateFileTransactedW(*args,self.transaction,None,None) if self.transaction
                else self.api.CreateFileW(*args))
        if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
        self.handle=handle
        self.api.GetFileInformationByHandleEx.argtypes=[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD]
        self.api.GetFileInformationByHandleEx.restype=W.BOOL
        tag=_Tag()
        if (not self.api.GetFileInformationByHandleEx(handle,9,ctypes.byref(tag),ctypes.sizeof(tag))
                or tag.attributes&(0x400|0x10)):
            raise ExportError('unsafe_staging','Publication handle is not an ordinary non-reparse file.')
        process=self.api.GetCurrentProcess();duplicate=W.HANDLE()
        if not self.api.DuplicateHandle(process,handle,process,ctypes.byref(duplicate),0,False,2):
            raise ctypes.WinError(ctypes.get_last_error())
        self.duplicate=duplicate.value
        self.fd=msvcrt.open_osfhandle(self.duplicate,os.O_RDONLY|os.O_BINARY);self.duplicate=None
        info=os.fstat(self.fd);digest=hashlib.sha256()
        if not stat.S_ISREG(info.st_mode):raise ExportError('unsafe_staging','Publication handle is not a regular file.')
        while block:=os.read(self.fd,1024*1024):control.check();digest.update(block)
        after=os.fstat(self.fd)
        values=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
        actual=OutputFileIdentity(self.expected.name,*values(after),digest.hexdigest())
        if values(info)!=values(after) or actual!=self.expected:
            raise ExportError('staging_changed','Validated staging changed during publication handoff. Nothing was published or replaced.')
        os.close(self.fd);self.fd=None
        control.check();self.prepared=True
        return self

    def rename(self,target,replace=False):
        if not self.prepared or self.handle is None or Path(target)!=self.target:
            raise ExportError('publication_unavailable','Validated file ownership is unavailable or mismatched.')
        if bool(replace)!=bool(self.transaction):
            raise ExportError('publication_unavailable','Replacement requires the existing owned NTFS transaction.')
        encoded=str(self.target).encode('utf-16-le')
        buffer=ctypes.create_string_buffer(max(ctypes.sizeof(_Rename),_Rename.name.offset+len(encoded)+2))
        value=_Rename.from_buffer(buffer);value.replace=replace;value.length=len(encoded)
        ctypes.memmove(ctypes.addressof(buffer)+_Rename.name.offset,encoded,len(encoded))
        if not self.api.SetFileInformationByHandle(self.handle,3,buffer,len(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        return self.target

    def close(self):
        if self.fd is not None:os.close(self.fd);self.fd=None
        for name in ('duplicate','handle'):
            handle=getattr(self,name)
            if handle is not None:
                if not self.api.CloseHandle(handle):raise ctypes.WinError(ctypes.get_last_error() or 6)
                setattr(self,name,None)
        if self.parents is not None:self.parents.close()
        if self.lease is not None:self.budget.release(self.lease);self.lease=None
        self.prepared=False
