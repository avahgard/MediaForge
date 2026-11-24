"""Pre-execution Windows image admission, not a post-load notification hook.

The creating thread owns the debug event pump through EXIT_PROCESS continuation.
Windows stops the debuggee at image events; unapproved images are never continued.
The optional runtime identity set comes from authenticated, still-leased objects.
Windows System32 and the explicitly supplied CPython installation are the TCB.
"""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import sys

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.models.owned import Info


class ExceptionRecord(C.Structure):
    _fields_=[('code',W.DWORD),('flags',W.DWORD),('record',C.c_void_p),
              ('address',C.c_void_p),('count',W.DWORD),('parameters',C.c_size_t*15)]

class ExceptionInfo(C.Structure):
    _fields_=[('record',ExceptionRecord),('first',W.DWORD)]

class CreateProcess(C.Structure):
    _fields_=[('file',W.HANDLE),('process',W.HANDLE),('thread',W.HANDLE),('base',C.c_void_p),
              ('offset',W.DWORD),('size',W.DWORD),('tls',C.c_void_p),('start',C.c_void_p),
              ('name',C.c_void_p),('unicode',W.WORD)]

class LoadDll(C.Structure):
    _fields_=[('file',W.HANDLE),('base',C.c_void_p),('offset',W.DWORD),('size',W.DWORD),
              ('name',C.c_void_p),('unicode',W.WORD)]

class EventData(C.Union):
    _fields_=[('exception',ExceptionInfo),('create',CreateProcess),('dll',LoadDll),('raw',C.c_byte*160)]

class DebugEvent(C.Structure):
    _fields_=[('code',W.DWORD),('pid',W.DWORD),('tid',W.DWORD),('data',EventData)]


def identity(api,handle):
    api.GetFileInformationByHandle.argtypes=[W.HANDLE,C.POINTER(Info)]
    api.GetFileInformationByHandle.restype=W.BOOL
    info=Info()
    if not api.GetFileInformationByHandle(handle,C.byref(info)):
        raise LocalAiError('native_protection_unavailable','Cannot authenticate mapped image object.')
    return (info.volume,info.id_high,info.id_low),info


class NativeGate:
    def __init__(self,runtime=None):
        self.api=C.WinDLL('kernel32',use_last_error=True)
        self.ps=C.WinDLL('psapi',use_last_error=True)
        signatures={
            'WaitForDebugEvent':([C.POINTER(DebugEvent),W.DWORD],W.BOOL),
            'ContinueDebugEvent':([W.DWORD,W.DWORD,W.DWORD],W.BOOL),
            'DebugSetProcessKillOnExit':([W.BOOL],W.BOOL),
            'GetFinalPathNameByHandleW':([W.HANDLE,W.LPWSTR,W.DWORD,W.DWORD],W.DWORD),
            'GetFileInformationByHandle':([W.HANDLE,C.POINTER(Info)],W.BOOL),
            'GetSystemDirectoryW':([W.LPWSTR,W.UINT],W.UINT),
            'GetCurrentProcess':([],W.HANDLE),
            'GetModuleHandleW':([W.LPCWSTR],W.HANDLE),
            'DuplicateHandle':([W.HANDLE,W.HANDLE,W.HANDLE,C.POINTER(W.HANDLE),W.DWORD,W.BOOL,W.DWORD],W.BOOL),
            'CloseHandle':([W.HANDLE],W.BOOL),
        }
        try:
            for name,(args,result) in signatures.items():
                f=getattr(self.api,name);f.argtypes=args;f.restype=result
            self.ps.GetMappedFileNameW.argtypes=[W.HANDLE,C.c_void_p,W.LPWSTR,W.DWORD]
            self.ps.GetMappedFileNameW.restype=W.DWORD
        except AttributeError as error:
            raise LocalAiError('native_protection_unavailable','Windows image admission APIs are unavailable; inference is refused.') from error
        self.locks=ReadLocks();self.pending=None;self.exited=False;self.started=False
        self.images=[];self.event_files=[];self.events=0;self.breakpoint=False
        self.allowed=dict(runtime.native_ids if runtime is not None else {})
        try:
            # Independently own the SAME authenticated objects and ancestors.
            # A failed child retirement can outlive the outer verification
            # context; FileID records alone cannot preserve that ownership.
            # Duplicates retain the original sharing restrictions, no reopen
            # or scan-to-open gap, and close only with this native owner.
            if runtime is not None:
                current=self.api.GetCurrentProcess()
                for original in runtime.handles:
                    duplicate=W.HANDLE()
                    self.require(self.api.DuplicateHandle(current,original,current,C.byref(duplicate),0,False,2))
                    self.locks.handles.append(duplicate.value)
            # This is an explicitly selected, already trusted host CPython, not
            # PATH/global site-packages. Optional-runtime names are never trusted
            # by prefix. Listed native bytes remain locked throughout use.
            base=Path(sys._base_executable).parent
            self.locks.parents(base)
            files=[Path(sys._base_executable),*base.glob('*.dll')]
            if (base/'DLLs').is_dir():
                self.locks.add(base/'DLLs',True)
                files.extend((base/'DLLs').glob('*.dll'));files.extend((base/'DLLs').glob('*.pyd'))
            for file in files:
                h=self.locks.add(file)
                key,info=identity(self.api,h)
                if info.attributes&0x400:raise LocalAiError('native_protection_unavailable','Aliased CPython native TCB.')
                self.allowed[key]=str(file)
            buffer=C.create_unicode_buffer(32768)
            n=self.api.GetSystemDirectoryW(buffer,len(buffer))
            if not 0<n<len(buffer):raise LocalAiError('native_protection_unavailable','Windows System32 identity is unavailable.')
            self.system=Path(buffer.value)
            device=C.create_unicode_buffer(32768)
            if not self.ps.GetMappedFileNameW(self.api.GetCurrentProcess(),self.api.GetModuleHandleW('kernel32.dll'),device,len(device)):
                raise LocalAiError('native_protection_unavailable','Windows volume mapping is unavailable.')
            self.system_device=device.value.rsplit('\\',1)[0]
        except BaseException:
            self.close();raise

    def require(self,value):
        if not value:raise LocalAiError('native_protection_unavailable',f'Windows image admission failed ({C.get_last_error()}).')

    def arm(self):
        self.require(self.api.DebugSetProcessKillOnExit(True));self.started=True

    def _image(self,process,file,base):
        buffer=C.create_unicode_buffer(32768)
        if file:
            key,info=identity(self.api,file)
            n=self.api.GetFinalPathNameByHandleW(file,buffer,len(buffer),0)
            self.require(0<n<len(buffer))
            path=buffer.value.removeprefix('\\\\?\\')
            if key in self.allowed:
                if info.attributes&0x400:raise LocalAiError('runtime_integrity','Mapped runtime object is a reparse alias.')
                expected=Path(self.allowed[key])
                if Path(path)!=expected or info.links!=1:
                    raise LocalAiError('runtime_integrity','Mapped native image path/alias differs from the owned object.')
                return path,'owned',key
            if Path(path).parent in (self.system,self.system/'wbem',self.system/'downlevel'):
                return path,'windows-protected-os',key
            if Path(path).is_relative_to(self.system.parent/'WinSxS'):
                return path,'windows-component-store',key
        else:
            # KnownDll section events may lack hFile. Only an exact direct
            # System32 mapping is accepted without an owned file identity.
            n=self.ps.GetMappedFileNameW(int(process._handle),base,buffer,len(buffer))
            self.require(0<n<len(buffer))
            path=buffer.value
            parent=path.rsplit('\\',1)[0]
            if parent.casefold()==self.system_device.casefold():return path,'windows-known-dll',None
        raise LocalAiError('runtime_integrity',f'Unlisted native image refused before execution: {path}')

    def continue_pending(self):
        if self.pending is not None:
            pid,tid,status=self.pending
            self.require(self.api.ContinueDebugEvent(pid,tid,status));self.pending=None

    def pump(self,process,*,retiring=False):
        """Bounded, same-thread pump. On refusal leave the event stopped."""
        for _ in range(64):
            if self.pending is not None:
                if not retiring:raise LocalAiError('lifecycle_failed','Unacknowledged native image admission.')
                self.continue_pending()
            event=DebugEvent()
            # A short bounded wait lets the resumed loader deliver its next
            # event without paying the outer resource-monitor interval per DLL.
            # At most 64 waits occur before cancellation/resource checks resume.
            if not self.api.WaitForDebugEvent(C.byref(event),1):
                error=C.get_last_error()
                if error in (121,258) or self.exited:return
                raise LocalAiError('native_protection_unavailable',f'Windows debug event stream failed ({error}).')
            self.events+=1
            status=0x10002 # DBG_CONTINUE
            if event.code==1:
                code=event.data.exception.record.code
                if code==0x80000003 and not self.breakpoint:self.breakpoint=True
                else:status=0x80010001 # let actual application handlers run
            self.pending=(event.pid,event.tid,status)
            if event.pid!=process.pid:raise LocalAiError('runtime_integrity','Unexpected process in native image admission.')
            if event.code in (3,6):
                image=event.data.create if event.code==3 else event.data.dll
                if image.file:self.event_files.append(image.file)
                try:
                    if not retiring:
                        path,kind,key=self._image(process,image.file,image.base)
                        self.images.append(dict(path=path,authority=kind,identity=key))
                        if len(self.images)>256:raise LocalAiError('runtime_integrity','Native image bound exceeded.')
                finally:
                    if image.file:
                        if self.api.CloseHandle(image.file):self.event_files.remove(image.file)
                        else:
                            # CpuJob's finally still owns retirement/retry. Only
                            # failed final cleanup may report retained resources.
                            raise LocalAiError('native_protection_unavailable','Debug image handle close failed; worker admission refused.')
            if event.code==5:self.exited=True
            if event.code==9 and not retiring:raise LocalAiError('native_protection_unavailable','Windows reported a fatal debugger error.')
            self.continue_pending()
            if self.exited:return

    def close(self):
        try:
            self.event_files=[h for h in self.event_files if not self.api.CloseHandle(h)]
            if self.event_files or self.pending is not None or self.started and not self.exited:
                raise OSError('Native event/handle ownership remains unresolved')
            self.locks.close()
        except BaseException as error:
            raise LocalAiError('lifecycle_failed','Native image admission ownership retained.',retained_resources=(self,)) from error
