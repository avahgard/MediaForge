"""Suspended Windows child admission; whole-job CPU commit, no breakaway."""
import ctypes as C
from ctypes import wintypes as W
import subprocess


class Basic(C.Structure):
    _fields_=[('process_time',C.c_int64),('job_time',C.c_int64),('flags',W.DWORD),
              ('minimum_ws',C.c_size_t),('maximum_ws',C.c_size_t),('active',W.DWORD),
              ('affinity',C.c_size_t),('priority',W.DWORD),('scheduling',W.DWORD)]


class Io(C.Structure):
    _fields_=[(name,C.c_uint64) for name in ('read','write','other','read_bytes','write_bytes','other_bytes')]


class Limits(C.Structure):
    _fields_=[('basic',Basic),('io',Io),('process_memory',C.c_size_t),('job_memory',C.c_size_t),
              ('peak_process',C.c_size_t),('peak_job',C.c_size_t)]


class ThreadEntry(C.Structure):
    _fields_=[('size',W.DWORD),('usage',W.DWORD),('id',W.DWORD),('pid',W.DWORD),
              ('priority',W.LONG),('delta',W.LONG),('flags',W.DWORD)]


class PreviewJobs:
    def __init__(self,limit,admission=None):
        self.limit=limit;self.jobs=[];self.peak=0;self.processes=[];self.admission=admission
        self.applied=[]
        self.api=C.WinDLL('kernel32',use_last_error=True)
        signatures={
            'CreateJobObjectW':([C.c_void_p,W.LPCWSTR],W.HANDLE),
            'SetInformationJobObject':([W.HANDLE,C.c_int,C.c_void_p,W.DWORD],W.BOOL),
            'QueryInformationJobObject':([W.HANDLE,C.c_int,C.c_void_p,W.DWORD,C.c_void_p],W.BOOL),
            'AssignProcessToJobObject':([W.HANDLE,W.HANDLE],W.BOOL),
            'IsProcessInJob':([W.HANDLE,W.HANDLE,C.POINTER(W.BOOL)],W.BOOL),
            'CloseHandle':([W.HANDLE],W.BOOL),
            'CreateToolhelp32Snapshot':([W.DWORD,W.DWORD],W.HANDLE),
            'Thread32First':([W.HANDLE,C.POINTER(ThreadEntry)],W.BOOL),
            'Thread32Next':([W.HANDLE,C.POINTER(ThreadEntry)],W.BOOL),
            'OpenThread':([W.DWORD,W.BOOL,W.DWORD],W.HANDLE),
            'ResumeThread':([W.HANDLE],W.DWORD),
        }
        for name,(args,result) in signatures.items():
            method=getattr(self.api,name);method.argtypes=args;method.restype=result

    def require(self,ok):
        if not ok:raise C.WinError(C.get_last_error())

    def __call__(self,command,**options):
        if self.admission is not None:self.limit=self.admission()
        job=self.api.CreateJobObjectW(None,None);self.require(job)
        self.jobs.append(job)
        limits=Limits();limits.basic.flags=0x2000|0x100|0x200|0x8;limits.basic.active=1
        limits.process_memory=limits.job_memory=self.limit
        self.require(self.api.SetInformationJobObject(job,9,C.byref(limits),C.sizeof(limits)))
        verified=Limits();self.require(self.api.QueryInformationJobObject(job,9,C.byref(verified),C.sizeof(verified),None))
        self.require(verified.job_memory==self.limit and verified.process_memory==self.limit
                     and verified.basic.flags&limits.basic.flags==limits.basic.flags and verified.basic.active==1)
        self.applied.append(self.limit)
        options['creationflags']=options.get('creationflags',0)|0x00000004|subprocess.CREATE_NO_WINDOW
        process=subprocess.Popen(command,**options)
        self.processes.append(process)
        try:
            self.require(self.api.AssignProcessToJobObject(job,int(process._handle)))
            inside=W.BOOL();self.require(self.api.IsProcessInJob(int(process._handle),job,C.byref(inside)) and inside.value)
            snapshot=self.api.CreateToolhelp32Snapshot(4,0)
            self.require(snapshot!=C.c_void_p(-1).value)
            try:
                row=ThreadEntry();row.size=C.sizeof(row);ok=self.api.Thread32First(snapshot,C.byref(row));ids=[]
                while ok:
                    if row.pid==process.pid:ids.append(row.id)
                    row.size=C.sizeof(row);ok=self.api.Thread32Next(snapshot,C.byref(row))
                if len(ids)!=1:raise RuntimeError('Suspended preview child thread ownership is ambiguous.')
                thread=self.api.OpenThread(2,False,ids[0]);self.require(thread)
                try:self.require(self.api.ResumeThread(thread)==1)
                finally:self.require(self.api.CloseHandle(thread))
            finally:self.require(self.api.CloseHandle(snapshot))
            return process
        except BaseException:
            process.terminate();process.wait(timeout=2)
            for pipe in (process.stdout,process.stderr):
                if pipe is not None:pipe.close()
            raise

    def close(self):
        if any(p.poll() is None for p in self.processes):
            raise RuntimeError('Preview Job still owns an unreaped primary child.')
        failed=[]
        close_error = None
        for job in self.jobs:
            limits=Limits()
            self.require(self.api.QueryInformationJobObject(job,9,C.byref(limits),C.sizeof(limits),None))
            self.peak=max(self.peak,limits.peak_job)
            if not self.api.CloseHandle(job):
                if close_error is None:
                    close_error = C.WinError(C.get_last_error())
                failed.append(job)
        self.jobs=failed
        if close_error is not None:
            raise RuntimeError(f'Preview Job handle cleanup failed: {close_error}')
