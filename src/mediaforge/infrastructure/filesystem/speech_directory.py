"""Atomic create-and-own directory capability for process-only Dub sessions.

NtCreateFile(FILE_CREATE|FILE_DIRECTORY_FILE) returns the created object itself;
there is no mkdir -> reopen window or persistent ACL/recovery state.
"""
import ctypes as C
from ctypes import wintypes as W
from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.infrastructure.models.owned import Ownership

class UnicodeString(C.Structure):
    _fields_=[('length',W.USHORT),('maximum',W.USHORT),('buffer',W.LPWSTR)]
class Attributes(C.Structure):
    _fields_=[('length',W.ULONG),('root',W.HANDLE),('name',C.POINTER(UnicodeString)),
              ('flags',W.ULONG),('security',C.c_void_p),('quality',C.c_void_p)]
class IoStatus(C.Structure):
    _fields_=[('status',C.c_void_p),('information',C.c_size_t)]

def create_directory(path):
    owner=Ownership()
    try:
        owner.parents.parents(path.parent)
        text='\\??\\'+str(path)
        encoded=text.encode('utf-16-le')
        buffer=C.create_string_buffer(encoded+b'\0\0')
        name=UnicodeString(len(encoded),len(encoded)+2,C.cast(buffer,W.LPWSTR))
        attrs=Attributes(C.sizeof(Attributes),None,C.pointer(name),0x40,None,None)
        api=C.WinDLL('ntdll',use_last_error=True)
        api.NtCreateFile.argtypes=[C.POINTER(W.HANDLE),W.DWORD,C.POINTER(Attributes),C.POINTER(IoStatus),
            C.c_void_p,W.ULONG,W.ULONG,W.ULONG,W.ULONG,C.c_void_p,W.ULONG]
        api.NtCreateFile.restype=W.LONG
        handle=W.HANDLE();io=IoStatus()
        # LIST_DIRECTORY + READ_ATTRIBUTES + SYNCHRONIZE; deny directory rename.
        # Holding DELETE here would conflict with Windows' child rename opens.
        # FILE_CREATE never opens/replaces an existing object. Ancestors held.
        status=api.NtCreateFile(C.byref(handle),0x100081,C.byref(attrs),C.byref(io),None,0x80,3,2,0x200021,None,0)
        if status<0:
            raise LocalAiError('storage_refused',f'Cannot create owned speech directory (NTSTATUS {status&0xffffffff:08x}).')
        owner.handles.append(handle.value)
        owner.info(handle.value,directory=True)
        if io.information!=2:raise LocalAiError('storage_refused','Directory was not newly created; no adoption permitted.')
        return owner,handle.value
    except AttributeError as error:
        owner.close()
        raise LocalAiError('native_protection_unavailable','Atomic speech directory creation is unavailable; no fallback.') from error
    except BaseException:
        owner.close();raise
