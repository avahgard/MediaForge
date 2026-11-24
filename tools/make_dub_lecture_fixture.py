"""Native SAPI fixture generator; not a MediaForge synthesis provider.

Uses already installed Windows English voice COM APIs directly. No shell,
execution-policy changes, package installs or additional voice acquisition.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import html
import json
from pathlib import Path
import time
import wave


class Guid(C.Structure):
    _fields_ = [('a',W.DWORD),('b',W.WORD),('c',W.WORD),('d',C.c_ubyte*8)]


class Value(C.Union):
    _fields_ = [('integer',C.c_longlong),('pointer',C.c_void_p),('pair',C.c_void_p*2)]


class Variant(C.Structure):
    _fields_ = [('vt',W.WORD),('r1',W.WORD),('r2',W.WORD),('r3',W.WORD),('value',Value)]


class Params(C.Structure):
    _fields_ = [('args',C.POINTER(Variant)),('named',C.POINTER(C.c_long)),('count',W.UINT),('named_count',W.UINT)]


def require(status):
    if status < 0:
        raise OSError('Windows SAPI fixture call refused: '+hex(status&0xffffffff))


class Dispatch:
    def __init__(self, pointer):
        self.pointer=pointer

    def function(self,index,result,args):
        table=C.cast(self.pointer,C.POINTER(C.POINTER(C.c_void_p))).contents
        return C.WINFUNCTYPE(result,C.c_void_p,*args)(table[index])

    @classmethod
    def create(cls,progid,ole):
        identifier=Guid();interface=Guid();pointer=C.c_void_p()
        require(ole.CLSIDFromProgID(progid,C.byref(identifier)))
        require(ole.CLSIDFromString('{00020400-0000-0000-C000-000000000046}',C.byref(interface)))
        require(ole.CoCreateInstance(C.byref(identifier),None,1,C.byref(interface),C.byref(pointer)))
        return cls(pointer.value)

    def invoke(self,name,*args,flag=1):
        empty=Guid();names=(W.LPCWSTR*1)(name);identifier=C.c_long()
        get=self.function(5,C.c_long,[C.POINTER(Guid),C.POINTER(W.LPCWSTR),W.UINT,W.DWORD,C.POINTER(C.c_long)])
        require(get(self.pointer,C.byref(empty),names,1,0,C.byref(identifier)))
        values=(Variant*len(args))();strings=[]
        automation=C.WinDLL('oleaut32',winmode=0x800)
        automation.SysAllocString.argtypes=[W.LPCWSTR];automation.SysAllocString.restype=C.c_void_p
        automation.SysFreeString.argtypes=[C.c_void_p]
        automation.VariantClear.argtypes=[C.POINTER(Variant)]
        result=Variant();named=C.c_long(-3)
        try:
            for value,row in zip(reversed(args),values):
                if isinstance(value,Dispatch):row.vt=9;row.value.pointer=value.pointer
                elif isinstance(value,bool):row.vt=11;row.value.integer=-1 if value else 0
                elif isinstance(value,int):row.vt=3;row.value.integer=value
                elif isinstance(value,str):
                    row.vt=8;row.value.pointer=automation.SysAllocString(value)
                    if not row.value.pointer:raise MemoryError('SAPI fixture argument allocation failed.')
                    strings.append(row.value.pointer)
                else:raise ValueError('Unsupported fixture COM argument.')
            params=Params(values,C.pointer(named) if flag in (4,8) else None,len(args),1 if flag in (4,8) else 0)
            call=self.function(6,C.c_long,[C.c_long,C.POINTER(Guid),W.DWORD,W.WORD,C.POINTER(Params),C.POINTER(Variant),C.c_void_p,C.c_void_p])
            require(call(self.pointer,identifier,C.byref(empty),0,flag,C.byref(params),C.byref(result),None,None))
            if result.vt==9:
                output=Dispatch(result.value.pointer);result.vt=0
                return output
            if result.vt==3:return C.c_int(result.value.integer&0xffffffff).value
            if result.vt==8:return C.wstring_at(result.value.pointer)
            return None
        finally:
            automation.VariantClear(C.byref(result))
            for pointer in strings:automation.SysFreeString(pointer)

    def close(self):
        if self.pointer is not None:
            self.function(2,W.ULONG,[])(self.pointer);self.pointer=None


PARTS=(
    'Today we will learn how a computer stores information.',
    'A file contains data. A folder helps us organize related files.',
    'Before changing an important document, make a backup copy.',
    'The original source should remain unchanged while we prepare the result.',
    'We can check the result by opening it and comparing the important details.',
    'If a calculation fails, stop and read the error before trying again.',
)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--case',choices=('short','minute','moderate','hour'),default='short')
    args=parser.parse_args()
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths,local_path
    paths=ProfilePaths.for_test(args.root);paths.activate_temporary()
    target=local_path(args.output)
    if not target.is_relative_to(paths.root/'fixtures') or target.exists() or not target.parent.is_dir():
        parser.error('New explicit fixture WAV under this portable root required.')
    ole=C.OleDLL('ole32')
    ole.CoInitializeEx.argtypes=[C.c_void_p,W.DWORD];ole.CoInitializeEx.restype=C.c_long
    ole.CLSIDFromProgID.argtypes=[W.LPCWSTR,C.POINTER(Guid)]
    ole.CLSIDFromString.argtypes=[W.LPCWSTR,C.POINTER(Guid)]
    ole.CoCreateInstance.argtypes=[C.POINTER(Guid),C.c_void_p,W.DWORD,C.POINTER(Guid),C.POINTER(C.c_void_p)]
    require(ole.CoInitializeEx(None,2))
    owners=[];opened=False;started=time.monotonic()
    try:
        voice=Dispatch.create('SAPI.SpVoice',ole);owners.append(voice)
        stream=Dispatch.create('SAPI.SpFileStream',ole);owners.append(stream)
        tokens=voice.invoke('GetVoices','Language=409','');owners.append(tokens)
        if tokens.invoke('Count',flag=2)<1:raise ValueError('No installed English SAPI fixture voice; no automatic installation.')
        token=tokens.invoke('Item',0);owners.append(token)
        description=token.invoke('GetDescription',0)
        voice.invoke('Voice',token,flag=8);voice.invoke('Rate',-2,flag=4)
        format_=stream.invoke('Format',flag=2);owners.append(format_)
        format_.invoke('Type',18,flag=4) # SAFT16kHz16BitMono
        stream.invoke('Open',str(target),3,False);opened=True
        voice.invoke('AudioOutputStream',stream,flag=8)
        voice.invoke('Speak','<sapi><silence msec="2000"/></sapi>',8)
        seconds={'short':0,'minute':60,'moderate':600,'hour':3600}[args.case]
        count=0
        while True:
            text=PARTS[count%len(PARTS)]
            voice.invoke('Speak','<sapi>'+html.escape(text)+'<silence msec="1500"/></sapi>',8)
            count+=1
            size=target.stat().st_size
            if size>126*1024**2 or count>800 or time.monotonic()-started>600:
                raise ValueError('Fixture generation exceeded its fixed bound.')
            if args.case=='short' or size/32000 >= seconds-2:break
        voice.invoke('Speak','<sapi><silence msec="2000"/></sapi>',8)
        stream.invoke('Close');opened=False
        with wave.open(str(target),'rb') as reader:
            if (reader.getnchannels(),reader.getsampwidth(),reader.getframerate())!=(1,2,16000):
                raise ValueError('SAPI did not produce the declared PCM fixture format.')
            duration=reader.getnframes()/16000
        print(json.dumps(dict(kind='synthetic-educational-native-fixture',case=args.case,voice=description,
                              rate=-2,phrases=count,seconds=duration,bytes=target.stat().st_size,
                              sha256=hashlib.sha256(target.read_bytes()).hexdigest(),elapsed=time.monotonic()-started)))
    finally:
        if opened:stream.invoke('Close')
        for owner in reversed(owners):owner.close()
        ole.CoUninitialize()


if __name__=='__main__':
    main()
