"""NTFS namespace publication without releasing validated-file sharing locks.

CreateHardLinkW publishes one locked file. For a numbered set, TxF creates a NEW
isolated directory and only the frozen links; CommitTransaction exposes the set.
We never enumerate a mutable public directory and mistake that for TxF isolation.
Docs: https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-createhardlinkw
https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-createdirectorytransactedw
https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-createhardlinktransactedw
Unsupported NTFS/TxF fails closed. No rename/copy/replacement fallback.
"""
import ctypes
from ctypes import wintypes as W
from mediaforge.infrastructure.filesystem.confirmed_output import ConfirmedOutput
from mediaforge.features.media.export import ExportError


def _failure():
    code=ctypes.get_last_error()
    raise ExportError('destination_exists' if code in (80,183) else 'publication_unavailable',
        f'Protected NTFS publication refused (Windows {code}); no fallback was attempted.')


def link_file(api,source,target):
    api.CreateHardLinkW.argtypes=[W.LPCWSTR,W.LPCWSTR,ctypes.c_void_p]
    api.CreateHardLinkW.restype=W.BOOL
    if not api.CreateHardLinkW(str(target),str(source),None):_failure()
    return target


class DirectoryPublication(ConfirmedOutput):
    def __init__(self,target):
        try:
            super().__init__(target,None)
            self.api.CreateDirectoryTransactedW.argtypes=[W.LPCWSTR,W.LPCWSTR,ctypes.c_void_p,W.HANDLE]
            self.api.CreateDirectoryTransactedW.restype=W.BOOL
            self.api.CreateHardLinkTransactedW.argtypes=[W.LPCWSTR,W.LPCWSTR,ctypes.c_void_p,W.HANDLE]
            self.api.CreateHardLinkTransactedW.restype=W.BOOL
        except (OSError,AttributeError) as error:
            raise ExportError('publication_unavailable','Protected directory publication requires available local NTFS transactions; no fallback is permitted.') from error

    def begin(self):
        self.transaction=self.ktm.CreateTransaction(None,None,0,0,0,0,None)
        if self.transaction==ctypes.c_void_p(-1).value:
            self.transaction=None;_failure()
        self.outcome='active'
        if not self.api.CreateDirectoryTransactedW(None,str(self.target),None,self.transaction):_failure()

    def add(self,member):
        if not self.api.CreateHardLinkTransactedW(str(self.target/member.name),str(member),None,self.transaction):_failure()

    def commit(self):
        if not self.ktm.CommitTransaction(self.transaction):_failure()
        self.committed=True;self.outcome='committed'
        return self.target
