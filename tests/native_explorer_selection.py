"""Test-only Windows Shell observation of Explorer's actual selected paths."""
import ctypes as c
from ctypes import wintypes as w


class GUID(c.Structure):
    _fields_=[('a',w.DWORD),('b',w.WORD),('d',w.WORD),('e',c.c_ubyte*8)]


class Value(c.Union):
    _fields_=[('number',c.c_long),('integer',c.c_longlong),('pointer',c.c_void_p),('record',c.c_void_p*2)]


class Variant(c.Structure):
    _fields_=[('vt',w.WORD),('r1',w.WORD),('r2',w.WORD),('r3',w.WORD),('value',Value)]


class Params(c.Structure):
    _fields_=[('args',c.POINTER(Variant)),('named',c.POINTER(c.c_long)),('count',w.UINT),('named_count',w.UINT)]


ole=c.OleDLL('ole32');aut=c.OleDLL('oleaut32')
aut.SysAllocString.argtypes=[c.c_wchar_p];aut.SysAllocString.restype=c.c_void_p
aut.VariantClear.argtypes=[c.POINTER(Variant)]
null=GUID()


class Dispatch:
    def __init__(self,pointer): self.pointer=c.c_void_p(pointer)
    def method(self,index,result,*arguments):
        table=c.cast(self.pointer,c.POINTER(c.POINTER(c.c_void_p))).contents
        return c.WINFUNCTYPE(result,c.c_void_p,*arguments)(table[index])
    def invoke(self,name,*args,method=False):
        names=(c.c_wchar_p*1)(name);identity=c.c_long()
        self.method(5,c.HRESULT,c.POINTER(GUID),c.POINTER(c.c_wchar_p),w.UINT,w.DWORD,c.POINTER(c.c_long))(
            self.pointer,c.byref(null),names,1,0,c.byref(identity))
        values=(Variant*len(args))()
        for i,arg in enumerate(reversed(args)):
            if isinstance(arg,int): values[i].vt=3;values[i].value.number=arg
            else: values[i].vt=8;values[i].value.pointer=aut.SysAllocString(str(arg))
        params=Params(values,None,len(args),0);result=Variant()
        try:
            self.method(6,c.HRESULT,c.c_long,c.POINTER(GUID),w.DWORD,w.WORD,c.POINTER(Params),c.POINTER(Variant),c.c_void_p,c.c_void_p)(
                self.pointer,identity,c.byref(null),0,1 if method else 2,c.byref(params),c.byref(result),None,None)
            if result.vt==9:
                dispatch=Dispatch(result.value.pointer);result.vt=0;return dispatch
            if result.vt==8: return c.wstring_at(result.value.pointer) if result.value.pointer else ''
            if result.vt in (3,19,22,23): return result.value.number
            if result.vt in (20,21): return result.value.integer
            return None
        finally:
            for value in values: aut.VariantClear(c.byref(value))
            aut.VariantClear(c.byref(result))
    def close(self):
        if self.pointer:
            self.method(2,w.ULONG)(self.pointer);self.pointer=None
    def __del__(self): self.close()


class ExplorerSelectionObserver:
    def __init__(self):
        self._uninitialize=False
        try:
            ole.CoInitializeEx(None,2);self._uninitialize=True
        except OSError: pass  # Qt may already own a different COM apartment.
        clsid=GUID();iid=GUID();pointer=c.c_void_p()
        ole.CLSIDFromProgID('Shell.Application',c.byref(clsid))
        ole.IIDFromString('{00020400-0000-0000-C000-000000000046}',c.byref(iid))
        ole.CoCreateInstance(c.byref(clsid),None,1,c.byref(iid),c.byref(pointer))
        self.shell=Dispatch(pointer.value)
    def selections(self):
        windows=self.shell.invoke('Windows',method=True);results=[]
        try:
            for index in range(windows.invoke('Count')):
                browser=windows.invoke('Item',index,method=True)
                try:
                    document=browser.invoke('Document');selected=document.invoke('SelectedItems',method=True)
                    paths=[]
                    for i in range(selected.invoke('Count')):
                        item=selected.invoke('Item',i,method=True)
                        try: paths.append(item.invoke('Path'))
                        finally: item.close()
                    results.append({'hwnd':browser.invoke('HWND'),'paths':paths})
                    selected.close();document.close()
                except OSError: pass
                finally: browser.close()
        finally: windows.close()
        return results
    def close_new(self,hwnd):
        windows=self.shell.invoke('Windows',method=True)
        try:
            for index in range(windows.invoke('Count')):
                browser=windows.invoke('Item',index,method=True)
                try:
                    if browser.invoke('HWND')==hwnd: browser.invoke('Quit',method=True)
                finally: browser.close()
        finally: windows.close()
    def close(self):
        self.shell.close()
        if self._uninitialize: ole.CoUninitialize()
