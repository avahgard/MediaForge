"""Dub muxing into one owned seekable file object, then exact-handle publish.

FFmpeg receives a duplicated stdio descriptor, never a writable staging pathname.
The file stays delete-pending/unlinked until full independent validation. This
keeps normal seekable MP4/MOV muxing and closes an exists/open pathname gap.
"""
from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
import hashlib
import msvcrt
import os
from uuid import uuid4

from mediaforge.features.media.export import ExportError, OutputFileIdentity, OutputIdentity
from mediaforge.infrastructure.filesystem.profile import local_path
from mediaforge.infrastructure.filesystem.speech_directory import create_directory
from mediaforge.infrastructure.models.native_guard import identity
from mediaforge.infrastructure.models.owned import Ownership


class FileIdInfo(C.Structure):
    _fields_=[('volume',C.c_uint64),('file_id',C.c_ubyte*16)]


class DubOutput:
    def __init__(self, destination, control):
        self.destination=local_path(destination);self.control=control
        self.owner=Ownership();self.directory_owner=None;self.directory=None
        self.handle=None;self.key=None;self.directory_key=None;self.seal=None
        self.published=False;self.closed=False
        api=self.owner.api
        api.GetCurrentProcess.argtypes=[];api.GetCurrentProcess.restype=W.HANDLE
        api.DuplicateHandle.argtypes=[W.HANDLE,W.HANDLE,W.HANDLE,C.POINTER(W.HANDLE),W.DWORD,W.BOOL,W.DWORD]
        api.DuplicateHandle.restype=W.BOOL
        api.SetFilePointerEx.argtypes=[W.HANDLE,C.c_int64,C.POINTER(C.c_int64),W.DWORD]
        api.SetFilePointerEx.restype=W.BOOL
        api.ReadFile.argtypes=[W.HANDLE,C.c_void_p,W.DWORD,C.POINTER(W.DWORD),C.c_void_p]
        api.ReadFile.restype=W.BOOL
        api.GetFileInformationByHandleEx.argtypes=[W.HANDLE,C.c_int,C.c_void_p,W.DWORD]
        api.GetFileInformationByHandleEx.restype=W.BOOL

    def __enter__(self):
        try:
            self.control.check();self.owner.parents.parents(self.destination.parent)
            if self.destination.exists():
                raise ExportError('destination_exists','Output already exists; choose a new name.')
            self.directory=self.destination.parent/('.mediaforge-dub-'+uuid4().hex)
            self.directory_owner,directory_handle=create_directory(self.directory)
            self.directory_key=identity(self.directory_owner.api,directory_handle)[0]
            self.handle=self.owner.open(self.directory/('partial-'+uuid4().hex),0xC0010000,0,1)
            self.key=identity(self.owner.api,self.handle)[0]
            self.owner.pending(self.handle,True)
            self.owner.info(self.handle,links=0)
            return self
        except BaseException:
            self.close();raise

    def rewind(self):
        self.owner.require(self.owner.api.SetFilePointerEx(self.handle,0,None,0))

    @contextmanager
    def descriptor(self):
        """Popen duplicates this fd for one child; the launch copy closes here."""
        # Called by an owned process factory INSIDE control.starting(). Its
        # admission lock is deliberately nonreentrant; never reacquire it here.
        # This method only duplicates/rewinds the already owned exact object.
        self.rewind()
        api=self.owner.api;current=api.GetCurrentProcess();duplicate=W.HANDLE()
        self.owner.require(api.DuplicateHandle(current,self.handle,current,C.byref(duplicate),0,False,2))
        try:
            descriptor=msvcrt.open_osfhandle(duplicate.value,os.O_BINARY|os.O_RDWR)
        except BaseException:
            self.owner.require(api.CloseHandle(duplicate));raise
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    def digest(self):
        self.control.check();self.rewind()
        info=self.owner.info(self.handle,links=0)
        expected=(info.high<<32)|info.low
        if not 0<expected<=66*1024**3 or identity(self.owner.api,self.handle)[0]!=self.key:
            raise ExportError('validation_failed','Dub output object identity/size is invalid.')
        result=hashlib.sha256();buffer=C.create_string_buffer(1024*1024);total=0
        while True:
            self.control.check();count=W.DWORD()
            self.owner.require(self.owner.api.ReadFile(self.handle,buffer,len(buffer),C.byref(count),None))
            if not count.value:break
            total+=count.value
            if total>expected:raise ExportError('validation_failed','Dub output grew while validating.')
            result.update(buffer.raw[:count.value])
        if total!=expected:raise ExportError('validation_failed','Dub output is incomplete.')
        modified=((info.written.dwHighDateTime<<32)|info.written.dwLowDateTime)-116444736000000000
        # CPython 3.12 stat uses the 64-bit volume serial and FILE_ID_128.
        # NativeGate's separate legacy FileID tuple remains unchanged.
        extended=FileIdInfo()
        self.owner.require(self.owner.api.GetFileInformationByHandleEx(self.handle,18,C.byref(extended),C.sizeof(extended)))
        return OutputFileIdentity(self.destination.name,extended.volume,int.from_bytes(bytes(extended.file_id),'little'),
                                  expected,modified*100,result.hexdigest())

    def validated(self):
        self.owner.require(self.owner.api.FlushFileBuffers(self.handle))
        self.seal=self.digest()
        return OutputIdentity(str(self.destination),(self.seal,))

    def publish(self):
        if self.seal is None:
            raise ExportError('validation_failed','Independent Dub validation is required before publication.')
        if self.digest()!=self.seal:
            raise ExportError('validation_failed','Validated Dub output changed before publication.')
        def commit():
            self.owner.info(self.handle,links=0)
            if identity(self.owner.api,self.handle)[0]!=self.key:
                raise ExportError('validation_failed','Publication object identity changed.')
            self.owner.pending(self.handle,False)
            try:
                self.owner.rename(self.handle,self.destination)
            except BaseException as error:
                native=C.get_last_error()
                self.owner.pending(self.handle,True)
                raise ExportError('destination_exists' if native in (80,183) else 'publication_failed',
                                  'Dub output was not replaced or published; choose a writable new destination.') from error
            self.published=True
            return self.destination
        return self.control.publish(commit)

    def close(self):
        if self.closed:return
        pin=Ownership();retirement=Ownership()
        try:
            # Kill/reap children BEFORE this owner is closed by the executor.
            self.owner.close();self.handle=None
            if self.directory_owner is not None:
                pin.parents.parents(self.directory.parent)
                handle=pin.open(self.directory,0x80,7,3,directory=True)
                if identity(pin.api,handle)[0]!=self.directory_key:
                    raise ExportError('lifecycle_failed','Output staging identity changed.')
                self.directory_owner.close()
                retirement.parents.parents(self.directory.parent)
                handle=retirement.open(self.directory,0x10080,3,3,directory=True)
                if identity(retirement.api,handle)[0]!=self.directory_key or any(self.directory.iterdir()):
                    raise ExportError('lifecycle_failed','Unknown output staging objects; cleanup refused.')
                retirement.pending(handle,True)
            retirement.close();pin.close();self.closed=True
        except BaseException as error:
            self.control.retain(self)
            self._retained=(pin,retirement,self.directory_owner,self.owner)
            raise ExportError('lifecycle_failed','Dub output ownership is unresolved; processing stays disabled.',
                              retained_resources=(self,)) from error

    def __exit__(self,*args):
        self.close()
