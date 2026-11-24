"""Finite external watchdog; actual Python child is assigned to a kill-on-close Job."""
import sys,os,time,json,ctypes,subprocess
from pathlib import Path
def run(script,root,args,seconds=60):
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    class Basic(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',ctypes.c_uint32),('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',ctypes.c_uint32),('affinity',ctypes.c_size_t),('priority',ctypes.c_uint32),('scheduling',ctypes.c_uint32)]
    class Limits(ctypes.Structure):
        _fields_=[('basic',Basic),('io',ctypes.c_uint64*6),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    api.CreateJobObjectW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p];api.CreateJobObjectW.restype=ctypes.c_void_p
    api.SetInformationJobObject.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32];api.SetInformationJobObject.restype=ctypes.c_int
    api.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32];api.OpenProcess.restype=ctypes.c_void_p
    api.AssignProcessToJobObject.argtypes=[ctypes.c_void_p,ctypes.c_void_p];api.AssignProcessToJobObject.restype=ctypes.c_int
    api.CloseHandle.argtypes=[ctypes.c_void_p];api.CloseHandle.restype=ctypes.c_int
    job=api.CreateJobObjectW(None,None);assert job
    limits=Limits();limits.basic.flags=0x2000
    assert api.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits))
    process=None;handle=None;result={}
    with (root/'child.log').open('x',encoding='utf-8') as log:
        try:
            process=subprocess.Popen([sys.executable,'-B',str(script),str(root),*args],stdout=log,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+8
            while not (root/'ready.json').exists() and time.monotonic()<deadline and process.poll() is None:time.sleep(.01)
            ready=json.loads((root/'ready.json').read_text());handle=api.OpenProcess(0x101,False,ready['pid']);assert handle
            assert api.AssignProcessToJobObject(job,handle),ctypes.get_last_error()
            (root/'go').write_text('Assigned actual child to watchdog Job')
            try:result['exit']=process.wait(timeout=seconds)
            except subprocess.TimeoutExpired:result.update(exit=124,timeout_seconds=seconds)
        finally:
            assert api.CloseHandle(job)
            if handle:assert api.CloseHandle(handle)
            if process and process.poll() is None:
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            (root/'watchdog.json').write_text(json.dumps(result,indent=2))
    return result.get('exit',125)
if __name__=='__main__':sys.exit(run(Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3:]))
