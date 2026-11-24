"""Provisional nonzero-source windows; acceptance STILL requires ASR + dual VAD.

Native per-channel quiet centers constrain segmentation, not recognition text.
No hard-zero fact is replaced, and candidates never grant checkpoint trust.
"""
import array
import base64
from fractions import Fraction
import hashlib

from mediaforge.features.dub.alignment import supports,ceil,floor
from mediaforge.features.dub.speech import TimingError

POLICY='estimated-outer-v2-native8ms-quiet160-guard16-q10x3-word80-dualvad-complete52-sourceclock'


def refuse(text):raise TimingError('speech_boundary_uncertain',text)


def estimated_support(ledger,original):
    rate=ledger['rate'];samples=ledger['samples']
    needs=[s for s in original if s.end-s.start>26*16000 or s.native_start==0 or s.native_end==samples]
    if not needs:return original,(),()
    feature=ledger.get('native_quiet',{})
    if (feature.get('policy')!='native-quiet-v1-allchannels-f64-maxrms8ms-u16ceil-3900s'
            or feature.get('rate')!=125 or feature.get('count')!=(samples*125+rate-1)//rate
            or not 0<feature.get('count',0)<=487500):refuse('Bounded all-channel native quiet evidence is unavailable.')
    if not isinstance(feature.get('data'),str) or len(feature['data'])!=4*((feature['count']*2+2)//3):
        refuse('Native quiet feature encoding exceeds its exact bound.')
    raw=base64.b64decode(feature['data'],validate=True)
    if len(raw)!=feature['count']*2 or hashlib.sha256(raw).hexdigest()!=feature['sha256']:
        refuse('Native quiet feature identity differs.')
    values=array.array('H',raw);quiet=[]
    for source in needs:
        first=floor(Fraction(source.native_start*125,rate));last=ceil(Fraction(source.native_end*125,rate))
        beginning=None
        for start in range(first,last,30*125):
            end=min(last,start+30*125);block=sorted(values[start:end])
            if not block:continue
            noise=max(1,block[(len(block)-1)//10]);limit=max(16,3*noise)
            # No foreground/noise separation claim is made here. Actual stable
            # speech must independently exceed measured bracket noise by 20 dB.
            for index in range(start,end):
                low=values[index]<=limit
                if low and beginning is None:beginning=index
                if not low and beginning is not None:
                    if index-beginning>=20:quiet.append((beginning,index))
                    beginning=None
        if beginning is not None and last-beginning>=20:quiet.append((beginning,last))
    centers=[]
    for left,right in quiet:
        # At the actual file boundaries retain the complete leading/trailing
        # quiet; interior candidates protect only their guarded 64+ ms center.
        native_left=ceil(Fraction(left*rate,125));native_right=min(samples,floor(Fraction(right*rate,125)))
        connects_left=left==0 or any(a<=native_left<=b for a,b in ledger['zeros'])
        connects_right=right==len(values) or any(a<=native_right<=b for a,b in ledger['zeros'])
        a=left if connects_left else left+2;b=right if connects_right else right-2
        if b-a<8:continue
        a=ceil(Fraction(a*rate,125));b=samples if right==len(values) else floor(Fraction(b*rate,125))
        if a<b:centers.append((a,b))
    intervals=sorted([*(tuple(row) for row in ledger['zeros']),*centers]);merged=[]
    for a,b in intervals:
        if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(merged[-1][1],b))
        else:merged.append((a,b))
    if len(merged)>5001:refuse('Estimated quiet ledger exceeds its 5001-interval bound.')
    result=supports(merged,samples,rate)
    if any(s.end-s.start>52*16000 for s in result):
        refuse('No corroboratable pause bounds a complete source utterance within two bounded observations; no clipped seam.')
    if not result:refuse('No distinct source speech remains above its measured background.')
    return result,tuple(merged),tuple(centers)
