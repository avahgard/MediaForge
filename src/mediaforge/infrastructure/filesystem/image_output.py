"""Bounded Windows source/parent ownership and no-replace directory publication."""
import ctypes
from ctypes import wintypes as W
import os
from pathlib import Path
from mediaforge.features.media.export import ExportError
from mediaforge.infrastructure.filesystem.export_output import ExportOutput
from mediaforge.infrastructure.filesystem.staged_output import StagedOutput
from mediaforge.application.contracts.image_source import SequenceSource
from mediaforge.infrastructure.filesystem.image_publication import DirectoryPublication,link_file


class _Tag(ctypes.Structure):
    _fields_=[('attributes',W.DWORD),('tag',W.DWORD)]


class ReadLocks:
    """Native handles do not consume one CRT stream per manifest member."""
    def __init__(self):
        self.handles=[]
        self.directories=set()
        self.api=ctypes.WinDLL('kernel32',use_last_error=True) if os.name=='nt' else None
        if self.api:
            self.api.CreateFileW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,ctypes.c_void_p,W.DWORD,W.DWORD,W.HANDLE]
            self.api.CreateFileW.restype=W.HANDLE
            self.api.CloseHandle.argtypes=[W.HANDLE];self.api.CloseHandle.restype=W.BOOL

    def add(self,path,directory=False,owner_delete=False,share_delete=False):
        if self.api is None:raise ExportError('unsupported_platform','Image conversion currently requires Windows filesystem ownership.')
        path=Path(path)
        if len(str(path))>30000 or any(p.endswith((' ','.')) for p in path.parts[1:]):
            raise ExportError('unsafe_path','Unsupported ambiguous or overlong Windows path.')
        if directory and str(path).casefold() in self.directories:return
        if path.lstat().st_file_attributes&0x400:raise ExportError('unsafe_path','Reparse paths are unsupported for conversion.')
        handle=self.api.CreateFileW(str(path),(0x10080 if owner_delete else 0x80) if directory else 0x80000000,3 if directory else 5 if share_delete else 1,None,3,0x02200000 if directory else 0x00200000,None)
        if handle==ctypes.c_void_p(-1).value:raise ExportError('source_busy','Cannot safely lock source/output path. Close other writers and try again.')
        self.handles.append(handle)
        self.api.GetFileInformationByHandleEx.argtypes=[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD]
        self.api.GetFileInformationByHandleEx.restype=W.BOOL
        tag=_Tag()
        if not self.api.GetFileInformationByHandleEx(handle,9,ctypes.byref(tag),ctypes.sizeof(tag)) or tag.attributes&0x400 or bool(tag.attributes&0x10)!=directory:
            raise ExportError('unsafe_path','Opened file identity/type is unsupported or uncertain.')
        if directory:self.directories.add(str(path).casefold())
        return handle

    def parents(self,path):
        for directory in reversed((Path(path),*Path(path).parents)):
            self.add(directory,True)

    def close(self):
        self.handles=[h for h in self.handles if not self.api.CloseHandle(h)]
        if self.handles:raise ExportError('lifecycle_failed','Native source/path handles could not be released; processing remains disabled.',retained_resources=(self,))

    def __enter__(self):return self
    def __exit__(self,*_):self.close()


def local_ntfs(folder):
    if os.name!='nt' or str(folder).startswith('\\\\'):raise ExportError('unsupported_volume','Sequence output requires a local Windows NTFS folder.')
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.GetVolumePathNameW.argtypes=[W.LPCWSTR,W.LPWSTR,W.DWORD];api.GetVolumePathNameW.restype=W.BOOL
    api.GetVolumeInformationW.argtypes=[W.LPCWSTR,W.LPWSTR,W.DWORD,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,W.LPWSTR,W.DWORD];api.GetVolumeInformationW.restype=W.BOOL
    api.GetDriveTypeW.argtypes=[W.LPCWSTR];api.GetDriveTypeW.restype=W.UINT
    root=ctypes.create_unicode_buffer(32768);fs=ctypes.create_unicode_buffer(64)
    if not api.GetVolumePathNameW(str(folder),root,len(root)) or api.GetDriveTypeW(root.value)!=3 or not api.GetVolumeInformationW(root.value,None,0,None,None,None,fs,len(fs)) or fs.value!='NTFS':
        raise ExportError('unsupported_volume','Sequence output requires a verified local NTFS folder; no copy/replace fallback.')


class ImageOutput(ExportOutput):
    def __init__(self,request,logger):
        super().__init__(request.source,request.destination,logger,protected_sources=request.protected_sources)
        self.is_sequence=request.mode=='sequence';self.request=request
        self.auxiliary=[];self.members=[];self.paths=ReadLocks();self.outer_lock=ReadLocks();self.member_locks=ReadLocks();self.commit_handle=None;self.outer_handle=None;self.outer_identity=None;self.stage_identity=None;self._published=False
        self.publication=None

    def __enter__(self):
        try:
            self.paths.parents(self.destination.parent)
            local_ntfs(self.destination.parent)
            StagedOutput.__enter__(self)
            info=self.directory.lstat();self.outer_identity=(info.st_dev,info.st_ino)
            self.outer_handle=self.outer_lock.add(self.directory,True,owner_delete=True)
            if self.is_sequence:
                self.staged=self.directory/'frames';self.staged.mkdir()
                layout=self.request.numbering
                if layout is None or layout.count!=self.request.count:raise ExportError('invalid_numbering','Missing frozen sequence output layout.')
                self.members=[self.staged/layout.name(i) for i in range(layout.count)]
                self.start_number=layout.start
                self.sequence_pattern=self.staged/layout.pattern
                self.stage_identity=(self.staged.stat().st_dev,self.staged.stat().st_ino)
                api=self.paths.api
                self.commit_handle=api.CreateFileW(str(self.staged),0x10080,3,None,3,0x02200000,None)
                if self.commit_handle==ctypes.c_void_p(-1).value:
                    self.commit_handle=None
                    raise ExportError('staging_unavailable','Cannot own sequence staging directory for no-replace publication.')
                tag=_Tag()
                if not api.GetFileInformationByHandleEx(self.commit_handle,9,ctypes.byref(tag),ctypes.sizeof(tag)) or tag.attributes&0x400 or not tag.attributes&0x10:
                    self._retain('Sequence staging native identity/type is uncertain')
                info=self.staged.lstat()
                if (info.st_dev,info.st_ino)!=self.stage_identity:self._retain('Sequence staging identity changed before ownership')
                self.publication=DirectoryPublication(self.destination)
                self.publication.begin()
            return self
        except BaseException:
            self._cleanup();raise

    def auxiliary_path(self,name):
        path=self.directory/name
        if path.exists():raise ExportError('staging_collision','Unexpected staging member.')
        self.auxiliary.append(path);return path

    def publish(self,control):
        if not self.is_sequence:
            # Adding a name to this locked file requires no DELETE sharing.
            # Readers/validators stay open; no writable interval before commit.
            def publish_locked(staged,target):
                result=link_file(self.paths.api,staged,target)
                self._published=True
                return result
            self._publisher=publish_locked
            return super().publish(control)
        if set(self.staged.iterdir())!=set(self.members):raise ExportError('validation_failed','Sequence membership changed before publication.')
        # Only frozen, still-read-locked members enter the isolated NEW directory.
        # External stage insertions cannot become final members. Outside writers
        # cannot access the new directory until this one transaction commits.
        for member in self.members:
            control.check();self.publication.add(member)
        result=control.publish(self.publication.commit)
        self._published=True
        return result

    def _retain(self,reason):
        outcome=self.publication.outcome if self.publication is not None else ('committed' if self._published else 'not_committed')
        raise ExportError('lifecycle_failed',f'{reason}. Outcome: {outcome}. '
            f'Resources/staging retained at {self.directory}; processing is disabled. '
            'Close the application and resolve retained resources before exporting again.',retained_resources=(self,))

    def _cleanup(self):
        if not self.cleanup_guard():self._retain('Child ownership is unresolved')
        try:
            if self.publication is not None and not self.publication.close():
                self._retain('Directory publication transaction cleanup is unresolved')
            if self.directory is not None:
                info=self.directory.lstat()
                if info.st_file_attributes&0x400 or not info.st_file_attributes&0x10 or (info.st_dev,info.st_ino)!=self.outer_identity:
                    self._retain('Outer staging identity changed; cleanup refused')
            self.member_locks.close()
            for path in self.auxiliary:path.unlink(missing_ok=True)
            if self.is_sequence and self.staged is not None:
                try:info=self.staged.lstat()
                except FileNotFoundError:info=None
                if info is not None:
                    if info.st_file_attributes&0x400 or not info.st_file_attributes&0x10 or (info.st_dev,info.st_ino)!=self.stage_identity:self._retain('Sequence staging identity changed; cleanup refused')
                # Unknown entries/reparse paths retain staging and poison the gate.
                present=set(self.staged.iterdir()) if info is not None else set()
                if not present<=set(self.members) or any(p.lstat().st_file_attributes&0x400 for p in present):self._retain('Unexpected sequence staging membership')
                if info is not None and self.commit_handle is None:self._retain('Sequence staging handle was not acquired; cleanup ownership is uncertain')
                for path in present:path.unlink()
                if self.commit_handle is not None:self._delete_owned_directory(self.commit_handle)
            elif not self.is_sequence and self.staged is not None:self.staged.unlink(missing_ok=True)
            if self.commit_handle is not None:
                if not self.paths.api.CloseHandle(self.commit_handle):self._retain('Sequence commit handle cleanup failed')
                self.commit_handle=None
            if self.outer_handle is not None:self._delete_owned_directory(self.outer_handle)
            elif self.directory is not None:self._retain('Outer staging handle was not acquired; cleanup ownership is uncertain')
            self.outer_lock.close()
            self.outer_handle=None;self.directory=None
            self.paths.close()
        except ExportError as error:
            if any(owner is self for owner in error.retained_resources):raise
            self._retain('Image staging handle cleanup is unresolved: '+str(error))
        except OSError:self._retain('Image staging cleanup failed')

    def _delete_owned_directory(self,handle):
        # Set deletion on the same owned, non-reparse directory. Keep its
        # non-delete-sharing handle until CloseHandle completes the operation.
        # New/unknown members cause failure, never recursive deletion.
        api=self.paths.api
        api.SetFileInformationByHandle.argtypes=[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD]
        api.SetFileInformationByHandle.restype=W.BOOL
        delete=ctypes.c_ubyte(1)
        if not api.SetFileInformationByHandle(handle,4,ctypes.byref(delete),ctypes.sizeof(delete)):
            self._retain(f'Owned directory deletion refused (Windows {ctypes.get_last_error()})')
