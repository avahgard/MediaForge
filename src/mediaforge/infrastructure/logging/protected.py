"""Copy-on-write portable logs: never mutate a published file or its DACL."""
import ctypes as C
from ctypes import wintypes as W
import msvcrt
import os
from uuid import uuid4

from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.filesystem.profile import local_path


class LogOwnershipError(RuntimeError):
    def __init__(self,owner):
        self.retained_resources=(owner,)
        super().__init__('Log handle cleanup unresolved; owner retained, close is not clean.')


class Info(C.Structure):
    _fields_=[('attributes',W.DWORD),('created',W.FILETIME),('accessed',W.FILETIME),
              ('written',W.FILETIME),('volume',W.DWORD),('high',W.DWORD),('low',W.DWORD),
              ('links',W.DWORD),('id_high',W.DWORD),('id_low',W.DWORD)]


class ProtectedLogs:
    limit=4*1024*1024

    def __init__(self,folder,*,create=True):
        self.folder=local_path(folder)
        self.parents=ReadLocks()
        self.handles=[]
        self.stages=[]
        self.lock=None
        self.api=C.WinDLL('kernel32',use_last_error=True)
        self.api.CreateFileW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,C.c_void_p,W.DWORD,W.DWORD,W.HANDLE]
        self.api.CreateFileW.restype=W.HANDLE
        self.api.GetFileInformationByHandle.argtypes=[W.HANDLE,C.POINTER(Info)]
        self.api.GetFileInformationByHandle.restype=W.BOOL
        self.api.SetFileInformationByHandle.argtypes=[W.HANDLE,C.c_int,C.c_void_p,W.DWORD]
        self.api.SetFileInformationByHandle.restype=W.BOOL
        self.api.CloseHandle.argtypes=[W.HANDLE];self.api.CloseHandle.restype=W.BOOL
        try:
            self.parents.parents(self.folder)
            if create:
                lock=self.api.CreateFileW(str(self.folder/'.application.lock'),0x80000000,0,None,4,0x00200000,None)
                if lock==C.c_void_p(-1).value:self.fail()
                self.lock=lock
                self.info(lock)
            self.refresh()
        except BaseException:
            self.close()
            raise

    def fail(self):
        raise ValueError('Portable logging refused: unsafe alias, changed object, permissions or size; '
                         'use ordinary single-link logs in the portable folder. No fallback is used.')

    def info(self,handle,*,links=1):
        value=Info()
        if not self.api.GetFileInformationByHandle(handle,C.byref(value)):
            raise C.WinError(C.get_last_error())
        if value.attributes&(0x400|0x10) or value.links!=links:self.fail()
        return value

    def pending(self,handle,value):
        flag=W.BOOLEAN(value)
        if not self.api.SetFileInformationByHandle(handle,4,C.byref(flag),C.sizeof(flag)):
            raise C.WinError(C.get_last_error())

    def refresh(self):
        self.release_files()
        try:
            for suffix in ('','.1','.2'):
                path=self.folder/('application.log'+suffix)
                handle=self.api.CreateFileW(str(path),0x80000000,5,None,3,0x00200000,None)
                if handle==C.c_void_p(-1).value:
                    if C.get_last_error()==2:
                        self.handles.append(None);continue
                    self.fail()
                self.handles.append(handle)
                self.info(handle)
        except BaseException:
            self.release_files()
            raise

    def read(self,index):
        handle=self.handles[index]
        if handle is None:return b''
        info=self.info(handle)
        if (info.high<<32|info.low)>self.limit:self.fail()
        # DuplicateHandle preserves the validated object's share-denying lease.
        duplicate=W.HANDLE()
        self.api.GetCurrentProcess.restype=W.HANDLE
        self.api.DuplicateHandle.argtypes=[W.HANDLE,W.HANDLE,W.HANDLE,C.POINTER(W.HANDLE),W.DWORD,W.BOOL,W.DWORD]
        self.api.DuplicateHandle.restype=W.BOOL
        process=self.api.GetCurrentProcess()
        if not self.api.DuplicateHandle(process,handle,process,C.byref(duplicate),0,False,2):
            raise C.WinError(C.get_last_error())
        resource=[duplicate.value,None,None]
        self.stages.append(resource)
        try:
            fd=msvcrt.open_osfhandle(duplicate.value,os.O_RDONLY|os.O_BINARY)
            resource[2]=fd
            stream=os.fdopen(fd,'rb');resource[1]=stream
            stream.seek(0);return stream.read(self.limit+1)
        finally:self.close_stage(resource)

    def publish(self,index,data):
        if len(data)>self.limit:self.fail()
        # No existing path is ever opened with write access. A fresh object is
        # made delete-pending BEFORE its first byte and link count is rechecked.
        stage=self.folder/('.log-'+uuid4().hex+'.tmp')
        handle=self.api.CreateFileW(str(stage),0xC0010000,0,None,1,0x00200000,None)
        if handle==C.c_void_p(-1).value:raise C.WinError(C.get_last_error())
        stream=None
        fd=None
        resource=[handle,None,None]
        self.stages.append(resource)
        try:
            self.info(handle)
            self.pending(handle,True);self.info(handle,links=0)
            fd=msvcrt.open_osfhandle(handle,os.O_RDWR|os.O_BINARY)
            resource[2]=fd
            stream=os.fdopen(fd,'w+b')
            resource[1]=stream
            stream.write(data);stream.flush();os.fsync(stream.fileno())
            self.pending(handle,False)
            name=str(self.folder/('application.log'+('','.1','.2')[index])).encode('utf-16-le')
            class Rename(C.Structure):
                _fields_=[('flags',W.DWORD),('root',W.HANDLE),('length',W.DWORD),('name',C.c_byte*(len(name)+2))]
            # REPLACE_IF_EXISTS | POSIX_SEMANTICS: old read snapshots survive
            # replacement, but are never modified. No path-based second open.
            rename=Rename();rename.flags=3;rename.length=len(name)
            C.memmove(C.addressof(rename)+Rename.name.offset,name,len(name))
            if not self.api.SetFileInformationByHandle(handle,22,C.byref(rename),C.sizeof(rename)):
                raise C.WinError(C.get_last_error())
        except BaseException:
            self.pending(handle,True)
            raise
        finally:
            self.close_stage(resource)

    def close_stage(self,resource):
        handle,stream,fd=resource
        try:
            if stream is not None:stream.close()
            elif fd is not None:os.close(fd)
            elif not self.api.CloseHandle(handle):raise LogOwnershipError(self)
        except BaseException as error:
            raise LogOwnershipError(self) from error
        self.stages.remove(resource)

    def release_files(self):
        failed=[]
        for handle in self.handles:
            if handle is not None and not self.api.CloseHandle(handle):failed.append(handle)
        self.handles=failed
        if failed:raise LogOwnershipError(self)

    def close(self):
        for stage in self.stages[:]:self.close_stage(stage)
        self.release_files()
        if self.lock is not None:
            if not self.api.CloseHandle(self.lock):raise LogOwnershipError(self)
            self.lock=None
        self.parents.close()

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
