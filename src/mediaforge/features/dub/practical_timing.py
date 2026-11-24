"""Owner-approved bounded timing admission, separate from strict C3 defaults.

Hard source silence remains exact. Estimates require real ASR word evidence,
both fixed VAD grids and independent waveform quiet/continuous-speech evidence.
No search, reroll, word dropping, duration stretching or timing-fit relaxation.
"""
import array
from dataclasses import dataclass
from fractions import Fraction
import math

from mediaforge.features.dub.alignment import activity_runs,qualify_core,ceil,floor
from mediaforge.features.dub.speech import normalized,TimingError

POLICY='timing-admission01-v6-words70-edge60-dualvad80low20-wave20db-noise3floor16-wavegrid8-quiet96-guard16-center64-word500-edge200-complete52-fileclock-hardzero'


def refuse(detail):raise TimingError('speech_boundary_uncertain',detail)


@dataclass(frozen=True)
class AdmittedPart:
    start:Fraction
    end:Fraction
    text:str
    words:tuple
    estimated:bool
    evidence:dict


def intersection(left,right):
    rows=[];i=j=0
    while i<len(left) and j<len(right):
        a=max(left[i][0],right[j][0]);b=min(left[i][1],right[j][1])
        if a<b:rows.append((a,b))
        if left[i][1]<right[j][1]:i+=1
        else:j+=1
    return rows


def bounded_runs(scores,offset,*,speech):
    # Same threshold/run semantics as strict C3, extended ONLY to the joined
    # final-route 56 s evidence. Native requests remain <=30 s unchanged.
    if len(scores)<=938:return activity_runs(scores,offset,speech=speech)
    if len(scores)>1750 or any(type(p) not in (int,float) or not math.isfinite(p) or not 0<=p<=1 for p in scores):
        refuse('Invalid bounded extended VAD observations.')
    result=[];begin=None
    for index,p in enumerate(scores):
        yes=p>=.8 if speech else p<=.2
        if yes and begin is None:begin=index
        if not yes and begin is not None:
            if index-begin>=3:result.append((offset+begin*512,offset+index*512))
            begin=None
    if begin is not None and len(scores)-begin>=3:result.append((offset+begin*512,offset+len(scores)*512))
    return tuple(result)


def admit_hard_island(source,observations,zeros,window,words,text,pcm,*,estimated_outer=False,file_samples=None,hard_zeros=()):
    """Return complete word groups and mandatory estimated pause exclusions.

    The outer boundaries here are independently measured native hard-silence
    facts. Only interior uncertain activity is eligible for this admission path.
    """
    if (not words or len(words)>512 or normalized(''.join(w['text'] for w in words))!=normalized(text)
            or not normalized(text)):
        refuse('Word evidence does not corroborate complete ordered recognized text.')
    exact=None
    if not estimated_outer:
        try:exact=qualify_core(source,observations,zeros,window=window)
        except TimingError:pass
    if exact is not None:
        return (AdmittedPart(source.start,source.end,text,tuple(words),False,exact),),()
    if (len(observations)!=2 or observations[0]['offset']!=window[0]
            or observations[1]['offset']!=window[0]+256):refuse('Both predetermined VAD grids are required.')
    before=next(((a,b) for a,b in zeros if b==source.start),None)
    after=next(((a,b) for a,b in zeros if a==source.end),None)
    # Exact leading/trailing hard zeros connect to the SAME known file clock.
    # A <96 ms edge zero cannot hold three complete low VAD bins; its physical
    # source boundary is nevertheless stronger than an invented outside bracket.
    # Keep that boundary, all zeros and every existing confidence/200 ms limit.
    file_start=bool(estimated_outer and file_samples is not None and window[0]==0
        and (source.start==0 or before is not None and before in hard_zeros and before[0]==0 and source.start<=3200)
        and window[0]<=words[0]['start']<=window[1]
        and abs(words[0]['start']-source.start)<=3200)
    file_end=bool(estimated_outer and file_samples is not None and window[1]==file_samples
        and (source.end==file_samples or after is not None and after in hard_zeros and after[1]==file_samples and file_samples-source.end<=3200)
        and window[0]<=words[-1]['end']<=window[1]
        and abs(source.end-words[-1]['end'])<=3200)
    if ((before is None and not file_start) or (after is None and not file_end)
            or before is None and after is None):
        refuse('Timing admission lacks an independent noise bracket or exact file-edge context.')
    highs=[];lows=[]
    def low_inside(row,a,b):
        run=0
        for index,p in enumerate(row['scores']):
            lo=row['offset']+index*512;hi=lo+512
            run=run+1 if a<=lo and hi<=b and p<=.2 else 0
            if run>=3:return True
        return False
    for row in observations:
        if len(row['scores'])!=(window[1]-row['offset'])//512:refuse('Incomplete activity context.')
        selected=[(a,b) for a,b in bounded_runs(row['scores'],row['offset'],speech=True)
                  if a<source.end and b>source.start]
        if not selected or selected[0][0]<source.start-512 or selected[-1][1]>source.end+512:
            refuse('Stable speech core is absent or connects another source unit.')
        if (before is not None and not file_start and not low_inside(row,*before)) or (after is not None and not file_end and not low_inside(row,*after)):
            refuse('Outer non-speech context is not corroborated.')
        highs.append(selected);lows.append(bounded_runs(row['scores'],row['offset'],speech=False))
    common=intersection(highs[0],highs[1])
    if not any(b-a>=1024 for a,b in common):refuse('No shared 64 ms stable speech core.')
    if ((not file_start and abs(highs[0][0][0]-highs[1][0][0])>512)
            or (not file_end and abs(highs[0][-1][1]-highs[1][-1][1])>512)):
        refuse('VAD outer envelopes disagree by more than 32 ms.')
    if any(r[0][0]-source.start>3200 or source.end-r[-1][1]>3200 for r in highs):
        refuse('Uncertain outer activity exceeds the qualified 200 ms envelope; no global shift.')
    if (any(not math.isfinite(w['probability']) or not 0<=w['probability']<=1 for w in words)
            or sum(w['probability'] for w in words)/len(words)<.7
            or min(words[0]['probability'],words[-1]['probability'])<(.8 if estimated_outer else .6)):
        refuse('ASR confidence is insufficient for estimated timing.')
    samples=array.array('h',pcm)
    if len(samples)!=window[1]-window[0]:refuse('Independent waveform context is incomplete.')
    def rms(a,b):
        left=max(0,ceil(Fraction(a))-window[0]);right=min(len(samples),floor(Fraction(b))-window[0])
        if left>=right:refuse('No complete waveform samples in the evidence interval.')
        return math.sqrt(sum(v*v for v in samples[left:right])/(right-left))
    signal=max(rms(max(source.start,a),min(source.end,b)) for a,b in common if max(source.start,a)<min(source.end,b))
    noise=max([1.,*(rms(max(window[0],bracket[0]),min(window[1],bracket[1]))
                   for bracket in (before,after) if bracket is not None)])
    if signal<64 or signal<10*noise:refuse('Estimated timing requires a 20 dB stable speech/noise separation.')
    # A weak consonant can be 20 dB below a vowel and still be a complete word.
    # Relative loudness is not silence; use the independent bracket noise floor.
    quiet_limit=max(16.,noise*3)
    def mean(row,a,b):
        total=weight=0
        for index,p in enumerate(row['scores']):
            lo=row['offset']+index*512;hi=lo+512
            n=max(0,min(b,hi)-max(a,lo))
            total+=p*n;weight+=n
        if weight!=b-a:refuse('Activity confidence does not cover the complete estimate.')
        return total/weight
    pauses=[];cuts=[];quiet_runs=[];quiet_start=None
    # Physical quiet is measured on ONE predetermined 8 ms waveform grid.
    # ASR word edges label a possible word boundary; they never define silence.
    # In particular, a 300 ms ASR gap may contain only 100 ms of real quiet.
    for a in range(ceil(source.start/128)*128,floor(source.end)-127,128):
        quiet=rms(a,a+128)<=quiet_limit
        if quiet and quiet_start is None:quiet_start=a
        if not quiet and quiet_start is not None:
            quiet_runs.append((quiet_start,a));quiet_start=None
    if quiet_start is not None:quiet_runs.append((quiet_start,floor(source.end/128)*128))
    for left_edge,right_edge in quiet_runs:
        if right_edge-left_edge<1536:continue
        # Outer tails are already bounded by the independent high envelopes.
        if left_edge-source.start<=128 or source.end-right_edge<=128:continue
        a=left_edge+256;b=right_edge-256
        candidates=[]
        for index,(left,right) in enumerate(zip(words,words[1:])):
            if (abs(left_edge-left['end'])<=8000 and abs(right_edge-right['start'])<=8000
                    and Fraction(left['start']+left['end'],2)<left_edge
                    and Fraction(right['start']+right['end'],2)>right_edge):
                candidates.append(index)
        if len(candidates)!=1:refuse('Measured quiet lacks one unambiguous complete-word pause boundary.')
        index=candidates[0];left,right=words[index:index+2]
        if min(left['probability'],right['probability'])<.8:
            refuse('Measured quiet lacks high-confidence adjacent complete words.')
        if max(mean(row,a,b) for row in observations)>.5:
            refuse('ASR, activity and waveform disagree on an interior pause.')
        if cuts and index+1<=cuts[-1]:refuse('Several measured pauses ambiguously cross the same word boundary.')
        pauses.append((Fraction(a),Fraction(b)));cuts.append(index+1)
    def covered(a,b):
        return sum(max(Fraction(0),min(b,y)-max(a,x)) for x,y in pauses)
    # A confirmed low region cannot be filled merely because an ASR word clock
    # was uncertain. The shared guarded pause must account for its stable core.
    for a,b in intersection(lows[0],lows[1]):
        # A maximal low run connected to an OUTER bracket is edge uncertainty,
        # not an interior pause. Lossy codec ringing can precede physical voice
        # while remaining nonzero. Bound it, retain the complete source support
        # and report estimates; never move the anchor or crop its waveform.
        if a<=source.start:
            if b-source.start>3200:refuse('Leading activity uncertainty exceeds 200 ms.')
            continue
        if b>=source.end:
            if source.end-a>3200:refuse('Trailing activity uncertainty exceeds 200 ms.')
            continue
        a=max(a,source.start+1024);b=min(b,source.end-1024)
        if b-a>=1536 and covered(a,b)<min(Fraction(1024),Fraction(b-a,2)):
            refuse('ASR, activity and waveform disagree: confirmed interior non-speech lacks a complete-word pause boundary.')
    for runs in highs:
        for (_,a),(b,_) in zip(runs,runs[1:]):
            a=max(a,source.start);b=min(b,source.end)
            if b-a<1536 or covered(a,b)>=min(Fraction(1024),Fraction(b-a,2)):continue
            intervals=[]
            for word in words:
                x=max(a,word['start']);y=min(b,word['end'])
                if x<y:intervals.append((x,y))
            # Overlapping word-clock estimates do not count the same time twice.
            used=0;last=a
            for x,y in intervals:
                used+=max(0,y-max(x,last));last=max(last,y)
            if used*5<(b-a)*4 or rms(a,b)<=quiet_limit:
                refuse('Uncertain activity gap lacks continuous-word/waveform corroboration. '
                    f'Clock={a}:{b}; word_coverage={float(Fraction(used,b-a)):.3f}; '
                    f'RMS={rms(a,b):.2f}; quiet_limit={quiet_limit:.2f}.')
    result=[];begin=0;start=source.start
    evidence=dict(mode='estimated-outer-and-internal-boundaries' if estimated_outer else 'estimated-internal-boundaries',policy=POLICY,signal_rms=signal,noise_rms=noise,
                  mean_word_confidence=sum(w['probability'] for w in words)/len(words),
                  pauses=[[str(a),str(b)] for a,b in pauses],vad_envelopes=[[r[0][0],r[-1][1]] for r in highs],
                  exact_file_edges=dict(start=file_start,end=file_end))
    for part_index,cut in enumerate([*cuts,len(words)]):
        end=pauses[part_index][0] if part_index<len(pauses) else source.end
        selected=tuple(words[begin:cut]);value=' '.join(w['text'].strip() for w in selected)
        if not selected or end<=start or not normalized(value):refuse('Estimated pause would drop or overlap complete words.')
        result.append(AdmittedPart(start,end,value,selected,True,evidence))
        if part_index<len(pauses):start=pauses[part_index][1]
        begin=cut
    if normalized(' '.join(p.text for p in result))!=normalized(text):refuse('Estimated partition changed recognized words.')
    return tuple(result),tuple(pauses)
