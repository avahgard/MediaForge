"""V2 bounded observations/phrase journals; same owned provider/session lifecycle."""
from dataclasses import asdict
from fractions import Fraction
import hashlib,json,math
import array,base64
from pathlib import Path
from mediaforge.application.contracts.local_ai import LocalAiError,RecognitionRequest
from mediaforge.application.contracts.speech import PhraseSegmentV2
from mediaforge.features.dub import phrases,phrase_timing,block_timing
from mediaforge.features.dub.speech import half_up

POLICY='TUTORIAL-DUB-V2'
PAGE=32768

def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def hash_value(value):return hashlib.sha256(encoded(value)).hexdigest()
def binding():
    from mediaforge.features.dub.glossary import identity
    return dict(policy=POLICY,sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        glossary=identity(),
        assembler=phrases.POLICY,assembler_sha256=hashlib.sha256(Path(phrases.__file__).read_bytes()).hexdigest(),
        scheduler=phrase_timing.POLICY,scheduler_sha256=hashlib.sha256(Path(phrase_timing.__file__).read_bytes()).hexdigest(),
        elastic=block_timing.ADAPTIVE_TEMPO.name,elastic_sha256=hashlib.sha256(Path(block_timing.__file__).read_bytes()).hexdigest(),
        composition_sha256=hashlib.sha256(Path(__file__).with_name('tutorial_composition.py').read_bytes()).hexdigest(),
        window_samples=480000,maximum_phrase_seconds=60,maximum_loan_samples=19200)

def read_record(session,name):
    head=session.get(name+'.json')
    if head is None:return None
    if head.get('schema')!='tutorial-pages-v2' or not 0<head.get('bytes',0)<=2*1024**2 or not 1<=len(head.get('pages',()))<=64:
        raise LocalAiError('checkpoint_ineligible','Tutorial metadata header differs.')
    raw=bytearray()
    for index,row in enumerate(head['pages']):
        if row['name']!=f'{name}-p{index}.json' or not 0<row['bytes']<=PAGE:
            raise LocalAiError('checkpoint_ineligible','Tutorial page identity differs.')
        path=session.name(row['name'])
        if session.sha(path)!=row['sha256']:raise LocalAiError('checkpoint_ineligible','Tutorial metadata page differs.')
        piece=path.read_bytes()
        if len(piece)!=row['bytes']:raise LocalAiError('checkpoint_ineligible','Tutorial metadata page size differs.')
        raw.extend(piece)
    if len(raw)!=head['bytes'] or hashlib.sha256(raw).hexdigest()!=head['sha256']:
        raise LocalAiError('checkpoint_ineligible','Tutorial complete metadata digest differs.')
    return json.loads(raw,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Nonfinite tutorial metadata')))

def write_record(session,name,value):
    old=read_record(session,name)
    if old is not None:
        if encoded(old)!=encoded(value):raise LocalAiError('checkpoint_ineligible','Tutorial record identity differs.')
        return
    raw=encoded(value)
    if not 0<len(raw)<=2*1024**2:raise LocalAiError('disk_budget','Tutorial metadata record exceeds bound.')
    # Interrupted prior writes are discarded ONLY through the same-process
    # ticket's exact-object primitive. An unowned lookalike fails sha/protect.
    for index in range(64):
        path=session.name(f'{name}-p{index}.json')
        if path.exists():session.sha(path);session.discard(path)
    pages=[]
    for index,start in enumerate(range(0,len(raw),PAGE)):
        part=raw[start:start+PAGE];path=session.put_bytes(f'{name}-p{index}.json',part)
        pages.append(dict(name=path.name,bytes=len(part),sha256=hashlib.sha256(part).hexdigest()))
    session.put(name+'.json',dict(schema='tutorial-pages-v2',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),pages=pages))

def _sustained_empty(activity,ledger,a,b):
    runs=[]
    for row in activity:
        items=[];start=None
        for i,p in enumerate(row['scores']):
            x=row['offset']+512*i
            if p>=.8 and start is None:start=x
            if p<.8 and start is not None:items.append((start,x));start=None
        if start is not None:items.append((start,row['offset']+512*len(row['scores'])))
        runs.append(items)
    if len(runs)!=2:return False
    for x,y in runs[0]:
        for u,v in runs[1]:
            left=max(x,u)*3;right=min(y,v)*3
            if right-left>=72000:
                values=array.array('H',base64.b64decode(ledger['native_quiet']['data'],validate=True))
                if any(v>16 for v in values[max(0,left//384):min(len(values),(right+383)//384)]):return True
    return False

def source_phrase_rows(ledger,extraction,samples,providers,session,control,progress,*,total_samples=None):
    total=total_samples if total_samples is not None else samples*3;pauses=phrase_timing.pause_map(ledger,total)
    source=dict(schema='tutorial-source-evidence-v2',ledger=ledger,extraction_sha256=session.sha(extraction),samples=samples,policy=binding())
    write_record(session,'v2-source',source);source_key=hash_value(source)
    coverage=[];uncertain=0;native_chars=0;previous_row_end=None;asr_gaps=[]
    def rows():
        nonlocal uncertain,native_chars,previous_row_end
        with extraction.open('rb') as stream:
            for ordinal,a in enumerate(range(0,samples,480000)):
                control.check();b=min(samples,a+480000);key=f'v2-asr-{ordinal}';observation=read_record(session,key)
                if observation is None:
                    if phrase_timing.covers(pauses.hard,a*3,b*3):
                        observation=dict(schema='tutorial-asr-observation-v2',window=[a,b],rows=[],activity=[],uncertainty=[],status='confirmed_source_silence')
                    else:
                        progress('recognize',a,samples);stream.seek(a*2);raw=stream.read((b-a)*2)
                        if len(raw)!=(b-a)*2:raise LocalAiError('invalid_output','Incomplete tutorial observation bytes.')
                        observation=providers.recognize_tutorial(RecognitionRequest(raw,a),control)
                        observation['status']='text' if observation['rows'] else 'unclassified_audio'
                    observation['source_binding']=source_key
                    write_record(session,key,observation)
                if (observation.get('schema')!='tutorial-asr-observation-v2' or observation.get('window')!=[a,b]
                        or observation.get('source_binding')!=source_key):raise LocalAiError('checkpoint_ineligible','Tutorial observation clock/source differs.')
                if not observation['rows'] and _sustained_empty(observation['activity'],ledger,a,b):
                    raise LocalAiError('unrecognized_speech',f'Sustained source speech has no recognized text in {a}/16000–{b}/16000. Check the selected track/source.')
                coverage.append(dict(window=[a,b],status=observation['status'],observation=key,sha256=hash_value(observation)))
                uncertain+=len(observation['uncertainty'])+(observation['status']=='unclassified_audio')
                for index,row in enumerate(observation['rows']):
                    native_chars+=len(row['text'])
                    if previous_row_end is not None and row['start']-previous_row_end>=48000:asr_gaps.append((previous_row_end,row['start']))
                    previous_row_end=row['end']
                    yield phrases.NativeRow(ordinal,index,row['start'],row['end'],row['text'],tuple(tuple(x) for x in row.get('estimates',())))
    # Journal native observations first, then stream them again as text. This
    # permits independent long-pause classification without retaining a full
    # transcript/VAD list or running another inference.
    for _ in rows():pass
    estimated=[(a,b) for a,b in pauses.quiet if b-a>=48000 and activity_quiet(session,a,b,complete=False)]
    # Owner's V2 Timing Recovery decision: retain the ASR estimate, but an
    # independently contradicted ASR-only gap is not a physical long pause.
    assessments=[assess_asr_gap(session,ledger,a,b) for a,b in asr_gaps]
    retained=[(r['start'],r['end']) for r in assessments if not r['disputed']]
    pauses=phrase_timing.PauseMap(total,pauses.hard,pauses.quiet,phrase_timing.merge([*pauses.mandatory,*estimated,*retained]))
    def replay():
        for ordinal in range(len(coverage)):
            control.check();observation=read_record(session,f'v2-asr-{ordinal}')
            if observation is None:raise LocalAiError('checkpoint_ineligible','Tutorial observation is missing during assembly.')
            for index,row in enumerate(observation['rows']):
                yield phrases.NativeRow(ordinal,index,row['start'],row['end'],row['text'],tuple(tuple(x) for x in row.get('estimates',())))
    count=0;characters=0;phrase_digest=hashlib.sha256()
    for phrase in phrases.assemble(replay(),(*pauses.mandatory,(total,total+480000))):
        control.check();record=phrase.record();phrase_digest.update(encoded(record));count+=1
        characters+=sum(ref.last-ref.first for ref in phrase.refs)
        anchor=dict(start=phrase.start,end=phrase.end,estimated=True,policy=POLICY,phrase=record,source_binding=source_key)
        yield PhraseSegmentV2(half_up(Fraction(phrase.start,3)),half_up(Fraction(phrase.end,3)),phrase.text),(phrase.ordinal,),anchor
    if not count:raise LocalAiError('no_speech','No tutorial speech text was recognized; no blank dub will be published.')
    if native_chars!=characters:raise LocalAiError('invalid_output','Tutorial canonical character coverage differs.')
    final=pauses
    write_record(session,'v2-plan',dict(schema='tutorial-dub-schedule-v2',policy=binding(),source_binding=source_key,
        source_evidence_sha256=hash_value(ledger),coverage=coverage,phrases=count,native_characters=native_chars,
        owned_characters=characters,phrase_sha256=phrase_digest.hexdigest(),uncertainty_count=uncertain,pauses=asdict(final),asr_gap_assessments=assessments))
    progress('recognize',samples,samples)

def activity_quiet(session,a,b,*,complete=True):
    left=Fraction(a,3);right=Fraction(b,3)
    if left>=right:return False
    for grid in (0,256):
        intervals=[];weighted=0;covered=0
        for index in range(int(left)//480000,int(right-1)//480000+1):
            row=read_record(session,f'v2-asr-{index}')
            if row is None or len(row.get('activity',()))!=2:return False
            activity=row['activity'][grid//256]
            for j,p in enumerate(activity['scores']):
                x=activity['offset']+j*512;y=x+512;n=max(0,min(right,y)-max(left,x))
                if n:covered+=n;weighted+=n*p
        # Long estimated pauses have independent all-channel quiet evidence;
        # retain their complete span despite <=64ms of native grid-edge holes.
        # Loans require COMPLETE actual coverage, with no invented VAD scores.
        if not covered or (covered!=right-left if complete else right-left-covered>1024) or weighted/covered>.5:return False
    return True

def loan_qualified(session,pauses,a,b):
    if phrase_timing.covers(pauses.hard,a,b):return True
    if not phrase_timing.covers(pauses.quiet,a,b):return False
    return activity_quiet(session,a,b)


def assess_asr_gap(session,ledger,a,b):
    """Conservative dispute requires BOTH actual grids and native energy.

    No hard/exterior zero or independently qualified long quiet is removed.
    Missing observations retain the old conservative exclusion. Unknown native
    grid-edge spans count against the proof as POSSIBLE nonhigh, never as an
    invented score. Actual high frames alone break that possible quiet run.
    """
    left=Fraction(a,3);right=Fraction(b,3);facts=[]
    values=array.array('H',base64.b64decode(ledger['native_quiet']['data'],validate=True))
    for grid in (0,256):
        covered=0;nonhigh=0;longest=0;positive=0;core=0;maximum_core=0
        cursor=left;unknown=0;longest_unknown=0
        for index in range(int(left)//480000,int(right-1)//480000+1):
            value=read_record(session,f'v2-asr-{index}')
            if value is None or len(value.get('activity',()))!=2:return dict(start=a,end=b,disputed=False,reason='missing_activity',grids=facts)
            row=value['activity'][grid//256]
            for j,p in enumerate(row['scores']):
                x=max(left,row['offset']+j*512);y=min(right,row['offset']+(j+1)*512)
                if x>=y:continue
                if x<cursor:raise LocalAiError('invalid_output','Overlapping native activity intervals cannot prove a gap.')
                if x>cursor:
                    gap=x-cursor;unknown+=gap;longest_unknown=max(longest_unknown,gap)
                    nonhigh+=gap;longest=max(longest,nonhigh);core=0
                cursor=y;n=y-x
                covered+=n
                if p<.8:nonhigh+=n;longest=max(longest,nonhigh);core=0
                else:
                    nonhigh=0
                    lo=max(0,int(x*3)//384);hi=min(len(values),int((y*3+383)//384))
                    if any(v>16 for v in values[lo:hi]):positive+=n;core+=n;maximum_core=max(maximum_core,core)
                    else:core=0
        if cursor<right:
            gap=right-cursor;unknown+=gap;longest_unknown=max(longest_unknown,gap)
            nonhigh+=gap;longest=max(longest,nonhigh)
        facts.append(dict(grid=grid,covered=str(covered),extent=str(right-left),unknown=str(unknown),longest_unknown=str(longest_unknown),
            longest_nonhigh=str(longest),positive=str(positive),maximum_positive_core=str(maximum_core)))
    disputed=all(Fraction(r['covered'])+Fraction(r['unknown'])==right-left and Fraction(r['longest_nonhigh'])<16000 and Fraction(r['maximum_positive_core'])>=1536 for r in facts)
    return dict(start=a,end=b,disputed=disputed,reason='independent_nonquiet_speech_and_no_possible_long_nonhigh_run' if disputed else 'conservative_estimated_long_pause',
        unknown_policy='possible_nonhigh_not_observed_score',grids=facts)
