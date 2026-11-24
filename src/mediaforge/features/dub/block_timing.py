"""Measured elastic timing inside immutable source-clock speech blocks.

This planner cannot remove an exclusion, change text or generate audio. Tempo
selection is a bounded search over actual renderer counts, never a duration
prediction promoted to successful output. No global delay crosses a block edge.
"""
from dataclasses import dataclass,asdict
from fractions import Fraction
from mediaforge.features.dub.speech import TimingError

POLICY='tutorial-elastic-block1-48k-guard20-separation40-grid10000-minimax-starts1s'
GUARDS=1920
SEPARATION=1920
MAX_PHRASES=5000

@dataclass(frozen=True)
class TempoPolicy:
    name:str
    maximum_grid:int
    maximum_trials:int

STRICT_TEMPO=TempoPolicy(POLICY,12500,14)
# Two pitch-preserving atempo stages, each <=2, bound this technical route to4x.
# This is an execution/search limit, never an intelligibility certification.
ADAPTIVE_TEMPO=TempoPolicy('tutorial-adaptive-tempo-v1-atempo2stages-max4-grid10000',40000,17)

def tempo_factors(tempo,policy=STRICT_TEMPO):
    if policy not in (STRICT_TEMPO,ADAPTIVE_TEMPO):
        raise TimingError('invalid_timing','Unknown authenticated tempo policy.')
    try:ratio=Fraction(tempo)
    except (ValueError,TypeError,OverflowError,ZeroDivisionError) as error:
        raise TimingError('invalid_timing','Nonfinite or invalid tempo.') from error
    if not 1<=ratio<=Fraction(policy.maximum_grid,10000):
        raise TimingError('phrase_timing_unfit','Tempo exceeds the selected bounded technical route.')
    return (ratio,) if ratio<=2 else (Fraction(2),ratio/2)

@dataclass(frozen=True)
class PhraseTime:
    ordinal:int
    start:int
    end:int
    neutral_samples:int

@dataclass(frozen=True)
class SpeechBlock:
    ordinal:int
    start:int
    end:int
    phrases:tuple[PhraseTime,...]

@dataclass(frozen=True)
class Placement:
    ordinal:int
    source_start:int
    source_end:int
    start:int
    end:int
    measured_body_samples:int
    delta:int

@dataclass(frozen=True)
class BlockSchedule:
    block:int
    start:int
    end:int
    tempo:Fraction
    placements:tuple[Placement,...]
    maximum_absolute_delta:int
    warnings:tuple[str,...]

def split_blocks(total,mandatory,phrases):
    if type(total) is not int or not 0<total<=3900*48000 or len(phrases)>MAX_PHRASES:
        raise TimingError('invalid_timing','Invalid bounded elastic source span.')
    previous=0
    for a,b in mandatory:
        if type(a) is not int or type(b) is not int or not previous<=a<b<=total:raise TimingError('invalid_timing','Invalid immutable block exclusion.')
        previous=b
    prior=-1;last_end=0
    for p in phrases:
        if (not isinstance(p,PhraseTime) or type(p.ordinal) is not int or p.ordinal<=prior
                or any(type(v) is not int for v in (p.start,p.end,p.neutral_samples))
                or not last_end<=p.start<p.end<=total or not 0<p.neutral_samples<=120*48000):
            raise TimingError('invalid_timing','Invalid ordered elastic phrase evidence.')
        prior=p.ordinal;last_end=p.end
    blocks=[];left=0;cursor=0
    for a,b in (*mandatory,(total,total)):
        items=[]
        while cursor<len(phrases) and phrases[cursor].start<a:
            p=phrases[cursor]
            if p.start<left or p.end>a:raise TimingError('phrase_timing_unfit','A phrase crosses a retained speech-block exclusion.')
            items.append(p);cursor+=1
        if items:blocks.append(SpeechBlock(len(blocks),left,a,tuple(items)))
        left=b
    if cursor!=len(phrases):raise TimingError('phrase_timing_unfit','Text lies inside a retained speech-block exclusion.')
    return tuple(blocks)

def body_capacity(block):
    return block.end-block.start-GUARDS*len(block.phrases)-SEPARATION*(len(block.phrases)-1)

def minimum_nominal_tempo(block):
    capacity=body_capacity(block)
    if capacity<=0:raise TimingError('phrase_timing_unfit','Speech block has no guarded body capacity.')
    return max(Fraction(1),Fraction(sum(p.neutral_samples for p in block.phrases),capacity))

def _refuse(block,counts,tempo,policy=STRICT_TEMPO):
    needed=sum(counts)+GUARDS*len(counts)+SEPARATION*(len(counts)-1)
    failure=TimingError('phrase_timing_unfit',f'Continuous speech block {block.ordinal+1} [{block.start},{block.end})/48000: complete measured narration needs {needed} samples, available {block.end-block.start}, tempo {tempo}; no barrier was borrowed.')
    failure.timing_facts=dict(schema='tutorial-elastic-block-refusal-v1',policy=policy.name,block=block.ordinal,
        start=block.start,end=block.end,measured_bodies=list(counts),tempo=str(tempo),
        guarded_required=needed,capacity=block.end-block.start,deficit_samples=max(0,needed-(block.end-block.start)),
        neutral_samples=sum(p.neutral_samples for p in block.phrases),nominal_minimum_tempo=str(minimum_nominal_tempo(block)))
    raise failure

def schedule_measured(block,counts,tempo,*,policy=STRICT_TEMPO):
    """Minimize worst start displacement; tie-break closest earlier phrase first."""
    counts=tuple(counts);tempo_factors(tempo,policy);tempo=Fraction(tempo)
    if (not block.phrases or len(counts)!=len(block.phrases) or len(counts)>MAX_PHRASES
            or any(type(n) is not int or not 0<n<=120*48000 for n in counts)):
        raise TimingError('invalid_timing','Invalid measured block/audio clock.')
    lengths=tuple(n+GUARDS for n in counts)
    if sum(lengths)+SEPARATION*(len(lengths)-1)>block.end-block.start:_refuse(block,counts,tempo,policy)
    def latest(delta):
        result=[0]*len(counts);edge=block.end
        for i in range(len(counts)-1,-1,-1):
            p=block.phrases[i];value=min(p.start+delta,edge-lengths[i]);result[i]=value
            if value<max(block.start,p.start-delta):return None
            edge=value-SEPARATION
        return result
    lo=0;hi=block.end-block.start
    while lo<hi:
        middle=(lo+hi)//2
        if latest(middle) is None:lo=middle+1
        else:hi=middle
    upper=latest(lo)
    if upper is None:_refuse(block,counts,tempo,policy)
    positions=[];edge=block.start
    for p,n,length,last in zip(block.phrases,counts,lengths,upper):
        first=max(edge,block.start,p.start-lo);start=max(first,min(p.start,last));end=start+length
        if start>last or end>block.end:raise TimingError('invalid_timing','Internal block solver inconsistency.')
        positions.append(Placement(p.ordinal,p.start,p.end,start,end,n,start-p.start));edge=end+SEPARATION
    maximum=max(abs(p.delta) for p in positions)
    warnings=tuple(w for test,w in ((maximum>48000,'phrase_timing_shifted'),(tempo>Fraction(23,20),'speech_accelerated'),
        (tempo>Fraction(8,5),'tempo_above_1_6'),(tempo>2,'tempo_above_2_0')) if test)
    return BlockSchedule(block.ordinal,block.start,block.end,tempo,tuple(positions),maximum,warnings)

def lowest_measured(block,measure,*,policy=STRICT_TEMPO):
    """At most14(strict)/17(adaptive) trials; lowest observed fit,1e-4 grid.

    The renderer must retain only its selected owned complete artifacts. Counts
    and all trials belong in the authenticated receipt. Untested continuous
    tempos are not claimed measured or globally optimal under WSOLA jitter.
    """
    tempo_factors(1,policy);trials={}
    def trial(grid):
        if grid not in trials:
            if len(trials)>=policy.maximum_trials:
                raise TimingError('invalid_timing','Finite measured tempo search exhausted.')
            counts=tuple(measure(Fraction(grid,10000)))
            try:result=schedule_measured(block,counts,Fraction(grid,10000),policy=policy)
            except TimingError as error:
                if error.code!='phrase_timing_unfit':raise
                result=None
            trials[grid]=(counts,result)
        return trials[grid][1]
    if trial(10000) is not None:return trials[10000][1],trials
    if trial(policy.maximum_grid) is None:
        _refuse(block,trials[policy.maximum_grid][0],Fraction(policy.maximum_grid,10000),policy)
    low=10000;high=policy.maximum_grid
    while high-low>1:
        mid=(low+high)//2
        if trial(mid) is None:low=mid
        else:high=mid
    selected=min(k for k,(_,result) in trials.items() if result is not None)
    return trials[selected][1],trials
