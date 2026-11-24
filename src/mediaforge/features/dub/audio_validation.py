"""Streaming published-audio comparison with the authenticated composition.

Exact internal silence remains an upstream requirement. Lossy codec ringing is
tested separately in guarded pause centers, never confused with exact PCM zeros.
"""
import math

from mediaforge.features.dub.speech import TimingError


class AudioSyncValidation:
    def __init__(self,protected,samples,*,lossless):
        if type(samples) is not int or samples<=0 or len(protected)>10002:
            raise TimingError('validation_failed','Invalid bounded silence validation ledger.')
        previous=0;self.intervals=[]
        for a,b in protected:
            if any(type(v) is not int for v in (a,b)) or not 0<=previous<=a<b<=samples:
                raise TimingError('validation_failed','Unordered protected audio validation clock.')
            previous=b
            # AAC/MP3 synthesis-filter ringing at the edge is evaluated by the
            # native codec fixture gate. Check remaining centers, never shift
            # speech or erase waveform bytes to obtain a silent measurement.
            guard=0 if lossless else 1152 # 24 ms, fixed for these pinned encoders
            if b-a>2*guard:self.intervals.append((a+guard,b-guard))
        self.position=0;self.cursor=0;self.minimum_correlation=1.
        self.quiet_samples=0;self.quiet_energy=0.;self.quiet_peak=0;self.active_blocks=0

    def consume(self,reference,decoded):
        import numpy as np
        if len(reference)!=len(decoded) or len(decoded)%2 or len(decoded)>32768:
            raise TimingError('validation_failed','Decoded/reference audio block clocks differ.')
        original=np.frombuffer(reference,dtype='<i2').astype(np.float64)
        actual=np.frombuffer(decoded,dtype='<i2').astype(np.float64)
        if len(original):
            a=float(original@original);b=float(actual@actual)
            if a>len(original)*64**2:
                correlation=float(original@actual)/math.sqrt(a*b) if b>0 else 0.
                self.minimum_correlation=min(self.minimum_correlation,correlation);self.active_blocks+=1
                if correlation<.90:
                    raise TimingError('validation_failed','Published audio differs from its source-clock composition; no timing-shift fallback.')
        start=self.position;end=start+len(actual)
        while self.cursor<len(self.intervals) and self.intervals[self.cursor][1]<=start:self.cursor+=1
        index=self.cursor
        while index<len(self.intervals) and self.intervals[index][0]<end:
            a,b=self.intervals[index];clip=actual[max(0,a-start):min(len(actual),b-start)]
            if len(clip):
                peak=int(np.max(np.abs(clip)));energy=float(clip@clip)
                self.quiet_peak=max(self.quiet_peak,peak);self.quiet_energy+=energy;self.quiet_samples+=len(clip)
                if peak>64 or energy>len(clip)*16**2:
                    raise TimingError('validation_failed','Published codec energy invades a protected pause center.')
            index+=1
        self.position=end

    def receipt(self):
        return dict(policy='published-audio-v1-reference-corr90-block16384-quiet24ms-peak64-rms16',
            active_blocks=self.active_blocks,minimum_correlation=self.minimum_correlation,
            protected_center_samples=self.quiet_samples,protected_center_peak=self.quiet_peak,
            protected_center_rms=math.sqrt(self.quiet_energy/self.quiet_samples) if self.quiet_samples else None)
