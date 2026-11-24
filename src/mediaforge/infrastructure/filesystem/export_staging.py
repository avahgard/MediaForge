"""Windows ownership of the two objects used by single-file Media Export."""
from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
import os
from stat import FILE_ATTRIBUTE_READONLY
import time

from mediaforge.infrastructure.models.owned import Info
from mediaforge.infrastructure.filesystem.speech_directory import Attributes, IoStatus, UnicodeString


_RETRY_DELAYS = (0.025, 0.05, 0.1, 0.2, 0.4)
_RETRY_ERRORS = (5, 32, 33)
_POSIX_DELETE = 0x03
_IGNORE_READONLY = 0x10


class _FileIdInfo(C.Structure):
    _fields_ = [('volume', C.c_uint64), ('file_id', C.c_ubyte * 16)]


class ExportStaging:
    def __init__(self, directory, partial):
        from mediaforge.infrastructure.filesystem.image_output import ReadLocks

        self.directory = directory
        self.partial = partial
        self.parents = ReadLocks()
        self.directory_pin = None
        self.writer = None
        self.partial_anchor = None
        self.directory_anchor = None
        self.deletion = None
        self.published = False
        self.api = C.WinDLL('kernel32', use_last_error=True)
        for name, args, result in (
            ('CreateFileW', [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE], W.HANDLE),
            ('CloseHandle', [W.HANDLE], W.BOOL),
            ('GetFileInformationByHandle', [W.HANDLE, C.POINTER(Info)], W.BOOL),
            ('GetFileInformationByHandleEx', [W.HANDLE, C.c_int, C.c_void_p, W.DWORD], W.BOOL),
            ('SetFileInformationByHandle', [W.HANDLE, C.c_int, C.c_void_p, W.DWORD], W.BOOL),
            ('GetCurrentProcess', [], W.HANDLE),
            ('DuplicateHandle', [W.HANDLE, W.HANDLE, W.HANDLE, C.POINTER(W.HANDLE),
                                 W.DWORD, W.BOOL, W.DWORD], W.BOOL),
        ):
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        native = C.WinDLL('ntdll')
        self._create_file = native.NtCreateFile
        self._create_file.argtypes = [C.POINTER(W.HANDLE), W.DWORD, C.POINTER(Attributes), C.POINTER(IoStatus),
                                      C.c_void_p, W.ULONG, W.ULONG, W.ULONG, W.ULONG, C.c_void_p, W.ULONG]
        self._create_file.restype = W.LONG
        self._dos_error = native.RtlNtStatusToDosError
        self._dos_error.argtypes, self._dos_error.restype = [W.LONG], W.ULONG

    @staticmethod
    def _require(ok):
        if not ok:
            raise C.WinError(C.get_last_error() or 6)

    def _create(self, parent, name, access, share, *, directory):
        # FILE_CREATE maps to CREATE_NEW, relative to the already opened parent.
        encoded = name.encode('utf-16-le')
        buffer = C.create_string_buffer(encoded + b'\0\0')
        text = UnicodeString(len(encoded), len(encoded) + 2, C.cast(buffer, W.LPWSTR))
        attrs = Attributes(C.sizeof(Attributes), parent, C.pointer(text), 0x40, None, None)
        handle, status = W.HANDLE(), IoStatus()
        options = 0x200020 | (1 if directory else 0x40)  # No-follow, synchronous, required type.
        result = self._create_file(C.byref(handle), access | 0x100000, C.byref(attrs), C.byref(status),
                                   None, 0x80, share, 2, options, None, 0)
        if result < 0:
            raise C.WinError(self._dos_error(result))
        return handle.value

    def create(self):
        self.parents.parents(self.directory.parent)
        self.directory_pin = self._create(self.parents.handles[-1], self.directory.name, 0x81, 3, directory=True)
        self._key(self.directory_pin, directory=True)
        self.writer = self._create(self.directory_pin, self.partial.name, 0xC0000000, 0, directory=False)
        self._key(self.writer, directory=False)

    def _inspect(self, handle, *, directory):
        info = Info()
        self._require(self.api.GetFileInformationByHandle(handle, C.byref(info)))
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory or (not directory and info.links != 1):
            raise OSError('Export staging object has an unexpected type, reparse point or link count')
        identity = _FileIdInfo()
        self._require(self.api.GetFileInformationByHandleEx(handle, 18, C.byref(identity), C.sizeof(identity)))
        if not any(identity.file_id):
            raise OSError('Windows did not provide a usable Export staging file ID')
        return (identity.volume, bytes(identity.file_id)), info.attributes

    def _key(self, handle, *, directory):
        return self._inspect(handle, directory=directory)[0]

    def _open(self, path, access, share, *, directory):
        flags = 0x00200000 | (0x02000000 if directory else 0)
        handle = self.api.CreateFileW(os.fspath(path), access, share, None, 3, flags, None)
        if handle == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        return handle

    def _close(self, name):
        handle = getattr(self, name)
        if handle is not None:
            self._require(self.api.CloseHandle(handle))
            setattr(self, name, None)

    @contextmanager
    def descriptor(self):
        import msvcrt

        current, duplicate = self.api.GetCurrentProcess(), W.HANDLE()
        self._require(self.api.DuplicateHandle(current, self.writer, current, C.byref(duplicate), 0, False, 2))
        try:
            descriptor = msvcrt.open_osfhandle(duplicate.value, os.O_RDWR | os.O_BINARY)
        except BaseException:
            self._require(self.api.CloseHandle(duplicate))
            raise
        with os.fdopen(descriptor, 'r+b', buffering=0) as stream:
            yield stream

    def _handoff(self, old, anchor, path, *, directory):
        pin = getattr(self, old)
        if pin is None:
            return
        if getattr(self, anchor) is None:
            # A metadata-only handle survives release of the exclusive writer
            # or directory pin, keeping the original file ID alive.
            setattr(self, anchor, self._open(path, 0x80, 7, directory=directory))
        if self._key(pin, directory=directory) != self._key(getattr(self, anchor), directory=directory):
            raise OSError('Export staging identity changed during ownership handoff')
        self._close(old)

    def finish_writing(self):
        self._handoff('writer', 'partial_anchor', self.partial, directory=False)

    def check_partial(self, handle):
        if self._key(handle, directory=False) != self._key(self.partial_anchor, directory=False):
            raise OSError('Export staged file identity changed before validation')

    def _delete(self, anchor, path, *, directory):
        expected = self._key(getattr(self, anchor), directory=directory)
        for delay in (*_RETRY_DELAYS, None):
            try:
                self.deletion = self._open(path, 0x10080, 3, directory=directory)
                try:
                    actual, attributes = self._inspect(self.deletion, directory=directory)
                    if actual != expected:
                        raise OSError('Export staging identity changed before handle deletion')
                    # Ignore ReadOnly only for the original Export-created
                    # directory. Deletion still needs DELETE access and NTFS
                    # enforces emptiness. This does not write any attributes.
                    flags = _POSIX_DELETE
                    if directory and attributes & FILE_ATTRIBUTE_READONLY:
                        flags |= _IGNORE_READONLY
                    flags = W.DWORD(flags)
                    self._require(self.api.SetFileInformationByHandle(self.deletion, 21, C.byref(flags), C.sizeof(flags)))
                finally:
                    self._close('deletion')
                break
            except OSError as error:
                if self.deletion is not None or delay is None or getattr(error, 'winerror', None) not in _RETRY_ERRORS:
                    raise
                time.sleep(delay)
        self._close(anchor)

    def close(self):
        if self.deletion is not None:
            raise OSError('Export staging deletion handle could not be released')
        self.finish_writing()
        if self.partial_anchor is not None:
            if self.published:
                self._close('partial_anchor')  # The owned file is now the final output.
            else:
                self._delete('partial_anchor', self.partial, directory=False)
        self._handoff('directory_pin', 'directory_anchor', self.directory, directory=True)
        if self.directory_anchor is not None:
            self._delete('directory_anchor', self.directory, directory=True)
        self.parents.close()
