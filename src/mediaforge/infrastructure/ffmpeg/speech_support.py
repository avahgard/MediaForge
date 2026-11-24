"""Stream precision-preserving per-channel source support, no native-rate spool."""
import array,math
from fractions import Fraction
from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.features.dub.alignment import supports,ceil,floor

class SupportScan:
    def __init__(self,rate,channels,samples,control):
        if not 8000<=rate<=192000 or not 1<=channels<=8:raise LocalAiError('speech_boundary_uncertain','Unqualified source precision/channel clock.')
        self.rate=rate;self.channels=channels;self.samples=samples;self.control=control
        self.position=0;self.zero=None;self.zeros=[];self.pending=b''
    def consume(self,raw):
        self.control.check();raw=self.pending+raw;size=self.channels*8;end=len(raw)//size*size
        self.pending=raw[end:];values=array.array('d',raw[:end])
        for start in range(0,len(values),self.channels):
            frame=values[start:start+self.channels]
            if not all(math.isfinite(n) for n in frame):raise LocalAiError('speech_boundary_uncertain','Nonfinite source reference samples.')
            zero=all(n==0 for n in frame)
            if zero and self.zero is None:self.zero=self.position
            if not zero and self.zero is not None:
                if self.zero==0 or (self.position-self.zero)*1000>=96*self.rate:self.zeros.append((self.zero,self.position))
                self.zero=None
            self.position+=1
            if self.position>self.samples or len(self.zeros)>5001:raise LocalAiError('resource_unavailable','Source support scan exceeds admitted clock/ledger.')
    def finish(self):
        if self.pending or self.position!=self.samples:raise LocalAiError('speech_boundary_uncertain','Source reference span is incomplete.')
        if self.zero is not None:self.zeros.append((self.zero,self.position))
        return dict(rate=self.rate,channels=self.channels,samples=self.samples,zeros=self.zeros)

def scan_source(audio,source,plan,track,recognition,control):
    # f64 preserves all finite decoded f32/f64 and <=32-bit integer precision.
    # Unknown 64-bit integer/alternate decoder classes are not inferred safe.
    if track.codec not in ('pcm_s16le','pcm_s24le','pcm_s32le','pcm_f32le','pcm_f64le','aac','mp3','flac','opus','vorbis','alac'):
        raise LocalAiError('speech_boundary_uncertain','Decoded source precision is unqualified.')
    rate=track.sample_rate;count=plan.duration*rate;origin=plan.origin*rate
    if count.denominator!=1 or origin.denominator!=1:raise LocalAiError('speech_boundary_uncertain','Native source span mapping is not exact.')
    scanner=SupportScan(rate,track.channels,int(count),control)
    first=int(origin);begin=format(float(plan.origin),'.17g');end=format(float(plan.origin+plan.duration),'.17g')
    filters=f'aformat=sample_fmts=dbl,aresample={rate}:async=1:first_pts={first}:osf=dbl,atrim=start={begin}:end={end},asetpts=PTS-STARTPTS,apad,atrim=end_sample={int(count)}'
    audio.pipe(['-v','error','-copyts','-i',str(source),'-map',f'0:{plan.audio_index}','-vn','-sn','-dn',
        '-af',filters,'-c:a','pcm_f64le','-f','f64le','pipe:1'],control,consume=scanner.consume,
        maximum=int(count)*track.channels*8,timeout=max(300,float(plan.duration)*10),locks=(source,))
    ledger=scanner.finish()
    with recognition.open('rb') as stream:
        for a,b in ledger['zeros']:
            start=ceil(Fraction(a*16000,rate));stop=floor(Fraction(b*16000,rate))
            stream.seek(start*2);remaining=(stop-start)*2
            while remaining>0:
                control.check();raw=stream.read(min(32768,remaining))
                if not raw or any(raw):raise LocalAiError('speech_boundary_uncertain','Native/recognition silence evidence disagrees.')
                remaining-=len(raw)
    return ledger,supports(ledger['zeros'],ledger['samples'],rate)

def verify_protected_zeros(path,ledger,total,control):
    if path.stat().st_size!=total*2:raise LocalAiError('validation_failed','Aligned composition count differs.')
    with path.open('rb') as stream:
        for a,b in ledger['zeros']:
            start=floor(Fraction(a*48000,ledger['rate']));stop=ceil(Fraction(b*48000,ledger['rate']))
            stream.seek(start*2);remaining=(min(total,stop)-start)*2
            while remaining>0:
                control.check();raw=stream.read(min(32768,remaining))
                if not raw or any(raw):raise LocalAiError('timing_unfit','Audio/filter energy invades confirmed source silence.')
                remaining-=len(raw)
