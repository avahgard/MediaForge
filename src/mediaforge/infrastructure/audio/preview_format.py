"""Short-preview device format conversion; never used by Dub composition/export."""
import ctypes as C
from ctypes import wintypes as W
from fractions import Fraction

from mediaforge.infrastructure.images.resources import _Memory


class PreviewAudioConverter:
    def __call__(self,pcm,source_rate,target_rate,channels,kind):
        if (not isinstance(pcm,bytes) or len(pcm)%2 or not 0<len(pcm)<=8*24000*2
                or source_rate!=24000 or not 16000<=target_rate<=192000 or channels not in (1,2)
                or kind not in ('Int16','Float','Int32','UInt8')):
            raise ValueError('The selected output device has an unsupported preview format.')
        api=C.WinDLL('kernel32',use_last_error=True)
        api.GlobalMemoryStatusEx.argtypes=[C.POINTER(_Memory)];api.GlobalMemoryStatusEx.restype=W.BOOL
        memory=_Memory();memory.length=C.sizeof(memory)
        if not api.GlobalMemoryStatusEx(C.byref(memory)):
            raise ValueError('Preview playback memory admission is unavailable.')
        reserve=96*1024**2
        if (memory.avail_phys<max(2*1024**3,memory.total_phys*15//100)+reserve
                or memory.avail_commit<max(2*1024**3,memory.total_commit//10)+reserve):
            raise ValueError('Insufficient memory for preview playback. Close other processing and retry.')
        return convert(pcm,source_rate,target_rate,channels,kind)


def convert(pcm,source_rate,target_rate,channels,kind):
    import numpy as np
    values=np.frombuffer(pcm,dtype='<i2').astype(np.float32)/32768.
    if target_rate==source_rate:mono=values
    else:
        # Fixed 65-tap windowed-sinc phases. Work in 4096-frame blocks, no
        # scipy/demo dependency, unbounded matrices or change to speech speed.
        ratio=Fraction(target_rate,source_rate);up,down=ratio.numerator,ratio.denominator
        if up>640:raise ValueError('This device sample-rate ratio is unqualified.')
        count=(2*len(values)*up+down)//(2*down)
        offsets=np.arange(-32,33,dtype=np.int64)
        positions=offsets[None,:]-np.arange(up,dtype=np.float64)[:,None]/up
        cutoff=min(1.,target_rate/source_rate)
        weights=np.sinc(positions*cutoff)*cutoff*.5*(1+np.cos(np.pi*positions/32))
        weights[np.abs(positions)>32]=0
        weights=(weights/weights.sum(axis=1)[:,None]).astype(np.float32)
        mono=np.empty(count,dtype=np.float32)
        for start in range(0,count,4096):
            clocks=np.arange(start,min(count,start+4096),dtype=np.int64)*down
            centers=clocks//up;phases=clocks%up
            indices=centers[:,None]+offsets
            valid=(indices>=0)&(indices<len(values))
            data=values[np.clip(indices,0,len(values)-1)]*valid
            mono[start:start+len(clocks)]=np.sum(data*weights[phases],axis=1)
    if not np.all(np.isfinite(mono)):raise ValueError('Nonfinite preview output refused.')
    # Device conversion may introduce a tiny interpolation overshoot. Bound
    # playback level without clipping; this never changes retained TTS bytes.
    peak=float(np.max(np.abs(mono),initial=0.))
    if peak>1:mono/=peak
    if channels==2:mono=np.repeat(mono[:,None],2,axis=1)
    if kind=='Float':data=mono.astype('<f4').tobytes()
    elif kind=='Int16':data=np.rint(mono*32767).astype('<i2').tobytes()
    elif kind=='Int32':data=np.rint(mono.astype(np.float64)*2147483647).astype('<i4').tobytes()
    else:data=np.rint((mono+1)*127.5).astype('u1').tobytes()
    if len(data)>16*1024**2:raise ValueError('Preview output exceeds its device buffer bound.')
    return data
