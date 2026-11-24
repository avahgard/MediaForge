"""Object-held stdlib Windows reads; no ACL mutation, fallback or pathname deletion."""
from pathlib import Path
import ctypes as C,hashlib,os,re
from ctypes import wintypes as W
from .mf_contracts import ProfileError
class Info(C.Structure):
    _fields_=[('attributes',W.DWORD),('created',W.FILETIME),('accessed',W.FILETIME),('written',W.FILETIME),('volume',W.DWORD),('size_hi',W.DWORD),('size_lo',W.DWORD),('links',W.DWORD),('id_hi',W.DWORD),('id_lo',W.DWORD)]
def path(value):
    if not isinstance(value,Path) or not value.is_absolute() or len(str(value))>30000 or len(value.parts)>128 or not re.fullmatch('[A-Za-z]:\\\\',value.anchor) or any(p in ('.','..') or p.endswith((' ','.')) or any(c in p for c in ':<>"|?*\0') or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',p) for p in value.parts[1:]):raise ProfileError('unsafe_path','Absolute local unambiguous parent-supplied Path required.')
    return value
class NativeFiles:
    def __init__(self,maximum=43656):
        if os.name!='nt':raise ProfileError('unsupported_platform','Windows native ownership required.')
        self.handles=[];self.directory_paths=set();self.maximum=maximum;self.api=C.WinDLL('kernel32',use_last_error=True)
        signatures={'CreateFileW':([W.LPCWSTR,W.DWORD,W.DWORD,C.c_void_p,W.DWORD,W.DWORD,W.HANDLE],W.HANDLE),
            'CloseHandle':([W.HANDLE],W.BOOL),'GetFileInformationByHandle':([W.HANDLE,C.POINTER(Info)],W.BOOL),
            'ReadFile':([W.HANDLE,C.c_void_p,W.DWORD,C.POINTER(W.DWORD),C.c_void_p],W.BOOL)}
        for name,(args,result) in signatures.items():f=getattr(self.api,name);f.argtypes=args;f.restype=result
    def info(self,h,directory=False):
        value=Info()
        if not self.api.GetFileInformationByHandle(h,C.byref(value)):raise ProfileError('io_failed','Native identity unavailable.')
        if value.attributes&0x400 or bool(value.attributes&0x10)!=directory or not directory and value.links!=1:raise ProfileError('unsafe_alias','Wrong opened type, reparse alias or hardlink.')
        return value
    def add(self,value,directory=False):
        value=path(value)
        if directory and str(value).casefold() in self.directory_paths:return
        if len(self.handles)>=self.maximum:raise ProfileError('ownership_limit','Native handle allowance exhausted before acquisition.')
        if value.lstat().st_file_attributes&0x400:raise ProfileError('unsafe_alias','Reparse path refused.')
        h=self.api.CreateFileW(str(value),0x80 if directory else 0x80000000,3 if directory else 1,None,3,0x02200000 if directory else 0x00200000,None)
        if h==C.c_void_p(-1).value:raise ProfileError('storage_refused','Native model/runtime read refused: Windows '+str(C.get_last_error()))
        self.handles.append(h);self.info(h,directory)
        if directory:self.directory_paths.add(str(value).casefold())
        return h
    def parents(self,value):
        for parent in reversed((path(value),*value.parents)):self.add(parent,True)
    def digest(self,h,size,control=None,collect=False):
        info=self.info(h)
        if (info.size_hi<<32)|info.size_lo!=size:raise ProfileError('digest_mismatch','Opened size mismatch.')
        buffer=C.create_string_buffer(1024*1024);got=W.DWORD();hashed=hashlib.sha256();count=0;parts=[]
        while True:
            if control:control()
            if not self.api.ReadFile(h,buffer,len(buffer),C.byref(got),None):raise ProfileError('io_failed','Native read failed.')
            if not got.value:break
            count+=got.value
            if count>size:raise ProfileError('digest_mismatch','Opened member grew.')
            data=buffer.raw[:got.value];hashed.update(data)
            if collect:parts.append(data)
        if count!=size:raise ProfileError('digest_mismatch','Incomplete member read.')
        return hashed.hexdigest(),b''.join(parts) if collect else None
    def close(self):
        remaining=[]
        for h in self.handles:
            try:closed=self.api.CloseHandle(h)
            except BaseException:closed=False
            if not closed:remaining.append(h)
        self.handles=remaining
        if self.handles:raise ProfileError('lifecycle_failed','Owned native handles remain unresolved.',retained_resources=(self,))
    def __enter__(self):return self
    def __exit__(self,*unused):self.close()
