"""Two bounded Whisper/VAD observations of ONE complete source utterance.

No source seam, fitted-audio seam, word editing or favorable phase search.
The predetermined 26 s offset swaps the two 512/256 VAD grids exactly.
Both observations must corroborate complete seam words and VAD categories.
"""
from fractions import Fraction

from mediaforge.features.dub.speech import normalized,TimingError

POLICY='complete-source-observation-v1-two30-offset26-join28-word80-delta80-vad-categories'
STEP=26*16000
WINDOW=30*16000
JOIN=28*16000


def refuse(detail):raise TimingError('speech_boundary_uncertain',detail)


def windows(begin,end):
    if type(begin) is not int or type(end) is not int or not 0<=begin<end or end-begin>56*16000:
        refuse('Complete source observation exceeds two bounded native windows.')
    if end-begin<=WINDOW:return ((begin,end),)
    return ((begin,begin+WINDOW),(begin+STEP,end))


def join(first,second,begin,end):
    """Typed first/second rows contain absolute clocks, confidence and VAD.

    30 s + 26 s second window permits at most 56 s total. At the fixed 28 s
    join, both engines have >=2 s context. A word is selected whole by midpoint
    ONLY after all words in the shared 2 s witness match in order and clocks.
    """
    seam=begin+JOIN
    if not begin+WINDOW<end<=begin+56*16000:refuse('Unexpected extended observation span.')
    a,b=first['words'],second['words']
    def center(word):return Fraction(word['start']+word['end'],2)
    witness=[]
    for rows in (a,b):
        witness.append([w for w in rows if seam-16000<=center(w)<=seam+16000])
    left,right=witness
    if not left or len(left)!=len(right):refuse('Overlapping native observations do not cover identical seam words.')
    for x,y in zip(left,right):
        if (not normalized(x['text']) or normalized(x['text'])!=normalized(y['text'])
                or min(x['probability'],y['probability'])<.8
                or max(abs(x['start']-y['start']),abs(x['end']-y['end']))>1280):
            refuse('Complete native seam words/text/confidence or clocks disagree.')
        if (center(x)<seam)!=(center(y)<seam):
            refuse('Native word-clock jitter makes fixed seam ownership ambiguous.')
    words=tuple([w for w in a if center(w)<seam]+[w for w in b if center(w)>=seam])
    if not 0<len(words)<=512:refuse('Complete utterance exceeds the bounded word inventory.')
    if any(x['end']>y['start'] for x,y in zip(words,words[1:])):
        refuse('Complete joined word clocks overlap; no clipping or repair.')
    if not words[0]['start']>=begin or not words[-1]['end']<=end:
        refuse('Complete joined words escaped their source context.')
    observations=[]
    def category(value):return 0 if value<=.2 else 2 if value>=.8 else 1
    for grid in (0,256):
        early=first['activity'][grid//256]
        late=second['activity'][1-grid//256]
        offset=begin+grid
        if early['offset']!=offset or late['offset']!=begin+STEP+(256-grid):
            refuse('Extended native VAD phase identity differs.')
        difference=late['offset']-offset
        if difference%512:refuse('Extended native VAD grids cannot share one exact sample clock.')
        base=difference//512;cut=JOIN//512
        # Compare a fixed +/-96 ms witness at the stateful VAD join. These
        # must be the SAME high/low/uncertain categories, not averaged scores.
        for index in range(cut-3,cut+3):
            if (not 0<=index<len(early['scores']) or not 0<=index-base<len(late['scores'])
                    or category(early['scores'][index])!=category(late['scores'][index-base])):
                refuse('Native VAD context resets disagree at the fixed observation join.')
        scores=[*early['scores'][:cut],*late['scores'][cut-base:]]
        if len(scores)!=(end-offset)//512:refuse('Extended activity clock is incomplete.')
        observations.append(dict(offset=offset,scores=scores))
    return dict(words=words,activity=observations,confidence=(*first['confidence'],*second['confidence']),
                rows=[dict(start_sample=words[0]['start'],end_sample=words[-1]['end'],
                           text=' '.join(w['text'].strip() for w in words))],
                policy=POLICY)
