"""Windows guarded, identity-bound replacement. Never fall back to exists/replace."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path

from mediaforge.features.media.export import ExportError


def identity(path):
    info=Path(path).lstat()
    if not Path(path).is_file() or getattr(info,'st_file_attributes',0) & 0x400:
        raise ExportError('unsafe_destination','Replacement requires a regular file without reparse points.')
    return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns


class ConfirmedOutput:
    """One NTFS transaction locks the approved file and publishes metadata atomically.

    Windows has no path-based compare-and-replace API. TxF is deliberately bounded
    to replacement, with no unsafe fallback on unsupported filesystems/OS versions.
    """
    def __init__(self,target,approved):
        self.target=Path(target)
        self.approved=approved
        self.handles=[]
        self.transaction=None
        self.committed=False
        self.outcome='not_started'
        self.cleanup_errors=[]
        self.api=ctypes.WinDLL('kernel32',use_last_error=True) if os.name=='nt' else None
        if self.api:
            self.api.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
            self.api.CreateFileW.restype=wintypes.HANDLE
            self.api.CloseHandle.argtypes=[wintypes.HANDLE]
            self.api.CloseHandle.restype=wintypes.BOOL
            self.ktm=ctypes.WinDLL('ktmw32',use_last_error=True)
            self.ktm.CreateTransaction.argtypes=[ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,wintypes.LPWSTR]
            self.ktm.CreateTransaction.restype=wintypes.HANDLE
            self.ktm.CommitTransaction.argtypes=[wintypes.HANDLE]
            self.ktm.RollbackTransaction.argtypes=[wintypes.HANDLE]
            self.ktm.CommitTransaction.restype=wintypes.BOOL
            self.ktm.RollbackTransaction.restype=wintypes.BOOL
            self.api.CreateFileTransactedW.argtypes=self.api.CreateFileW.argtypes+[wintypes.HANDLE,ctypes.c_void_p,ctypes.c_void_p]
            self.api.CreateFileTransactedW.restype=wintypes.HANDLE
            self.api.MoveFileTransactedW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,wintypes.HANDLE]
            self.api.MoveFileTransactedW.restype=wintypes.BOOL

    def _open(self,path,access,share,flags=0x00200000):
        handle=self.api.CreateFileW(str(path),access,share,None,3,flags,None)
        if handle==ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def __enter__(self):
        if not self.api:
            raise ExportError('replacement_unavailable','Confirmed replacement requires supported Windows filesystem semantics.')
        try:
            for directory in reversed(self.target.parent.parents):
                if directory==Path(directory.anchor): continue
                if directory.lstat().st_file_attributes & 0x400:
                    raise ExportError('unsafe_destination','Reparse destination directories cannot be replaced safely.')
                self.handles.append(self._open(directory,0x80,3,0x02200000))
            if self.target.parent.lstat().st_file_attributes & 0x400:
                raise ExportError('unsafe_destination','Reparse destination directory is unsupported.')
            self.handles.append(self._open(self.target.parent,0x80,3,0x02200000))
            self.transaction=self.ktm.CreateTransaction(None,None,0,0,0,0,None)
            if self.transaction==ctypes.c_void_p(-1).value:
                self.transaction=None
                raise ctypes.WinError(ctypes.get_last_error())
            self.outcome='active'
            handle=self.api.CreateFileTransactedW(str(self.target),0xC0000000,7,None,3,0x00200000,None,self.transaction,None,None)
            if handle==ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            self.handles.append(handle)
            self.check()
            return self
        except Exception:
            self.__exit__()
            raise

    def check(self):
        try:
            current=identity(self.target)
        except OSError as error:
            raise ExportError('destination_changed','Approved destination disappeared or cannot be inspected. Confirm again.') from error
        if current!=self.approved:
            raise ExportError('destination_changed','Destination changed after confirmation. Nothing was replaced; confirm again.')

    def publish(self,staged,validated=None):
        self.check()
        # The transaction retains its isolation lock after closing the file handle.
        # Windows deletion/replacement cannot commit with that handle still open.
        handle=self.handles[-1]
        if not self.api.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error() or 6)
        self.handles.pop()  # Discard ownership only after the BOOL confirms closure.
        if validated is not None:
            if validated.transaction!=self.transaction:
                raise ExportError('publication_unavailable','Validated file belongs to a different transaction.')
            validated.rename(self.target,replace=True)
        elif not self.api.MoveFileTransactedW(str(staged),str(self.target),None,None,1,self.transaction):
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.ktm.CommitTransaction(self.transaction):
            raise ctypes.WinError(ctypes.get_last_error())
        self.committed=True
        self.outcome='committed'
        return self.target

    @property
    def unresolved(self):
        return bool(self.handles or self.transaction is not None)

    def close(self):
        """Try both documented cleanup routes; keep every unsuccessfully closed handle."""
        for handle in reversed(self.handles[:]):
            if self.api.CloseHandle(handle):
                self.handles.remove(handle)
            else:
                self.cleanup_errors.append(('CloseHandle(file)',handle,ctypes.get_last_error() or 6))
        if self.transaction is not None:
            tx=self.transaction
            if not self.committed:
                if self.ktm.RollbackTransaction(tx):
                    self.outcome='rolled_back'
                else:
                    self.outcome='rollback_unconfirmed'
                    self.cleanup_errors.append(('RollbackTransaction',tx,ctypes.get_last_error() or 6))
            if self.api.CloseHandle(tx):
                self.transaction=None
                if not self.committed and self.outcome!='rolled_back':
                    # Sole, non-inheritable transaction handle; verified last close
                    # requests rollback. A failed explicit rollback alone is not a leak.
                    self.outcome='rolled_back_by_last_close'
            else:
                self.cleanup_errors.append(('CloseHandle(transaction)',tx,ctypes.get_last_error() or 6))
        return not self.unresolved

    def __exit__(self,*_):
        if not self.close():
            raise ExportError('lifecycle_failed','Replacement transaction/handle cleanup is unresolved; resources retained.',
                              retained_resources=(self,))
