"""Tutorial V2: exact ordered native character coverage, estimated phrase clocks.

No word-confidence gate, transcript rewrite or physical word-boundary claim.
The assembler retains only one bounded pending phrase and the current row.
"""
from dataclasses import dataclass,asdict
from fractions import Fraction
import hashlib,re
from mediaforge.features.dub.speech import TimingError

POLICY='TUTORIAL-DUB-V2-phrases1-char4096-extent60-target4to20'

@dataclass(frozen=True)
class NativeRow:
    observation:int
    row:int
    start:int  # original-clock 48 kHz; converted once from native ASR16k
    end:int
    text:str
    estimates:tuple=()

@dataclass(frozen=True)
class TextRef:
    observation:int
    row:int
    first:int
    last:int
    row_sha256:str
    separator:str=''

@dataclass(frozen=True)
class Phrase:
    ordinal:int
    start:int
    end:int
    text:str
    refs:tuple[TextRef,...]
    basis:str='monotone-native-row-character-estimate'

    def record(self):
        return dict(schema='tutorial-phrase-v2',ordinal=self.ordinal,start=self.start,end=self.end,text=self.text,
            refs=[[r.observation,r.row,r.first,r.last,r.row_sha256,r.separator] for r in self.refs],
            basis=self.basis,sha256=hashlib.sha256(self.text.encode()).hexdigest())

def assemble(rows,barriers):
    """Partition every input character once, including punctuation/whitespace.

    Barrier splits move whole text tokens, never samples of synthesized speech.
    Attention word times are deliberately not authoritative in this first recipe.
    """
    pending=[];text='';start=end=0;ordinal=0;region=None;previous_row_end=None
    def emit():
        nonlocal pending,text,ordinal
        value=Phrase(ordinal,start,end,text,tuple(pending));ordinal+=1
        if ordinal>5000:raise TimingError('resource_unavailable','Tutorial phrase count exceeds 5000.')
        pending=[];text=''
        return value
    for row in rows:
        if (not isinstance(row,NativeRow) or type(row.start) is not int or type(row.end) is not int
                or row.start<0 or row.end<=row.start or not isinstance(row.text,str) or not row.text.strip()
                or len(row.text)>4000 or '\x00' in row.text):
            raise TimingError('invalid_output','Invalid native tutorial row.')
        if previous_row_end is not None and row.start<previous_row_end:
            raise TimingError('invalid_output','Native tutorial rows overlap or are unordered.')
        if pending and previous_row_end is not None and row.start-previous_row_end>=48000:
            yield emit()
        previous_row_end=row.end
        digest=hashlib.sha256(row.text.encode()).hexdigest();cursor=0
        tokens=list(re.finditer(r'\S+\s*',row.text))
        available=[];edge=row.start
        for x,y in barriers:
            if y<=edge:continue
            if x>=row.end:break
            if x>edge:available.append((edge,min(x,row.end)))
            edge=max(edge,y)
        if edge<row.end:available.append((edge,row.end))
        extent=sum(b-a for a,b in available)
        if extent<=0:raise TimingError('phrase_timing_unfit','Native text lies wholly in a mandatory output exclusion.')
        def project(character):
            distance=Fraction(extent*character,len(row.text))
            for x,y in available:
                if distance<=y-x:return x+int(distance)
                distance-=y-x
            return available[-1][1]
        useful=(len(row.estimates)==len(tokens) and all(row.start<=x<y<=row.end and (i==0 or row.estimates[i-1][1]<=x)
                for i,(x,y) in enumerate(row.estimates)))
        for token_index,token in enumerate(tokens):
            lo=cursor;hi=token.end();cursor=hi;part=row.text[lo:hi]
            a=project(lo);b=project(hi)
            if useful:
                x,y=row.estimates[token_index]
                if not any(u<=Fraction(x+y,2)<v for u,v in barriers):a,b=x,y
            mid=Fraction(a+b,2);left=0;right=None
            for x,y in barriers:
                if mid>=y:left=max(left,y)
                elif mid<x:right=x;break
                else:
                    # Estimate in a preserved pause: deterministic whole-token
                    # side, explicitly derived; no fabricated native timestamp.
                    if mid<Fraction(x+y,2):right=x
                    else:left=y
                    break
            if right is not None and right<=left:
                raise TimingError('phrase_timing_unfit','No source region outside mandatory pause.')
            a=max(a,left);b=max(a+1,b)
            if right is not None:b=min(b,right);a=min(a,b-1)
            key=(left,right)
            separator='' if not pending or pending[-1].observation==row.observation and pending[-1].row==row.row or text[-1:].isspace() or part[:1].isspace() else ' '
            candidate=text+separator+part
            if pending and (key!=region or b-start>60*48000 or len(candidate)>4000 or len(candidate.encode())>4096):
                yield emit();separator='';candidate=part
            if len(candidate)>4000 or len(candidate.encode())>4096 or b-a>60*48000:
                raise TimingError('invalid_output','Unbounded tutorial text token/phrase.')
            if not pending:start=a;region=key
            end=max(b,end if pending else b)
            if pending and pending[-1].observation==row.observation and pending[-1].row==row.row and pending[-1].last==lo and not separator:
                previous=pending[-1];pending[-1]=TextRef(previous.observation,previous.row,previous.first,hi,digest,previous.separator)
            else:pending.append(TextRef(row.observation,row.row,lo,hi,digest,separator))
            text=candidate
            if len(pending)>2048:raise TimingError('resource_unavailable','Phrase character-reference limit.')
            terminal=part.rstrip()[-1:] in '.!?'
            clause=part.rstrip()[-1:] in ',;:'
            if terminal and end-start>=4*48000 or clause and end-start>=20*48000:
                yield emit()
        if cursor!=len(row.text):raise TimingError('invalid_output','Native character coverage is incomplete.')
    if pending:yield emit()

