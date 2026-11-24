"""Windows object-bound ownership for model staging; no ACL changes or fallback."""
import ctypes as C
from ctypes import wintypes as W
from uuid import uuid4

from mediaforge.application.contracts.models import ModelError
from mediaforge.infrastructure.filesystem.image_output import ReadLocks


class Info(C.Structure):
    _fields_ = [('attributes', W.DWORD), ('created', W.FILETIME), ('accessed', W.FILETIME),
                ('written', W.FILETIME), ('volume', W.DWORD), ('high', W.DWORD),
                ('low', W.DWORD), ('links', W.DWORD), ('id_high', W.DWORD), ('id_low', W.DWORD)]


class Ownership:
    def __init__(self):
        self.handles = []
        self.parents = ReadLocks()
        self.api = C.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateFileW': ([W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE], W.HANDLE),
            'CloseHandle': ([W.HANDLE], W.BOOL),
            'GetFileInformationByHandle': ([W.HANDLE, C.POINTER(Info)], W.BOOL),
            'SetFileInformationByHandle': ([W.HANDLE, C.c_int, C.c_void_p, W.DWORD], W.BOOL),
            'WriteFile': ([W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p], W.BOOL),
            'FlushFileBuffers': ([W.HANDLE], W.BOOL),
        }
        for name, (args, result) in signatures.items():
            method = getattr(self.api, name)
            method.argtypes, method.restype = args, result

    def require(self, result):
        if not result:
            raise ModelError('storage_refused', f'Protected model operation refused (Windows {C.get_last_error()}).')

    def open(self, path, access, share, disposition, *, directory=False, reservation=False):
        handle = self.api.CreateFileW(str(path), access, share, None, disposition,
                                      0x02200000 if directory else 0x00200000, None)
        if handle == C.c_void_p(-1).value:
            error=C.get_last_error()
            if reservation and error==32:
                raise ModelError('in_use','Another OS handle owns this model reservation (Windows sharing violation 32).')
            self.require(False)
        self.handles.append(handle)
        self.info(handle, directory=directory)
        return handle

    def info(self, handle, *, links=1, directory=False):
        value = Info()
        self.require(self.api.GetFileInformationByHandle(handle, C.byref(value)))
        if value.attributes & 0x400 or bool(value.attributes & 0x10) != directory or (not directory and value.links != links):
            raise ModelError('unsafe_alias', 'Managed object has an unsafe type, reparse point or link count.')
        return value

    def pending(self, handle, value):
        flag = W.BOOLEAN(value)
        self.require(self.api.SetFileInformationByHandle(handle, 4, C.byref(flag), C.sizeof(flag)))

    def rename(self, handle, target):
        name = str(target).encode('utf-16-le')
        class Rename(C.Structure):
            _fields_ = [('flags', W.DWORD), ('root', W.HANDLE), ('length', W.DWORD),
                        ('name', C.c_byte * (len(name) + 2))]
        record = Rename()
        record.length = len(name)  # no replacement, ever
        C.memmove(C.addressof(record) + Rename.name.offset, name, len(name))
        self.require(self.api.SetFileInformationByHandle(handle, 22, C.byref(record), C.sizeof(record)))

    def _rename_sibling(self, handle, leaf):
        """Rename within the same directory without reopening a DELETE-held parent."""
        name = leaf.encode('utf-16-le')

        class Rename(C.Structure):
            _fields_ = [('replace', W.BOOLEAN), ('root', W.HANDLE), ('length', W.ULONG),
                        ('name', C.c_byte * len(name))]

        class Status(C.Structure):
            _fields_ = [('status', C.c_void_p), ('information', C.c_size_t)]

        record = Rename(False, None, len(name))
        C.memmove(C.addressof(record) + Rename.name.offset, name, len(name))
        ntdll = C.WinDLL('ntdll')
        native = ntdll.NtSetInformationFile
        native.argtypes = [W.HANDLE, C.POINTER(Status), C.c_void_p, W.ULONG, C.c_int]
        native.restype = W.LONG
        status = native(handle, C.byref(Status()), C.byref(record), Rename.name.offset + len(name), 10)
        if status != 0:
            convert = ntdll.RtlNtStatusToDosError
            convert.argtypes = [W.LONG]
            convert.restype = W.ULONG
            raise ModelError('storage_refused', f'Protected model member rename refused (Windows {convert(status)}).')

    def publish(self, target, source, size, digest, *, inside_owned_directory=False):
        """Stream into an unlinked fresh object, then freeze and rename that handle."""
        import hashlib
        handle = self.open(target.parent / ('.member-' + uuid4().hex), 0xC0010000, 0, 1)
        try:
            self.pending(handle, True)
            self.info(handle, links=0)
            hashed = hashlib.sha256()
            remaining = size
            while remaining:
                block = source.read(min(1024 * 1024, remaining))
                if not block or len(block)>remaining:
                    raise ModelError('corrupt', 'Incomplete model member.')
                hashed.update(block)
                written=W.DWORD()
                self.require(self.api.WriteFile(handle,C.c_char_p(block),len(block),C.byref(written),None))
                if written.value!=len(block):raise ModelError('storage_refused','Incomplete native model write.')
                remaining -= len(block)
            if source.read(1) or hashed.hexdigest() != digest:
                raise ModelError('corrupt', 'Model member size/hash mismatch.')
            self.require(self.api.FlushFileBuffers(handle))
            self.pending(handle, False)  # from here onwards content is immutable
            if inside_owned_directory:
                self._rename_sibling(handle, target.name)
            else:
                self.rename(handle, target)
        except BaseException:
            self.pending(handle, True)
            raise
        finally:
            if handle in self.handles:
                self.require(self.api.CloseHandle(handle))
                self.handles.remove(handle)

    def close(self):
        try:
            self.handles = [h for h in self.handles if not self.api.CloseHandle(h)]
            if self.handles:
                raise OSError('Unclosed model handles')
            self.parents.close()
        except BaseException as error:
            raise ModelError('lifecycle_failed', 'Model ownership remains unresolved.', retained_resources=(self,)) from error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class ModelMutex(Ownership):
    """Exclusive per-model OS lease, also excludes other application instances."""
    def __init__(self, paths, model_id):
        super().__init__()
        try:
            self.parents.parents(paths.manifests)
            self.open(paths.manifests / ('.' + model_id + '.lock'), 0x80000000, 0, 4,reservation=True)
        except BaseException:
            self.close()
            raise
