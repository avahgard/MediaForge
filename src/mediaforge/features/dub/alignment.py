"""Approved C3 source/VAD boundary gates; exact clocks, no signal thresholds."""
from dataclasses import dataclass
from fractions import Fraction
import math,re
from mediaforge.features.dub.speech import TimingError,Fit,half_up

POLICY='source-support-v2-f64-allchannels-zero96-vad512-grid256-core1024-hi80-lo20-run3-delta500-guard20'
VAD_SHA256='4cbf549b8326f60f80f2536d9eefeb450a9abe83365a098031c89719f1be17d2'
def refuse(message):raise TimingError('speech_boundary_uncertain',message)
def floor(x):return x.numerator//x.denominator
def ceil(x):return -floor(-x)

@dataclass(frozen=True)
class Support:
    start:Fraction # source-relative 16 kHz sample indices, possibly fractional
    end:Fraction
    native_start:int
    native_end:int
    native_rate:int

def supports(zeros,samples,rate):
    """Maximal long-zero exclusions; no nonzero sample erased as background."""
    if type(samples) is not int or samples<=0 or not 8000<=rate<=192000:refuse('Invalid source support clock.')
    prior=0;result=[]
    for start,end in zeros:
        if not 0<=prior<=start<end<=samples:refuse('Invalid source silence ledger.')
        if prior<start:result.append(Support(Fraction(prior*16000,rate),Fraction(start*16000,rate),prior,start,rate))
        prior=end
    if prior<samples:result.append(Support(Fraction(prior*16000,rate),Fraction(samples*16000,rate),prior,samples,rate))
    if len(result)>5000:raise TimingError('resource_unavailable','Source boundary ledger exceeds 5000 units.')
    return tuple(result)

def activity_runs(scores,offset,*,speech):
    if not isinstance(scores,(list,tuple)) or len(scores)>938 or any(type(p) not in (int,float) or not math.isfinite(p) or not 0<=p<=1 for p in scores):refuse('Invalid bounded VAD observations.')
    runs=[];start=None
    for i,p in enumerate(scores):
        yes=p>=.8 if speech else p<=.2
        if yes and start is None:start=i
        if not yes and start is not None:
            if i-start>=3:runs.append((offset+start*512,offset+i*512))
            start=None
    if start is not None and len(scores)-start>=3:runs.append((offset+start*512,offset+len(scores)*512))
    return tuple(runs)

def qualify(support,observations):
    """Historical v1 edge predicate, retained for the frozen refusal regression."""
    edges=[]
    if len(observations)!=2 or observations[1]['offset']-observations[0]['offset']!=256:refuse('Missing VAD grid observations.')
    for row in observations:
        runs=activity_runs(row['scores'],row['offset'],speech=True)
        matching=[(a,b) for a,b in runs if min(Fraction(b),support.end)>max(Fraction(a),support.start)]
        if not matching:refuse('No stable VAD speech interval.')
        a,b=matching[0][0],matching[-1][1]
        if abs(Fraction(a)-support.start)>1024 or abs(Fraction(b)-support.end)>1024:refuse('VAD/waveform edge disagreement exceeds 64 ms.')
        lows=activity_runs(row['scores'],row['offset'],speech=False)
        if not any(y<=a for x,y in lows) or not any(x>=b for x,y in lows):
            refuse('Missing stable non-speech bracketing.')
        if any(x>support.start+1024 and y<support.end-1024 for x,y in lows):refuse('Unresolved internal non-speech pause.')
        edges.append((a,b))
    if any(abs(edges[0][i]-edges[1][i])>512 for i in (0,1)):refuse('VAD grids disagree by more than 32 ms.')
    return tuple(edges)

def qualify_core(support,observations,zeros,*,window):
    """Amended hard-silence branch: VAD corroborates a core, never crops support."""
    if (not isinstance(observations,(list,tuple)) or len(observations)!=2
            or any(not isinstance(r,dict) or type(r.get('offset')) is not int or not isinstance(r.get('scores'),(list,tuple)) for r in observations)
            or observations[1]['offset']-observations[0]['offset']!=256):refuse('Missing/malformed VAD grid observations.')
    p,q=support.start,support.end;receipts=[]
    before=next(((a,b) for a,b in zeros if b==p),None)
    after=next(((a,b) for a,b in zeros if a==q),None)
    if before is None or after is None:refuse('Missing real bracketing hard silence.')
    for row in observations:
        scores=row['scores'];offset=row['offset']
        if (type(offset) is not int or offset not in (window[0],window[0]+256)
                or len(scores)!=(window[1]-offset)//512):refuse('Incomplete/padded activity clock.')
        highs=activity_runs(scores,offset,speech=True);lows=activity_runs(scores,offset,speech=False)
        selected=[(a,b) for a,b in highs if a<q and b>p]
        if not selected:refuse('No stable interior speech core.')
        a,b=selected[0][0],selected[-1][1]
        if a<p-512 or b>q+512:refuse('Activity connects unrelated source support.')
        gaps=[(left[1],right[0]) for left,right in zip(selected,selected[1:])]
        if any(y-x>=1536 or any(c<y and d>x for c,d in zeros) for x,y in gaps):refuse('Ambiguous interior activity topology.')
        # Test complete low bins inside support and bracketing ZERO intervals,
        # rather than requiring the endpoint of an entire maximal low run.
        def low_inside(x,y):
            run=0
            for j,v in enumerate(scores):
                lo=offset+j*512;hi=lo+512
                run=run+1 if x<=lo and hi<=y and v<=.2 else 0
                if run>=3:return True
            return False
        if low_inside(p,q):refuse('Unresolved low-confidence material inside source support.')
        if not low_inside(*before) or not low_inside(*after):refuse('Missing real low-score bracketing context.')
        receipts.append(dict(offset=offset,highs=[list(run) for run in selected],gaps=[list(run) for run in gaps],
            lows=[list(run) for run in lows],envelope=[a,b],
            brackets=[[str(n) for n in interval] for interval in (before,after)]))
    e0,e1=(r['envelope'] for r in receipts)
    if any(abs(e0[i]-e1[i])>512 for i in (0,1)):refuse('VAD grids disagree by more than 32 ms.')
    common=[max(p,e0[0],e1[0]),min(q,e0[1],e1[1])]
    if common[1]-common[0]<1024:refuse('Insufficient shared speech core.')
    return dict(grids=receipts,common_core=[str(n) for n in common],support=[str(p),str(q)],policy=POLICY)

def anchored_fit(start,end,body_samples):
    width=end-start;gap=min(1920,half_up(Fraction(width,20)));usable=width-gap
    body=usable-1920 # exact 20 ms zeros on BOTH sides, after tempo
    if width<=0 or body<1 or body_samples<=0:raise TimingError('timing_unfit','No protected speech/guard slot.')
    ratio=max(Fraction(1),Fraction(body_samples,body))
    if ratio>Fraction(5,4):raise TimingError('timing_unfit','Speech cannot fit actual source support within 1.25.')
    return Fit(start,end,usable,ratio,ratio>Fraction(23,20))

def corrected_tempo(ratio,measured,capacity):
    """One measured WSOLA correction, never a permission to exceed the cap."""
    if not 1<=ratio<=Fraction(5,4) or not 0<capacity<measured:
        raise TimingError('timing_unfit','Invalid measured correction capacity.')
    proposed=ratio*Fraction(measured+2048,capacity)
    correction=min(Fraction(5,4),proposed)
    if correction<=ratio:raise TimingError('timing_unfit','Speech already exceeds capacity at the tempo cap.')
    return correction,proposed
