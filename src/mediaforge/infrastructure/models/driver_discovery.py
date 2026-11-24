"""Device-bound Windows driver-package discovery for the Qwen-only policy."""
from pathlib import Path
import ctypes as C,winreg,hashlib,uuid,struct
from ctypes import wintypes as W

PURPOSE='qwen-cuda-driver-v1'
class Refusal(RuntimeError):
    def __init__(self,code,message,retained=()):self.code=code;self.retained_resources=retained;super().__init__(message)
class Guid(C.Structure):_fields_=[('value',C.c_ubyte*16)]
def guid(value):return Guid((C.c_ubyte*16).from_buffer_copy(uuid.UUID(value).bytes_le))
class Device(C.Structure):_fields_=[('size',W.DWORD),('class_guid',Guid),('instance',W.DWORD),('reserved',C.c_size_t)]
class InfContext(C.Structure):_fields_=[('inf',C.c_void_p),('current',C.c_void_p),('section',W.UINT),('line',W.UINT)]
assert C.sizeof(Device)==32 and C.sizeof(InfContext)==24

def system_directory():
    k=C.WinDLL('kernel32',winmode=0x800,use_last_error=True);k.GetSystemDirectoryW.argtypes=[W.LPWSTR,W.UINT];k.GetSystemDirectoryW.restype=W.UINT
    text=C.create_unicode_buffer(32768);n=k.GetSystemDirectoryW(text,len(text))
    if not 0<n<len(text):raise Refusal('windows_unavailable','Cannot resolve actual System32')
    return Path(text.value)

class Discovery:
    def __init__(self):
        self.system=system_directory();self.api=C.WinDLL(str(self.system/'setupapi.dll'),use_last_error=True,winmode=0x800)
        definitions={'SetupDiGetClassDevsW':([C.POINTER(Guid),W.LPCWSTR,W.HWND,W.DWORD],W.HANDLE),'SetupDiEnumDeviceInfo':([W.HANDLE,W.DWORD,C.POINTER(Device)],W.BOOL),'SetupDiGetDeviceInstanceIdW':([W.HANDLE,C.POINTER(Device),W.LPWSTR,W.DWORD,C.POINTER(W.DWORD)],W.BOOL),'SetupDiGetDeviceRegistryPropertyW':([W.HANDLE,C.POINTER(Device),W.DWORD,C.POINTER(W.DWORD),C.c_void_p,W.DWORD,C.POINTER(W.DWORD)],W.BOOL),'SetupDiOpenDevRegKey':([W.HANDLE,C.POINTER(Device),W.DWORD,W.DWORD,W.DWORD,W.DWORD],W.HANDLE),'SetupDiDestroyDeviceInfoList':([W.HANDLE],W.BOOL),'SetupGetInfDriverStoreLocationW':([W.LPCWSTR,C.c_void_p,W.LPCWSTR,W.LPWSTR,W.DWORD,C.POINTER(W.DWORD)],W.BOOL),'SetupOpenInfFileW':([W.LPCWSTR,W.LPCWSTR,W.DWORD,C.POINTER(W.UINT)],W.HANDLE),'SetupFindFirstLineW':([W.HANDLE,W.LPCWSTR,W.LPCWSTR,C.POINTER(InfContext)],W.BOOL),'SetupGetStringFieldW':([C.POINTER(InfContext),W.DWORD,W.LPWSTR,W.DWORD,C.POINTER(W.DWORD)],W.BOOL),'SetupCloseInfFile':([W.HANDLE],None)}
        for name,(args,result) in definitions.items():f=getattr(self.api,name);f.argtypes=args;f.restype=result
    def require(self,value,label):
        if not value:raise Refusal('discovery_refused',label+' WinError '+str(C.get_last_error()))
    def selected(self):
        devices=self.api.SetupDiGetClassDevsW(C.byref(guid('4d36e968-e325-11ce-bfc1-08002be10318')),None,None,2)
        self.require(devices!=C.c_void_p(-1).value,'GetClassDevs');matches=[]
        try:
            for index in range(32):
                row=Device();row.size=C.sizeof(row)
                if not self.api.SetupDiEnumDeviceInfo(devices,index,C.byref(row)):
                    if C.get_last_error()==259:break
                    self.require(False,'EnumDeviceInfo')
                text=C.create_unicode_buffer(4096);needed=W.DWORD();self.require(self.api.SetupDiGetDeviceInstanceIdW(devices,C.byref(row),text,len(text),C.byref(needed)),'DeviceInstanceId')
                if not text.value.upper().startswith('PCI\\VEN_10DE&DEV_2882&'):continue
                key=self.api.SetupDiOpenDevRegKey(devices,C.byref(row),1,0,2,0x20019);self.require(key!=C.c_void_p(-1).value,'OpenDevRegKey')
                try:
                    values={}
                    for name in ('InfPath','InfSection','DriverVersion','DriverDate','ProviderName','MatchingDeviceId','DriverDesc'):
                        try:values[name]=winreg.QueryValueEx(key,name)[0]
                        except FileNotFoundError:values[name]=None
                finally:winreg.CloseKey(key)
                matches.append(dict(instance=text.value,class_guid='4d36e968-e325-11ce-bfc1-08002be10318',driver=values))
            else:raise Refusal('discovery_bound','More than32 display devices')
            if len(matches)!=1:raise Refusal('device_ambiguous','Expected one exact RTX4060 PCI hardware instance')
            result=matches[0];name=result['driver']['InfPath']
            if not isinstance(name,str) or Path(name).name!=name or not name.lower().endswith('.inf'):raise Refusal('unsafe_metadata','Unexpected installed INF selector')
            published=self.system.parent/'INF'/name;out=C.create_unicode_buffer(32768);needed=W.DWORD()
            self.require(self.api.SetupGetInfDriverStoreLocationW(str(published),None,None,out,len(out),C.byref(needed)),'GetInfDriverStoreLocation')
            store=Path(out.value)
            if not store.is_relative_to(self.system/'DriverStore/FileRepository') or store.suffix.lower()!='.inf':raise Refusal('unsafe_metadata','Store INF outside actual local DriverStore')
            result.update(published_inf=str(published),store_inf=str(store),setupapi=str(self.system/'setupapi.dll'))
            return result
        finally:self.require(self.api.SetupDiDestroyDeviceInfoList(devices),'DestroyDeviceInfoList')
    def version_fields(self,inf):
        error_line=W.UINT();handle=self.api.SetupOpenInfFileW(str(inf),None,2,C.byref(error_line));self.require(handle!=C.c_void_p(-1).value,'OpenInfFile')
        values={}
        try:
            for key in ('Signature','Class','ClassGuid','Provider','DriverVer','CatalogFile','CatalogFile.NT','CatalogFile.NTamd64'):
                context=InfContext()
                if self.api.SetupFindFirstLineW(handle,'Version',key,C.byref(context)):
                    fields=[]
                    for index in (1,2):
                        text=C.create_unicode_buffer(4096);needed=W.DWORD()
                        if self.api.SetupGetStringFieldW(C.byref(context),index,text,len(text),C.byref(needed)):fields.append(text.value)
                    values[key]=fields
            return values
        finally:self.api.SetupCloseInfFile(handle)

