"""Qwen target-process WDDM telemetry through public x64 D3DKMT APIs."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
from dataclasses import dataclass
import threading,time

class TelemetryError(RuntimeError):
    def __init__(self,code,detail,status=None,retained=()):self.code=code;self.status=status;self.retained_resources=retained;super().__init__(detail)
class Luid(C.Structure):_fields_=[('low',W.DWORD),('high',W.LONG)]
class AdapterInfo(C.Structure):_fields_=[('handle',W.UINT),('luid',Luid),('sources',W.ULONG),('precise',W.BOOL)]
class EnumAdapters(C.Structure):_fields_=[('count',W.ULONG),('adapters',C.POINTER(AdapterInfo))]
class OpenAdapter(C.Structure):_fields_=[('luid',Luid),('handle',W.UINT)]
class CloseAdapter(C.Structure):_fields_=[('handle',W.UINT)]
class AdapterQuery(C.Structure):_fields_=[('handle',W.UINT),('kind',C.c_int),('data',C.c_void_p),('size',W.UINT)]
class DeviceIds(C.Structure):_fields_=[('physical',W.UINT)]+[(n,W.UINT) for n in ('vendor','device','subvendor','subsystem','revision','bus')]
class VideoMemory(C.Structure):
    _fields_=[('process',W.HANDLE),('adapter',W.UINT),('segment',C.c_int)]+[(n,C.c_uint64) for n in ('budget','usage','reservation','available_reservation')]+[('physical',W.UINT)]

ABI={'Luid':(8,{'low':0,'high':4}),'AdapterInfo':(20,{'handle':0,'luid':4,'sources':12,'precise':16}),'EnumAdapters':(16,{'count':0,'adapters':8}),'OpenAdapter':(12,{'luid':0,'handle':8}),'CloseAdapter':(4,{'handle':0}),'AdapterQuery':(24,{'handle':0,'kind':4,'data':8,'size':16}),'DeviceIds':(28,{'physical':0,'vendor':4,'device':8,'subvendor':12,'subsystem':16,'revision':20,'bus':24}),'VideoMemory':(56,{'process':0,'adapter':8,'segment':12,'budget':16,'usage':24,'reservation':32,'available_reservation':40,'physical':48})}
def abi():
    if C.sizeof(C.c_void_p)!=8:raise TelemetryError('unsupported_abi','Windows x64 required')
    for name,(size,offsets) in ABI.items():
        kind=globals()[name]
        if C.sizeof(kind)!=size or any(getattr(kind,key).offset!=value for key,value in offsets.items()):raise TelemetryError('unsupported_abi','SDK structure mismatch '+name)
    return ABI
def ok(status,operation):
    if status!=0:raise TelemetryError('query_refused',operation+' NTSTATUS '+hex(status&0xffffffff),status&0xffffffff)

class Api:
    def __init__(self):
        abi();self.k=C.WinDLL('kernel32',use_last_error=True,winmode=0x800)
        signatures={'GetSystemDirectoryW':([W.LPWSTR,W.UINT],W.UINT),'GetCurrentProcess':([],W.HANDLE),'CloseHandle':([W.HANDLE],W.BOOL),'DuplicateHandle':([W.HANDLE,W.HANDLE,W.HANDLE,C.POINTER(W.HANDLE),W.DWORD,W.BOOL,W.DWORD],W.BOOL),'GetProcessId':([W.HANDLE],W.DWORD),'GetProcessTimes':([W.HANDLE,C.c_void_p,C.c_void_p,C.c_void_p,C.c_void_p],W.BOOL),'WaitForSingleObject':([W.HANDLE,W.DWORD],W.DWORD)}
        for name,(args,result) in signatures.items():f=getattr(self.k,name);f.argtypes=args;f.restype=result
        text=C.create_unicode_buffer(32768);n=self.k.GetSystemDirectoryW(text,len(text))
        if not 0<n<len(text):raise TelemetryError('api_unavailable','Cannot resolve System32')
        self.system=Path(text.value);self.g=C.WinDLL(str(self.system/'gdi32.dll'),use_last_error=True,winmode=0x800)
        try:
            for name,kind in [('D3DKMTEnumAdapters2',EnumAdapters),('D3DKMTOpenAdapterFromLuid',OpenAdapter),('D3DKMTCloseAdapter',CloseAdapter),('D3DKMTQueryAdapterInfo',AdapterQuery),('D3DKMTQueryVideoMemoryInfo',VideoMemory)]:
                f=getattr(self.g,name);f.argtypes=[C.POINTER(kind)];f.restype=C.c_long
        except AttributeError as e:raise TelemetryError('api_unavailable','Required public D3DKMT export absent') from e
    def duplicate(self,handle,access=0):
        out=W.HANDLE();self_handle=self.k.GetCurrentProcess()
        if not self.k.DuplicateHandle(self_handle,handle,self_handle,C.byref(out),access,False,0 if access else 2):raise TelemetryError('ownership_refused','Cannot duplicate creator-owned target handle: '+str(C.get_last_error()))
        return out.value
    def process_identity(self,handle):
        pid=self.k.GetProcessId(handle);times=[C.c_uint64() for _ in range(4)]
        if not pid or not self.k.GetProcessTimes(handle,*(C.byref(t) for t in times)):raise TelemetryError('ownership_refused','Cannot authenticate held target process: '+str(C.get_last_error()))
        return (pid,times[0].value)
    def alive(self,handle):
        result=self.k.WaitForSingleObject(handle,0)
        if result==0:raise TelemetryError('target_exited','Held target has exited')
        if result!=258:raise TelemetryError('ownership_refused','Target synchronization unavailable')
    def query_adapter(self,handle,kind,value):
        row=AdapterQuery(handle,kind,C.cast(C.byref(value),C.c_void_p),C.sizeof(value));ok(self.g.D3DKMTQueryAdapterInfo(C.byref(row)),'QueryAdapterInfo');return value
    def close_adapter(self,handle):ok(self.g.D3DKMTCloseAdapter(C.byref(CloseAdapter(handle))),'CloseAdapter')
    def select(self):
        # Fixed maximum16, no unbounded enumeration/retry. Enum owns returned
        # KMT handles; all are closed even when selection/query fails.
        rows=(AdapterInfo*16)();query=EnumAdapters(16,rows);handles=[];found=[];self.enumeration=[]
        try:
            status=self.g.D3DKMTEnumAdapters2(C.byref(query))
            handles=list(dict.fromkeys(row.handle for row in rows if row.handle))
            ok(status,'EnumAdapters2')
            if not 0<query.count<=16:raise TelemetryError('adapter_unavailable','Adapter enumeration bound')
            for row in rows[:query.count]:
                count=self.query_adapter(row.handle,30,W.UINT()).value
                record=dict(luid=(row.luid.low,row.luid.high),physical_count=count,sources=row.sources);self.enumeration.append(record)
                if count!=1:continue
                ids=self.query_adapter(row.handle,31,DeviceIds(physical=0))
                record['device_ids']={name:getattr(ids,name) for name,_ in DeviceIds._fields_}
                flags=self.query_adapter(row.handle,15,W.UINT()).value;record['adapter_type_flags']=flags
                # SDK Render+Display, non-software/non-paravirtual/non-compute
                # render adapter. EnumAdapters2 may also expose a compute-only
                # interface for the same PCI device: never choose by index.
                if (ids.vendor,ids.device)==(0x10de,0x2882) and flags&3==3 and not flags&(4|128|2048):found.append(dict(luid=(row.luid.low,row.luid.high),physical_count=count,physical=0,sources=row.sources,adapter_type_flags=flags,device_ids={name:getattr(ids,name) for name,_ in DeviceIds._fields_}))
            if len(found)!=1:raise TelemetryError('adapter_unavailable','Expected one non-linked NVIDIA RTX4060 device10de:2882')
            return found[0]
        finally:
            failed=[]
            for handle in handles:
                try:self.close_adapter(handle)
                except TelemetryError:failed.append(handle)
            if failed:raise TelemetryError('lifecycle_failed','Enum adapter handles retained',retained=((self,failed),))

@dataclass(frozen=True)
class Sample:
    identity:tuple
    luid:tuple
    physical:int
    begin_ns:int
    end_ns:int
    local:tuple
    nonlocal_:tuple

def admission(sample,expected_identity,expected_luid,future_growth,additional=0,projected_total=None,*,now_ns=None):
    now=time.perf_counter_ns() if now_ns is None else now_ns
    if sample.identity!=expected_identity or sample.luid!=expected_luid or sample.physical!=0:raise TelemetryError('identity_mismatch','Target/adapter generation differs')
    if not 0<=now-sample.end_ns<=500_000_000 or not 0<=sample.end_ns-sample.begin_ns<=500_000_000:raise TelemetryError('stale','Telemetry older than500ms or invalid QPC interval')
    if type(future_growth) is not int or future_growth<0 or type(additional) is not int or additional<0:raise TelemetryError('invalid_ledger','Invalid future/additional growth')
    budget,usage,reserved,available=sample.local
    if not budget:raise TelemetryError('budget_unknown','Zero LOCAL budget never authorizes allocation')
    ceiling=max(0,min(6*1024**3,budget-2*1024**3-future_growth));headroom=max(0,ceiling-usage)
    if usage>ceiling or additional>headroom or projected_total is not None and (type(projected_total) is not int or projected_total<usage or projected_total>ceiling):raise TelemetryError('resource_refused','Target exceeds WDDM ceiling/headroom')
    return dict(B=budget,U=usage,E=future_growth,C=ceiling,H=headroom,nonlocal_usage=sample.nonlocal_[1],nonlocal_is_vram=False)

def shared_growth(sample,baseline,qualified_allowance=0):
    if sample.identity!=baseline.identity or sample.luid!=baseline.luid:raise TelemetryError('identity_mismatch','Shared baseline belongs to another owner')
    if type(qualified_allowance) is not int or qualified_allowance<0:raise TelemetryError('invalid_ledger','Invalid shared allowance')
    growth=max(0,sample.nonlocal_[1]-baseline.nonlocal_[1])
    if growth>qualified_allowance:raise TelemetryError('nonlocal_growth_unqualified','Shared/system GPU backing grew beyond qualified phase allowance')
    return growth

class Target:
    def __init__(self,api,original,expected_identity,adapter,access=0):
        self.api=api;self.process=None;self.adapter=None;self.identity=expected_identity;self.mapping=adapter
        try:
            self.process=api.duplicate(original,access)
            if api.process_identity(self.process)!=expected_identity:raise TelemetryError('identity_mismatch','Creator handle process generation differs')
            api.alive(self.process)
            opened=OpenAdapter(Luid(*adapter['luid']),0);status=api.g.D3DKMTOpenAdapterFromLuid(C.byref(opened))
            self.adapter=opened.handle or None
            ok(status,'OpenAdapterFromLuid')
            if self.adapter is None:raise TelemetryError('adapter_unavailable','Null opened adapter')
            count=api.query_adapter(self.adapter,30,W.UINT()).value;ids=api.query_adapter(self.adapter,31,DeviceIds(physical=0))
            if count!=1 or any(getattr(ids,k)!=v for k,v in adapter['device_ids'].items()):raise TelemetryError('identity_mismatch','LUID physical node/device changed')
        except BaseException:self.close();raise
    def sample(self):
        if self.process is None or self.adapter is None:raise TelemetryError('ownership_refused','Target already closed')
        start=time.perf_counter_ns();self.api.alive(self.process)
        if self.api.process_identity(self.process)!=self.identity:raise TelemetryError('identity_mismatch','Target generation changed')
        values=[]
        for segment in (0,1):
            query=VideoMemory(self.process,self.adapter,segment,0,0,0,0,0)
            ok(self.api.g.D3DKMTQueryVideoMemoryInfo(C.byref(query)),'QueryVideoMemoryInfo segment'+str(segment))
            numbers=tuple(getattr(query,k) for k in ('budget','usage','reservation','available_reservation'))
            if any(n>2**60 for n in numbers):raise TelemetryError('inconsistent','Impossible telemetry value')
            values.append(numbers)
        self.api.alive(self.process)
        end=time.perf_counter_ns()
        if end-start>500_000_000:raise TelemetryError('stale','Query pair exceeded500ms')
        return Sample(self.identity,tuple(self.mapping['luid']),0,start,end,*values)
    def close(self):
        errors=[]
        if self.adapter is not None:
            try:self.api.close_adapter(self.adapter);self.adapter=None
            except TelemetryError as e:errors.append(str(e))
        if self.process is not None:
            if self.api.k.CloseHandle(self.process):self.process=None
            else:errors.append('CloseHandle process failed')
        if errors:raise TelemetryError('lifecycle_failed',';'.join(errors),retained=(self,))

