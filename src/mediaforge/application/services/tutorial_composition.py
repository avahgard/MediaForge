"""Owned V2 elastic generation/measurement/composition; existing workers only."""
from dataclasses import asdict
from fractions import Fraction
import array,hashlib
from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.application.contracts.speech import SpeechComposition
from mediaforge.application.services import tutorial_speech
from mediaforge.features.dub import block_timing
from mediaforge.features.dub.speech import synthesis_units

def _voice(service,request,session,control,engine,index,segment,anchor,translated):
    text=translated['text'];english=hashlib.sha256(segment.text.encode()).hexdigest();russian=hashlib.sha256(text.encode()).hexdigest()
    body=session.name(f'e-body-{index}.pcm');key=f'e-body-{index}.json';record=session.get(key)
    if record is not None:
        if (record.get('schema')!='tutorial-elastic-body-v1' or record['english_sha256']!=english or record['russian_sha256']!=russian
                or record['phrase_sha256']!=anchor['phrase']['sha256'] or record['body_sha256']!=session.sha(body)
                or body.stat().st_size!=record['body_samples']*2):raise LocalAiError('checkpoint_ineligible','Complete elastic body identity differs.')
        cursor=0;count=0
        for unit in record['units']:
            if (unit['first']!=cursor or not cursor<unit['last']<=len(text)
                    or unit['text_sha256']!=hashlib.sha256(text[cursor:unit['last']].encode()).hexdigest()
                    or unit['completion']['reason']!='normal_eos' or unit['completion']['terminal_eos'] is not True):
                raise LocalAiError('checkpoint_ineligible','Elastic body EOS/text lineage differs.')
            cursor=unit['last'];count+=unit['samples']
        if cursor!=len(text) or count!=record['samples24k']:raise LocalAiError('checkpoint_ineligible','Elastic complete text/sample extent differs.')
        return record
    # Explicit Retry discards only tracked incomplete stage objects. No previous
    # process cache or a filename lookalike is adopted.
    for name in (body.name,f'e-neutral-{index}.pcm',f'e-resample-{index}.pcm'):
        path=session.name(name)
        if session.owns_name(name):session.discard(path)
        elif path.exists():raise LocalAiError('storage_refused','Unknown elastic-stage object; no cache adoption.')
    units=[];parts=[];cursor=0;size=0
    for unit in synthesis_units(text):
        control.check();fragment=engine.synthesize(unit,request.voice)
        if fragment.sample_rate!=24000 or fragment.speaker!=request.voice or fragment.completion is None:
            raise LocalAiError('invalid_output','Selected voice/completion identity differs.')
        if fragment.completion.reason!='normal_eos' or fragment.completion.terminal_eos is not True:
            raise LocalAiError('invalid_output','Elastic narration lacks terminal EOS.')
        size+=len(fragment.pcm16)
        if size>120*24000*2 or size>16*1024**2:raise LocalAiError('invalid_output','Complete elastic voice exceeds fragment bound.')
        parts.append(fragment.pcm16);units.append(dict(first=cursor,last=cursor+len(unit),text_sha256=hashlib.sha256(unit.encode()).hexdigest(),
            pcm_sha256=hashlib.sha256(fragment.pcm16).hexdigest(),samples=fragment.samples,completion=asdict(fragment.completion)))
        cursor+=len(unit)
    if cursor!=len(text):raise LocalAiError('invalid_output','Elastic synthesis text coverage differs.')
    neutral=session.put_bytes(f'e-neutral-{index}.pcm',b''.join(parts));resampled=session.name(f'e-resample-{index}.pcm')
    session.admit(120*48000*4+16*1024**2)
    service.audio.convert(neutral,resampled,control,rate=24000);session.track(resampled)
    raw=resampled.read_bytes()
    if len(raw)>16*1024**2:raise LocalAiError('invalid_output','Elastic voice buffer exceeds bound.')
    values=array.array('h',raw)
    if not values or not any(values):raise LocalAiError('invalid_output','Silent complete voice refused.')
    first=next(i for i,v in enumerate(values) if v);last=len(values)-next(i for i,v in enumerate(reversed(values)) if v)
    session.put_bytes(body.name,raw[first*2:last*2])
    record=dict(schema='tutorial-elastic-body-v1',ordinal=index,phrase_sha256=anchor['phrase']['sha256'],english_sha256=english,
        russian_sha256=russian,source_start=anchor['start'],source_end=anchor['end'],body_samples=last-first,body_sha256=session.sha(body),
        untrimmed_samples48k=len(values),trimmed_exact_exterior=[first,len(values)-last],samples24k=size//2,
        original_neutral_sha256=session.sha(neutral),units=units)
    session.put(key,record)
    session.discard(neutral);session.discard(resampled)
    return record

def compose(service,request,session,control,progress,rows,ledger,generation,count,tutorial):
    """One GPU engine, bounded disk bodies, then GPU retirement before fitting."""
    total=request.plan.sample_count;times=[];warnings={'estimated_phrase_timing'}
    if tutorial['uncertainty_count']:warnings.add('recognition_uncertain')
    progress('load_voice',0,0)
    try:
        with service.tts.open(control,service.providers.session_owner) as (engine,checked):
            for index,(segment,lineage,anchor) in enumerate(rows):
                checked.check();translated=session.get(f'v2-translate-{index}.json')
                if translated is None or not translated['text'].strip() or translated['english_sha256']!=hashlib.sha256(segment.text.encode()).hexdigest():
                    raise LocalAiError('checkpoint_ineligible','Elastic CPU translation journal differs.')
                progress('synthesize',index,count)
                record=_voice(service,request,session,checked,engine,index,segment,anchor,translated)
                times.append(block_timing.PhraseTime(index,anchor['start'],anchor['end'],record['body_samples']))
                session.put(f'v2-eos-{generation}-{index}.json',record)
                progress('synthesize',index+1,count)
    finally:
        if service.tts.last_statistics is not None:service.last_receipt['tts_resources']=dict(service.tts.last_statistics)
    if len(times)!=count:raise LocalAiError('invalid_output','Elastic phrase generation coverage differs.')
    mandatory=tuple(map(tuple,tutorial['pauses']['mandatory']))
    blocks=block_timing.split_blocks(total,mandatory,tuple(times));schedules=[];selected=[]
    progress('fit',0,len(blocks))
    for block in blocks:
        measurements={}
        def measure(tempo):
            receipts=[]
            for phrase in block.phrases:
                control.check();source=session.name(f'e-body-{phrase.ordinal}.pcm')
                if tempo==1:
                    receipts.append(dict(samples=phrase.neutral_samples,sha256=session.sha(source)));continue
                target=session.name(f'e-trial-{generation}-{phrase.ordinal}.pcm')
                samples=service.audio.convert(source,target,control,rate=48000,tempo=tempo,maximum=120*48000+4800,
                    tempo_policy=block_timing.ADAPTIVE_TEMPO);session.track(target)
                receipts.append(dict(samples=samples,sha256=session.sha(target)));session.discard(target)
            measurements[str(tempo)]=receipts
            return tuple(r['samples'] for r in receipts)
        try:schedule,trials=block_timing.lowest_measured(block,measure,policy=block_timing.ADAPTIVE_TEMPO)
        except Exception as error:
            facts=getattr(error,'timing_facts',None)
            if facts is not None:
                service.last_receipt['fit_candidate']=facts
                tutorial_speech.write_record(session,f'e-block-refusal-{generation}-{block.ordinal}',dict(facts,measurements=measurements))
            raise
        record=dict(schema='tutorial-adaptive-block-v1',policy=block_timing.ADAPTIVE_TEMPO.name,source_binding=tutorial['source_binding'],
            block=asdict(block),tempo=str(schedule.tempo),placements=[asdict(p) for p in schedule.placements],
            maximum_absolute_delta=schedule.maximum_absolute_delta,warnings=schedule.warnings,
            trials=[dict(tempo=str(Fraction(k,10000)),counts=list(counts),feasible=result is not None) for k,(counts,result) in sorted(trials.items())],
            selected=measurements[str(schedule.tempo)])
        tutorial_speech.write_record(session,f'e-block-{generation}-{block.ordinal}',record)
        schedules.append(schedule);selected.append(record['selected']);warnings.update(schedule.warnings)
        progress('fit',block.ordinal+1,len(blocks))
    # Measurements are metadata, not retained full timeline copies. Re-render
    # selected deterministic FFmpeg bytes one phrase at a time and compare the
    # observed hash/count before writing; never regenerate TTS for fitting.
    composed=session.name('e-compose-'+generation+'.pcm');position=0;written=0;digest=hashlib.sha256()
    with session.pcm(composed.name,total) as writer:
        for schedule,receipts in zip(schedules,selected):
            for placement,receipt in zip(schedule.placements,receipts):
                control.check();index=placement.ordinal;source=session.name(f'e-body-{index}.pcm');target=source
                if schedule.tempo!=1:
                    target=session.name(f'e-selected-{generation}-{index}.pcm')
                    samples=service.audio.convert(source,target,control,rate=48000,tempo=schedule.tempo,maximum=120*48000+4800,
                        tempo_policy=block_timing.ADAPTIVE_TEMPO);session.track(target)
                else:samples=source.stat().st_size//2
                if samples!=receipt['samples'] or session.sha(target)!=receipt['sha256']:
                    raise LocalAiError('validation_failed','Selected elastic measured render is not reproducible; no publication.')
                if not position<=placement.start or placement.end!=placement.start+1920+samples:
                    raise LocalAiError('invalid_timing','Elastic absolute write schedule differs.')
                writer.silence(placement.start-position);writer.silence(960)
                for raw in session.read_blocks(target):writer.write(raw)
                writer.silence(960);position=placement.end;written+=1
                record=dict(schema='tutorial-adaptive-fit-v1',policy=block_timing.ADAPTIVE_TEMPO.name,ordinal=index,block=schedule.block,
                    start=placement.start,end=placement.end,source_start=placement.source_start,source_end=placement.source_end,
                    delta=placement.delta,tempo=str(schedule.tempo),measured_body_samples=samples,fitted_sha256=receipt['sha256'],
                    phrase_sha256=session.get(f'e-body-{index}.json')['phrase_sha256'],block_start=schedule.start,block_end=schedule.end)
                session.put(f'v2-schedule-{generation}-{index}.json',record);digest.update(tutorial_speech.encoded(record))
                if target!=source:session.discard(target)
                progress('compose',position,total)
        writer.silence(total-position)
    session.track(composed)
    if written!=count or composed.stat().st_size!=2*total:raise LocalAiError('validation_failed','Elastic full timeline/text coverage differs.')
    session.sha(composed)
    for index in range(count):session.discard(session.name(f'e-body-{index}.pcm'));session.discard(session.name(f'e-body-{index}.json'))
    progress('level',0,total);leveled=session.name('speech-'+generation+'.pcm')
    (integrated,peak),level_warnings=service.audio.level(composed,leveled,control,total);session.track(leveled)
    with leveled.open('rb') as stream:
        for a,b in mandatory:
            stream.seek(a*2);remaining=2*(b-a)
            while remaining:
                control.check();raw=stream.read(min(32768,remaining))
                if not raw or any(raw):raise LocalAiError('validation_failed','Elastic narration invades a retained mandatory pause.')
                remaining-=len(raw)
    warnings.update(level_warnings)
    service.last_receipt['tutorial_v2'].update(elastic_blocks=len(blocks),synthesized_phrases=written,schedule_sha256=digest.hexdigest(),
        maximum_absolute_delta=max(s.maximum_absolute_delta for s in schedules),mandatory_exclusions=mandatory,
        tempo_policy=block_timing.ADAPTIVE_TEMPO.name,maximum_tempo=str(max(s.tempo for s in schedules)),
        tempo_distribution=[dict(block=s.block,tempo=str(s.tempo),source_samples=s.end-s.start,
            measured_body_samples=sum(p.measured_body_samples for p in s.placements),maximum_absolute_delta=s.maximum_absolute_delta,
            warnings=s.warnings) for s in schedules])
    progress('level',total,total)
    return SpeechComposition(leveled,total,session.sha(leveled),peak,integrated,tuple(sorted(warnings)),session.path.name,protected_silence=mandatory)
