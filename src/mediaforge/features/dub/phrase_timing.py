"""Tutorial V2 source facts, mandatory dub barriers and deterministic gap loans."""
import array,base64,hashlib,math,time
from dataclasses import dataclass,asdict
from fractions import Fraction
from mediaforge.features.dub.alignment import anchored_fit,floor,ceil
from mediaforge.features.dub.speech import TimingError

POLICY='TUTORIAL-DUB-V2-schedule1-long1000-loan400-next80-separation40-tempo125'

def merge(intervals):
    result=[]
    for a,b in sorted(intervals):
        if a>=b:continue
        if result and a<=result[-1][1]:result[-1]=(result[-1][0],max(result[-1][1],b))
        else:result.append((a,b))
    if len(result)>5001:raise TimingError('resource_unavailable','Tutorial pause inventory exceeds 5001.')
    return tuple(result)

@dataclass(frozen=True)
class PauseMap:
    total:int
    hard:tuple
    quiet:tuple
    mandatory:tuple

def pause_map(ledger,total):
    rate=ledger['rate']
    if (type(rate) is not int or not 0<rate<=768000 or type(ledger.get('samples')) is not int
            or not 0<ledger['samples']<=3900*rate or type(total) is not int or not 0<total<=3900*48000
            or len(ledger.get('zeros',()))>5001):raise TimingError('invalid_output','Invalid native tutorial source clock.')
    previous=0
    for a,b in ledger['zeros']:
        if type(a) is not int or type(b) is not int or not previous<=a<b<=ledger['samples']:raise TimingError('invalid_output','Invalid native hard-zero inventory.')
        previous=b
    hard=merge((max(0,floor(Fraction(a*48000,rate))),min(total,ceil(Fraction(b*48000,rate)))) for a,b in ledger['zeros'])
    feature=ledger.get('native_quiet',{})
    if (feature.get('policy')!='native-quiet-v1-allchannels-f64-maxrms8ms-u16ceil-3900s'
            or feature.get('rate')!=125 or feature.get('count')!=(ledger['samples']*125+rate-1)//rate
            or not 0<feature.get('count',0)<=487500 or not isinstance(feature.get('data'),str)
            or len(feature['data'])!=4*((feature['count']*2+2)//3)):
        raise TimingError('invalid_output','Native tutorial quiet evidence identity differs.')
    raw=base64.b64decode(feature['data'],validate=True)
    if len(raw)!=feature['count']*2 or hashlib.sha256(raw).hexdigest()!=feature['sha256']:
        raise TimingError('invalid_output','Native tutorial quiet evidence hash differs.')
    values=array.array('H',raw);quiet=[];begin=None
    for n in range(0,len(values),3750):
        block=sorted(values[n:n+3750]);limit=max(16,3*max(1,block[(len(block)-1)//10]))
        for i in range(n,min(n+3750,len(values))):
            if values[i]<=limit and begin is None:begin=i
            if values[i]>limit and begin is not None:
                if (i-begin)*384>=3840:quiet.append((begin*384,min(total,i*384)))
                begin=None
    if begin is not None and total-begin*384>=3840:quiet.append((begin*384,total))
    quiet=merge(quiet)
    # Nonzero quiet candidates need actual coarse activity before they become
    # estimated barriers. A loud constant foreground is not a silence oracle.
    mandatory=merge((a,b) for a,b in hard if a==0 or b==total or b-a>=48000)
    return PauseMap(total,hard,quiet,mandatory)

def covers(intervals,a,b):
    return a<b and any(x<=a and b<=y for x,y in intervals)

@dataclass(frozen=True)
class Slot:
    ordinal:int
    start:int
    base_end:int
    maximum_end:int
    loan:int
    next_start:int|None
    barrier:int|None

def slot_for(phrase,following,pauses,loan_qualified=False):
    start=phrase.start;end=phrase.end;next_start=following.start if following else None
    if not 0<=start<end<=pauses.total:raise TimingError('phrase_timing_unfit','Invalid estimated phrase span.')
    if any(start<b and end>a for a,b in pauses.mandatory):
        raise TimingError('phrase_timing_unfit','Phrase estimate crosses a mandatory pause.')
    gap=next_start-end if following else 0
    loan=min(19200,max(0,gap-3840)) if 0<gap<48000 and loan_qualified else 0
    barrier=next((a for a,b in pauses.mandatory if a>=end),None)
    maximum=min(pauses.total,end+loan,next_start-1920 if following else pauses.total,barrier if barrier is not None else pauses.total)
    base=min(end,maximum)
    if base<=start:raise TimingError('phrase_timing_unfit','Ordered phrase has no nonoverlapping capacity.')
    return Slot(phrase.ordinal,start,base,maximum,loan,next_start,barrier)

def fit_phrase(slot,body_samples):
    """Prefer natural body at base, then minimum needed loan, then <=1.25x."""
    def body(end):
        width=end-slot.start
        return width-min(1920,floor(Fraction(width,20)+Fraction(1,2)))-1920
    selected=slot.base_end
    if body_samples>body(selected):
        selected=slot.maximum_end
        if body_samples<=body(selected):
            lo=slot.base_end;hi=selected
            while lo<hi:
                mid=(lo+hi)//2
                if body(mid)>=body_samples:hi=mid
                else:lo=mid+1
            selected=lo
    try:return anchored_fit(slot.start,selected,body_samples)
    except TimingError as error:
        capacity=max(0,body(slot.maximum_end));ratio=Fraction(body_samples,capacity) if capacity else None
        failure=TimingError('phrase_timing_unfit',f'Phrase {slot.ordinal+1} at {slot.start}/48000: complete voice {body_samples} samples, maximum body {capacity}, needed ratio {ratio}, allowed 5/4.')
        failure.timing_facts=dict(schema='tutorial-fit-v2',ordinal=slot.ordinal,slot=asdict(slot),neutral_samples=body_samples,body_capacity=capacity,needed_ratio=str(ratio),allowed_ratio='5/4')
        raise failure from error

