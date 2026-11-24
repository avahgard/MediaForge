"""Owned sequential local workers: suspended Job admission and bounded IPC."""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
import time

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.infrastructure.ffmpeg.preview_job import Limits, ThreadEntry
from mediaforge.infrastructure.images.resources import _Memory, _Process
from mediaforge.infrastructure.models.native_guard import NativeGate


class CpuJob:
    """Inference-specific owner; PreviewJobs is not used as a model launcher."""
    def __init__(self,limit=2*1024**3):
        self.process=None;self.job=None;self.limit=limit;self.native=None
        self.api=C.WinDLL('kernel32',use_last_error=True)
        self.ps=C.WinDLL('psapi',use_last_error=True)
        signatures={
            'CreateJobObjectW':([C.c_void_p,W.LPCWSTR],W.HANDLE),
            'SetInformationJobObject':([W.HANDLE,C.c_int,C.c_void_p,W.DWORD],W.BOOL),
            'QueryInformationJobObject':([W.HANDLE,C.c_int,C.c_void_p,W.DWORD,C.c_void_p],W.BOOL),
            'AssignProcessToJobObject':([W.HANDLE,W.HANDLE],W.BOOL),
            'CloseHandle':([W.HANDLE],W.BOOL),
            'GlobalMemoryStatusEx':([C.c_void_p],W.BOOL),
            'GetCurrentProcess':([],W.HANDLE),
            'GetProcessAffinityMask':([W.HANDLE,C.c_void_p,C.c_void_p],W.BOOL),
            'CreateToolhelp32Snapshot':([W.DWORD,W.DWORD],W.HANDLE),
            'Thread32First':([W.HANDLE,C.POINTER(ThreadEntry)],W.BOOL),
            'Thread32Next':([W.HANDLE,C.POINTER(ThreadEntry)],W.BOOL),
            'OpenThread':([W.DWORD,W.BOOL,W.DWORD],W.HANDLE),
            'ResumeThread':([W.HANDLE],W.DWORD),
            'GetProcessTimes':([W.HANDLE,C.c_void_p,C.c_void_p,C.c_void_p,C.c_void_p],W.BOOL),
        }
        for name,(args,result) in signatures.items():
            method=getattr(self.api,name);method.argtypes=args;method.restype=result
        self.ps.GetProcessMemoryInfo.argtypes=[W.HANDLE,C.c_void_p,W.DWORD]
        self.ps.GetProcessMemoryInfo.restype=W.BOOL
        self.metrics={'rss_peak':0,'commit_peak':0,'threads_peak':0,'cpu_seconds':0.0}

    def require(self,value):
        if not value:raise LocalAiError('resource_unavailable',f'Windows worker ownership refused ({C.get_last_error()}).')

    def threads(self):
        handle=self.api.CreateToolhelp32Snapshot(4,0)
        self.require(handle!=C.c_void_p(-1).value)
        try:
            row=ThreadEntry();row.size=C.sizeof(row);found=[]
            ok=self.api.Thread32First(handle,C.byref(row))
            while ok:
                if row.pid==self.process.pid:found.append(row.id)
                row.size=C.sizeof(row);ok=self.api.Thread32Next(handle,C.byref(row))
            return found
        finally:self.require(self.api.CloseHandle(handle))

    def spawn(self,command,*,runtime_guard=None,**options):
        core=getattr(sys,'mediaforge_core_guard',None)
        if core is None:self.native=NativeGate(runtime_guard)
        else:
            # Portable-only stronger TCB closure; accepted host/C2 behavior is
            # unchanged when no authenticated deployment capability exists.
            self.native=core.native(runtime_guard)
            options['env']=core.environment(options.get('env',{}))
        memory=_Memory();memory.length=C.sizeof(memory)
        self.require(self.api.GlobalMemoryStatusEx(C.byref(memory)))
        physical=max(0,memory.avail_phys-max(2*1024**3,memory.total_phys*15//100)-64*1024**2)
        commit=max(0,memory.avail_commit-max(2*1024**3,memory.total_commit//10)-64*1024**2)
        limit=min(self.limit,physical//4,commit//4)
        if limit<768*1024**2:raise LocalAiError('resource_unavailable','Insufficient host memory/commit headroom for the qualified local-model minimum.')
        mask=C.c_size_t();system=C.c_size_t()
        self.require(self.api.GetProcessAffinityMask(self.api.GetCurrentProcess(),C.byref(mask),C.byref(system)))
        bits=[1<<i for i in range(C.sizeof(mask)*8) if mask.value&(1<<i)][:4]
        if not bits:raise LocalAiError('resource_unavailable','No admitted CPU affinity.')
        job=self.api.CreateJobObjectW(None,None);self.require(job)
        self.job=job
        limits=Limits();limits.basic.flags=0x2000|0x100|0x200|0x8|0x10
        limits.basic.active=1;limits.basic.affinity=sum(bits)
        limits.process_memory=limits.job_memory=limit
        self.require(self.api.SetInformationJobObject(self.job,9,C.byref(limits),C.sizeof(limits)))
        verified=Limits()
        self.require(self.api.QueryInformationJobObject(self.job,9,C.byref(verified),C.sizeof(verified),None))
        self.require(verified.basic.active==1 and verified.job_memory==limit and verified.process_memory==limit
                     and verified.basic.affinity==sum(bits) and verified.basic.flags&limits.basic.flags==limits.basic.flags)
        self.metrics.update(memory_limit=limit,cpu_affinity=sum(bits),cpu_slots=len(bits))
        # Windows otherwise selects its diagnostic debug heap at process birth,
        # making ORT graph construction orders of magnitude slower. Keep the
        # normal retail allocator; this does not detach image admission or alter
        # the Job, CPU/commit ceilings, or import/object authentication.
        options['env']=dict(options['env'],_NO_DEBUG_HEAP='1')
        options['creationflags']=subprocess.CREATE_NO_WINDOW|0x4|0x2 # suspended, DEBUG_ONLY_THIS_PROCESS
        self.process=subprocess.Popen(command,**options)
        self.native.arm()
        self.require(self.api.AssignProcessToJobObject(self.job,int(self.process._handle)))
        ids=self.threads()
        if len(ids)!=1:raise LocalAiError('lifecycle_failed','Suspended worker primary thread is ambiguous.')
        thread=self.api.OpenThread(2,False,ids[0]);self.require(thread)
        try:self.require(self.api.ResumeThread(thread)==1)
        finally:self.require(self.api.CloseHandle(thread))
        self.metrics['pid']=self.process.pid
        return self.process

    def sample(self,*,enforce=True):
        if self.process is None:return
        process=_Process();process.cb=C.sizeof(process)
        if self.ps.GetProcessMemoryInfo(int(self.process._handle),C.byref(process),process.cb):
            self.metrics['rss_peak']=max(self.metrics['rss_peak'],process.peak_ws)
            self.metrics['commit_peak']=max(self.metrics['commit_peak'],process.peak_commit)
        self.metrics['threads_peak']=max(self.metrics['threads_peak'],len(self.threads()))
        if enforce and self.metrics['threads_peak']>64:raise LocalAiError('resource_unavailable','Local worker exceeded its bounded thread policy.')
        values=[C.c_uint64() for _ in range(4)]
        if self.api.GetProcessTimes(int(self.process._handle),*(C.byref(v) for v in values)):
            self.metrics['cpu_seconds']=(values[2].value+values[3].value)/10_000_000

    def close(self):
        telemetry_error=None
        try:
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.terminate()
                    deadline=time.monotonic()+2
                    while self.process.poll() is None and time.monotonic()<deadline:
                        self.native.pump(self.process,retiring=True);time.sleep(.01)
                    if self.process.poll() is None:
                        self.process.kill()
                        deadline=time.monotonic()+2
                        while self.process.poll() is None and time.monotonic()<deadline:
                            self.native.pump(self.process,retiring=True);time.sleep(.01)
                    self.process.wait(timeout=.1)
                if self.native is not None:self.native.pump(self.process,retiring=True)
                deadline=time.monotonic()+2
                while self.native is not None and not self.native.exited and time.monotonic()<deadline:
                    self.native.pump(self.process,retiring=True);time.sleep(.01)
                try:self.sample(enforce=False)
                except Exception as error:telemetry_error=error
                for pipe in (self.process.stdout,self.process.stderr):
                    if pipe is not None:pipe.close()
            if self.job is not None:
                limits=Limits()
                if self.api.QueryInformationJobObject(self.job,9,C.byref(limits),C.sizeof(limits),None):
                    self.metrics['job_commit_peak']=limits.peak_job
                else:telemetry_error=OSError('Final Job telemetry unavailable')
                self.require(self.api.CloseHandle(self.job));self.job=None
            if self.process is not None:
                self.process._handle.Close()
            if self.native is not None:
                self.metrics['native_images']=self.native.images
                self.metrics['debug_events']=self.native.events
                self.native.close()
        except BaseException as error:
            raise LocalAiError('lifecycle_failed','Local worker ownership is unresolved.',retained_resources=(self,)) from error
        if telemetry_error is not None:
            raise LocalAiError('resource_unavailable','Worker was reaped and handles closed, but final resource telemetry failed.') from telemetry_error


def run_worker(command,*,paths,environment,control,timeout=180,limit=2*1024**3,on_started=None,on_phase=None,runtime_guard=None):
    owner=CpuJob(limit);process=None;readers=[];out=bytearray();diagnostic=bytearray();oversized=[]
    def read(stream,target,bound):
        try:
            while block:=stream.read(4096):
                available=max(0,bound-len(target));target.extend(block[:available])
                if len(block)>available:oversized.append(True)
        except Exception as error:oversized.append(error)
    def stderr():
        try:
            while line:=process.stderr.readline(4096):
                if line.startswith(b'MF_PHASE '):
                    if on_phase:on_phase(line[9:].decode('ascii').strip())
                else:
                    available=max(0,8192-len(diagnostic));diagnostic.extend(line[:available])
                    if len(line)>available:oversized.append(True)
        except Exception as error:oversized.append(error)
    started=time.monotonic()
    try:
        with control.starting():
            process=owner.spawn(command,runtime_guard=runtime_guard,cwd=paths.temporary,env=environment,stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,shell=False)
        readers=[Thread(target=read,args=(process.stdout,out,5*1024*1024),daemon=True),
                 Thread(target=stderr,daemon=True)]
        for reader in readers:reader.start()
        if on_started:on_started(process.pid)
        while process.poll() is None:
            control.check();owner.native.pump(process);owner.sample()
            if oversized:raise LocalAiError('invalid_output','Worker IPC exceeds the bounded contract.')
            if time.monotonic()-started>timeout:raise LocalAiError('provider_timeout','Local inference exceeded its deadline.')
            time.sleep(.05)
        owner.native.pump(process)
        for reader in readers:reader.join(2)
        if any(reader.is_alive() for reader in readers):raise LocalAiError('lifecycle_failed','Worker IPC reader remains active.')
        control.check();owner.sample()
        if oversized:raise LocalAiError('invalid_output','Invalid bounded IPC.')
        try:result=json.loads(out)
        except (ValueError,UnicodeDecodeError) as error:
            raise LocalAiError('provider_failed',f'Worker exited {process.returncode}; invalid result: {diagnostic.decode(errors="replace")[:1024]}') from error
        if process.returncode or not result.get('ok'):
            raise LocalAiError('provider_failed',str(result.get('detail','Local model failed.'))[:1024])
        owner.metrics['elapsed_seconds']=time.monotonic()-started
        result['metrics']=owner.metrics
        return result
    except OSError as error:
        raise LocalAiError('start_failed','Local worker runtime could not be started.') from error
    finally:
        try:
            owner.close()
            for reader in readers:reader.join(2)
            if any(reader.is_alive() for reader in readers):
                raise LocalAiError('lifecycle_failed','Local IPC reader could not be reaped.',retained_resources=(owner,*readers))
        except BaseException as error:
            if getattr(error,'retained_resources',()):control.retain_unreaped_child()
            raise
