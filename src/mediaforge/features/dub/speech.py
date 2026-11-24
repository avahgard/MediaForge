"""C3 sample-clock policy. Pure, bounded, independent of providers and Qt."""
from dataclasses import dataclass, replace
from fractions import Fraction
import re

POLICY='dub-speech-v6-job-leases-core26-first-zero-seam-context2-gap40-tempo125-level16-tplimiter17-cal1'
RATE=48000
MAX_SEGMENTS=5000

class TimingError(RuntimeError):
    def __init__(self,code,message):self.code=code;super().__init__(message)

def fail(code,message):raise TimingError(code,message)

def half_up(value):
    value=Fraction(value)
    return (2*value.numerator+value.denominator)//(2*value.denominator)

@dataclass(frozen=True)
class Window:
    start:int
    end:int
    core_start:int
    core_end:int

def windows(samples):
    if type(samples) is not int or not 0<samples<=3900*16000:fail('invalid_timing','Invalid bounded source sample span.')
    for start in range(0,samples,26*16000):
        end=min(samples,start+26*16000)
        yield Window(max(0,start-32000),min(samples,end+32000),start,end)

def normalized(text):return ' '.join(re.findall(r'\w+',text.casefold()))

def valid(segment):
    if (type(segment.start_sample) is not int or type(segment.end_sample) is not int
            or not 0<=segment.start_sample<segment.end_sample
            or segment.end_sample-segment.start_sample>30*16000
            or not segment.text.strip() or len(segment.text.encode('utf-8'))>4096):
        fail('invalid_timing','Invalid bounded speech segment.')

def owned(window,segments):
    if len(segments)>64:fail('invalid_timing','Recognition window exceeds the 64-row batch bound.')
    result=[]
    for s in segments:
        valid(s)
        if not window.start<=s.start_sample<s.end_sample<=window.end:
            fail('invalid_timing','Recognition escaped its exact source window.')
        if 2*window.core_start<=s.start_sample+s.end_sample<2*window.core_end:result.append(s)
    return tuple(result)

def seam(previous,current,boundary):
    """Require corroboration of every observation that actually crosses a core seam.

    Different segmentations/partial words are refused, never clipped or guessed.
    Midpoint ownership is applied only after this adjacent-context comparison.
    """
    for left,right in ((previous,current),(current,previous)):
        for s in left:
            if s.start_sample<boundary<s.end_sample:
                matches=[t for t in right if normalized(s.text)==normalized(t.text)
                    and min(s.end_sample,t.end_sample)>max(s.start_sample,t.start_sample)]
                if len(matches)!=1:fail('timing_unfit','Ambiguous recognition chunk seam; explicit retry required.')

def reconcile(previous_window,previous,current_window,current,previous_owned=None):
    boundary=current_window.core_start
    seam(previous,current,boundary)
    left=list(owned(previous_window,previous) if previous_owned is None else previous_owned)
    right=list(owned(current_window,current))
    for s in previous:
        if s.start_sample<boundary<s.end_sample:
            t=next(t for t in current if normalized(s.text)==normalized(t.text)
                and min(s.end_sample,t.end_sample)>max(s.start_sample,t.start_sample))
            if s in left:
                if t in right:right.remove(t)
            elif t not in right:
                # Timestamp jitter may put both midpoints in the other core.
                # Retain one corroborated complete observation, never neither.
                left.append(s)
    return tuple(sorted(left,key=lambda s:s.start_sample)),tuple(right)

def group(segments):
    """Coalesce only equal starts or <=80 ms boundary overlaps, with lineage."""
    prior=None;lineage=[];count=0
    for index,s in enumerate(segments):
        valid(s);count+=1
        if count>MAX_SEGMENTS:fail('resource_unavailable','Speech segment count exceeds 5000.')
        if prior is not None:
            if s.start_sample<prior.start_sample:fail('invalid_timing','Speech starts are out of order.')
            overlap=prior.end_sample-s.start_sample
            if overlap>0 or s.start_sample==prior.start_sample:
                if overlap>1280 and s.start_sample!=prior.start_sample:
                    fail('timing_unfit','Unresolved overlapping speech cannot be overlaid.')
                text=prior.text if normalized(prior.text)==normalized(s.text) else prior.text+' '+s.text
                prior=replace(prior,end_sample=max(prior.end_sample,s.end_sample),text=text)
                valid(prior);lineage.append(index);continue
            yield prior,tuple(lineage)
        prior=s;lineage=[index]
    if prior is not None:yield prior,tuple(lineage)

@dataclass(frozen=True)
class Fit:
    start:int
    end:int
    usable:int
    ratio:Fraction
    accelerated_warning:bool

def fit(start,next_start,samples):
    if any(type(n) is not int for n in (start,next_start,samples)) or start<0 or samples<=0:
        fail('timing_unfit','Invalid fit clock.')
    width=next_start-start
    if width<=0:fail('timing_unfit','Nonpositive voice slot.')
    gap=min(1920,half_up(Fraction(width,20)));usable=width-gap
    if usable<1:fail('timing_unfit','Sub-sample voice slot.')
    ratio=max(Fraction(1),Fraction(samples,usable))
    if ratio>Fraction(5,4):fail('timing_unfit','Speech exceeds the 1.25 tempo ceiling; no words were cut.')
    return Fit(start,next_start,usable,ratio,ratio>Fraction(23,20))

def synthesis_units(text):
    """Ordered complete source coverage within accepted C2's 500-character unit."""
    if not text.strip() or len(text.encode('utf-8'))>4096:fail('invalid_output','Invalid bounded translated segment.')
    position=0
    while position<len(text):
        end=min(len(text),position+500)
        if end<len(text):
            end=text.rfind(' ',position+1,end)
            if end<=position:fail('invalid_output','Synthesis text has an unbounded token.')
            end+=1
        yield text[position:end];position=end
