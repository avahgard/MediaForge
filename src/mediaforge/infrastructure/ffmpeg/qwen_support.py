"""Bounded all-channel native quiet evidence for approved estimated admission.

The accepted hard-zero scanner still supplies exact facts. Additional 8 ms RMS
features are estimates, never represented as digital zeros or decoded audio.
At most 487500 uint16 features (<1 MiB) cover the 3900-second admission envelope.
"""
import array
import base64
import hashlib
import math

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.features.dub.alignment import supports
from mediaforge.infrastructure.ffmpeg.speech_support import SupportScan

POLICY='native-quiet-v1-allchannels-f64-maxrms8ms-u16ceil-3900s'


class NativeQuietScan(SupportScan):
    def __init__(self,*args):
        super().__init__(*args)
        if self.samples>3900*self.rate:raise LocalAiError('resource_unavailable','Native quiet span exceeds its bound.')
        self.feature_pending=b'';self.frame=0;self.bin=0;self.features=array.array('H')

    def consume(self,raw):
        import numpy as np
        super().consume(raw)
        raw=self.feature_pending+raw;size=self.channels*8;end=len(raw)//size*size
        wave=np.frombuffer(raw[:end],dtype='<f8').reshape(-1,self.channels)
        position=0
        while position<len(wave):
            self.control.check()
            boundary=((self.bin+1)*self.rate+124)//125
            need=boundary-self.frame
            if need<=0:raise LocalAiError('invalid_timing','Native feature clock differs.')
            if len(wave)-position<need:break
            block=wave[position:position+need]
            value=float(np.max(np.sqrt(np.mean(block*block,axis=0))))*32768
            if not math.isfinite(value) or value>65535:
                raise LocalAiError('speech_boundary_uncertain','Native signal range is unqualified.')
            self.features.append(math.ceil(value));self.frame=boundary;self.bin+=1;position+=need
            if len(self.features)>487500:raise LocalAiError('resource_unavailable','Native quiet features exceed their bound.')
        self.feature_pending=raw[position*size:]

    def finish(self):
        import numpy as np
        ledger=super().finish()
        if self.feature_pending:
            block=np.frombuffer(self.feature_pending,dtype='<f8').reshape(-1,self.channels)
            value=float(np.max(np.sqrt(np.mean(block*block,axis=0))))*32768
            if not math.isfinite(value) or value>65535:
                raise LocalAiError('speech_boundary_uncertain','Native signal range is unqualified.')
            self.features.append(math.ceil(value));self.bin+=1
        if self.bin!=(self.samples*125+self.rate-1)//self.rate:
            raise LocalAiError('invalid_timing','Incomplete native feature bins.')
        raw=self.features.tobytes()
        ledger['native_quiet']=dict(policy=POLICY,rate=125,count=len(self.features),
            data=base64.b64encode(raw).decode('ascii'),sha256=hashlib.sha256(raw).hexdigest())
        return ledger


def scan_source(audio,source,plan,track,recognition,control):
    if track.codec not in ('pcm_s16le','pcm_s24le','pcm_s32le','pcm_f32le','pcm_f64le','aac','mp3','flac','opus','vorbis','alac'):
        raise LocalAiError('speech_boundary_uncertain','Decoded source precision is unqualified.')
    rate=track.sample_rate;count=plan.duration*rate;origin=plan.origin*rate
    if count.denominator!=1 or origin.denominator!=1:
        raise LocalAiError('speech_boundary_uncertain','Native source span mapping is not exact.')
    scanner=NativeQuietScan(rate,track.channels,int(count),control)
    first=int(origin);begin=format(float(plan.origin),'.17g');end=format(float(plan.origin+plan.duration),'.17g')
    filters=f'aformat=sample_fmts=dbl,aresample={rate}:async=1:first_pts={first}:osf=dbl,atrim=start={begin}:end={end},asetpts=PTS-STARTPTS,apad,atrim=end_sample={int(count)}'
    audio.pipe(['-v','error','-copyts','-i',str(source),'-map',f'0:{plan.audio_index}','-vn','-sn','-dn',
        '-af',filters,'-c:a','pcm_f64le','-f','f64le','pipe:1'],control,consume=scanner.consume,
        maximum=int(count)*track.channels*8,timeout=max(300,float(plan.duration)*10),locks=(source,))
    ledger=scanner.finish()
    # Cross-rate point mapping is not a resampling-filter support mapping.
    # Authenticate every ASR byte; keep the original native zero ledger intact.
    audio.verify_extraction(source,plan,recognition,control)
    return ledger,supports(ledger['zeros'],ledger['samples'],rate)
