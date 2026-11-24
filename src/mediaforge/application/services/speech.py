"""C3 speech orchestration. Internal PCM only; no mux, export or QML state."""
from dataclasses import asdict
from contextlib import contextmanager
from fractions import Fraction
import hashlib
import json
import re
import time
from pathlib import Path

from mediaforge.application.contracts.local_ai import (
    LocalAiError, RecognitionRequest, SpeechSegment, SynthesisRequest, TranslationRequest,
)
from mediaforge.application.contracts.speech import SpeechComposition,PhraseSegmentV2
from mediaforge.features.dub.speech import POLICY, fit, group, half_up, owned, reconcile, synthesis_units, windows
from mediaforge.features.dub.alignment import anchored_fit,corrected_tempo,POLICY as ALIGNMENT_POLICY
from mediaforge.application.services.speech_alignment import anchored_rows
from mediaforge.application.services.speech_islands import source_island_rows,binding as island_binding,verify_estimated_silence,source_phrase_rows,tutorial_binding
from mediaforge.application.services import tutorial_speech
from mediaforge.features.dub import phrase_timing

class JobControl:
    def __init__(self,inner):self.inner=inner;self.deadline=time.monotonic()+24*3600
    def __getattr__(self,name):return getattr(self.inner,name)
    def check(self):
        try:self.inner.check()
        except Exception as error:
            if getattr(error,'code',None)=='cancelled':raise LocalAiError('cancelled','Dub job cancelled.') from None
            raise
        if time.monotonic()>self.deadline:raise LocalAiError('provider_timeout','Dub job exceeded its 24-hour bound.')
    @contextmanager
    def starting(self):
        self.check()
        try:
            with self.inner.starting():yield
        except Exception as error:
            if getattr(error,'code',None)=='cancelled':raise LocalAiError('cancelled','Dub job cancelled.') from None
            raise


class DubSpeechService:
    def __init__(self,providers,audio,sessions,source_lease,*,edge=None,tts=None):
        self.providers=providers;self.audio=audio;self.sessions=sessions
        self.source_lease=source_lease;self.edge=edge;self.tts=tts;self.last_receipt=None

    def prepare(self,request,control,progress=lambda *args:None,*,gate_owner=None):
        try:
            result=self._owned_prepare(request,JobControl(control),progress,gate_owner)
            control.check()
            self.last_receipt['status']='internal_prepared'
            return result
        except BaseException:
            if self.last_receipt is not None:
                self.last_receipt['status']='failed_or_cancelled'
                if not request.retain_private_recovery and not control.cleanup_failed:
                    gate=self.providers.gate
                    borrowed=gate_owner is not None and gate.owned_by(gate_owner)
                    token=gate_owner if borrowed else gate.acquire()
                    if token is None:
                        gate.quarantine(self.sessions,'Failed speech session cleanup awaits ownership.')
                        raise LocalAiError('lifecycle_failed','Private session cleanup remains owned.',retained_resources=(self.sessions,))
                    try:self.sessions.retire_if_present(self.last_receipt['session_id'],control)
                    except BaseException as error:
                        gate.poison(token,'Private speech session retirement failed.')
                        raise LocalAiError('lifecycle_failed','Private session retirement failed.',retained_resources=(self.sessions,)) from error
                    finally:
                        if not borrowed and not gate.failure:gate.release(token)
            raise

    def _owned_prepare(self,request,control,progress,gate_owner=None):
        started=time.monotonic();self.last_receipt=None
        if not re.fullmatch(r'[a-zA-Z0-9-]{1,80}',request.job_id):raise LocalAiError('invalid_request','Invalid job generation.')
        if request.voice not in (self.tts.voices if self.tts is not None else ('ruslan','dmitry')):
            raise LocalAiError('invalid_request','Select an explicit supported voice.')
        if self.tts is None and request.voice=='dmitry':
            if request.online_consent_job!=request.job_id or request.online_text_transmission is not True:
                raise LocalAiError('online_consent_required','This job requires explicit online text-transmission consent.')
            if self.edge is None:raise LocalAiError('dependency_unavailable','Dmitry transport is not qualified; no fallback is permitted.')
        plan=request.plan
        if plan.source!=request.source.identity or not 0<plan.duration<=3900 or plan.sample_count!=half_up(plan.duration*48000):
            raise LocalAiError('invalid_timing','Frozen source/output span differs.')
        if plan.requires_span_acknowledgment and not request.acknowledge_clipped_span:
            raise LocalAiError('invalid_request','Explicit acknowledgment of excluded audio span is required.')
        workspace_bytes=plan.sample_count*4+half_up(plan.duration*16000)*2+16*1024**2
        with self.providers.session(control,owner=gate_owner),self.sessions.admission(control,workspace_bytes=workspace_bytes,retry_session=request.retry_session):
            with self.source_lease(plan.source.path,control) as source:
                if source.identity!=plan.source:raise LocalAiError('source_changed','Source identity differs from inspection.')
                # Reauthenticate ALL selected models even when checkpoints exist.
                keys=('whisper-base','argos-en-ru',*(('piper-ruslan',) if self.tts is None and request.voice=='ruslan' else ()))
                with self.providers.selected_models(keys,control) as selected:
                    models={key:asdict(spec) for key,spec in selected.items()}
                    binding=dict(source_sha256=source.identity.sha256,track=plan.audio_index,
                        origin=str(plan.origin),duration=str(plan.duration),samples=plan.sample_count,
                        voice=request.voice,models=models,runtime=self.providers.binding(),ffmpeg=self.audio.binding(),
                        source_clocks=[dict(ledger=t.clock.interval_sha256,sample_rate=t.sample_rate,
                            time_base=str(t.time_base),first=str(t.clock.first),end=str(t.clock.end))
                            for t in request.source.audio_tracks if t.index==plan.audio_index and t.clock is not None],
                        policy=POLICY,alignment=dict(self.audio.support_binding(),
                            orchestration=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                            reconciliation=hashlib.sha256(Path(__file__).with_name('speech_alignment.py').read_bytes()).hexdigest()),
                        edge=getattr(self.edge,'fingerprint',None) if request.voice=='dmitry' else None,
                        tts=self.tts.binding() if self.tts is not None else None,
                        recognition=tutorial_binding() if self.tts is not None else None)
                    with self.sessions.open(request,binding,control) as session:
                        self.last_receipt=dict(session_id=session.path.name,status='running',checkpoint_hits=0)
                        try:
                            result=self._prepare(request,session,control,progress)
                            control.check()
                            self.last_receipt.update(status='prepared_pending_retirement',checkpoint_hits=session.hits,
                                seconds=time.monotonic()-started,samples=result.samples,sha256=result.sha256,
                                model_resources=dict(self.providers.statistics),peak_dbtp=result.peak,
                                integrated_lufs=result.integrated_lufs,warnings=result.warnings)
                            return result
                        except BaseException:
                            self.last_receipt.update(status='failed_or_cancelled',checkpoint_hits=session.hits,
                                seconds=time.monotonic()-started)
                            raise

    def _prepare(self,request,session,control,progress):
        plan=request.plan;total=plan.sample_count
        # Each retry regenerates extraction/composition, reusing only authenticated
        # semantic rows and neutral voice fragments. Never adopt a partial output.
        generation=hashlib.sha256(request.job_id.encode()).hexdigest()[:16]
        extraction=session.name('extract-'+generation+'.pcm')
        if extraction.exists():raise LocalAiError('invalid_request','Retry requires a fresh job generation.')
        session.admit(half_up(plan.duration*16000)*2+total*4+32*1024**2)
        progress('extract',0,total)
        samples=self.audio.extract(plan.source.path,plan,extraction,control);session.track(extraction)
        progress('extract',total,total)
        track=next((t for t in request.source.audio_tracks if t.index==plan.audio_index),None)
        if track is None:raise LocalAiError('speech_boundary_uncertain','Selected source track is unavailable.')
        progress('support',0,total)
        ledger,support=self.audio.source_support(plan.source.path,plan,track,extraction,control)
        frozen=json.loads(json.dumps(ledger))
        if self.tts is not None:
            # Recompute native features on EVERY attempt. The bounded HMAC row
            # authenticates their canonical identity, not a giant cached object
            # or untrusted source facts recovered after restart.
            raw=json.dumps(ledger,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
            if len(raw)>4*1024**2:raise LocalAiError('disk_budget','Bounded source feature metadata exceeded 4 MiB.')
            frozen=dict(schema='source-ledger-fingerprint-v1',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        existing=session.get('support-ledger.json')
        if existing is None:session.put('support-ledger.json',frozen)
        elif existing!=frozen:raise LocalAiError('checkpoint_ineligible','Source support ledger differs.')
        progress('support',total,total)

        def recognized():
            previous=None;previous_window=None;previous_emit=None;count=0
            with extraction.open('rb') as stream:
                for index,window in enumerate(self.audio.recognition_windows(extraction,samples,control)):
                    control.check();progress('recognize',window.core_start,samples)
                    name=f'recognize-{index}.json';cached=session.get(name)
                    if cached is None:
                        stream.seek(window.start*2);raw=stream.read((window.end-window.start)*2)
                        if len(raw)!=(window.end-window.start)*2:raise LocalAiError('invalid_timing','Incomplete recognition window.')
                        row=self.providers.recognize(RecognitionRequest(raw,window.start),control)
                        if row.window_samples!=window.end-window.start:raise LocalAiError('invalid_output','STT window size differs.')
                        current=row.segments
                    else:current=tuple(SpeechSegment(**s) for s in cached)
                    chosen=owned(window,current)  # validate before persisting/reusing
                    if previous is not None:
                        left,chosen=reconcile(previous_window,previous,window,current,previous_emit)
                        if session.get(f'recognize-{index-1}.json') is None:
                            session.put(f'recognize-{index-1}.json',[asdict(s) for s in previous])
                        for s in left:
                            count+=1
                            if count>5000:raise LocalAiError('resource_unavailable','Segment count exceeds 5000.')
                            yield s
                    previous=current;previous_window=window;previous_emit=chosen
                if previous is not None:
                    name=f'recognize-{index}.json'
                    if session.get(name) is None:session.put(name,[asdict(s) for s in previous])
                    for s in previous_emit:yield s
            progress('recognize',samples,samples)

        # One-row lookahead: no whole transcript/voice list and no process queue.
        if self.tts is not None:
            recognized_rows=grouped=None
            rows=session.own_reader(source_phrase_rows(ledger,extraction,samples,self.providers,session,control,progress,total_samples=total))
        else:
            recognized_rows=session.own_reader(recognized())
            grouped=session.own_reader(group(recognized_rows))
            rows=session.own_reader(anchored_rows(grouped,support,ledger,extraction,samples,self.providers,session,control,progress))
        if self.tts is not None:
            # Finish all CPU inference before reserving the persistent CUDA
            # engine. Journals remain bounded authenticated per-unit objects;
            # no full transcript list or concurrent inference worker is kept.
            count=0
            for segment,lineage,anchor in rows:
                self._journal_translation(session,count,segment,lineage,anchor,control,progress)
                count+=1
                if count>5000:raise LocalAiError('resource_unavailable','Too many speech units.')
            for reader in (rows,grouped,recognized_rows):
                if reader is not None:reader.close()
            if not count:raise LocalAiError('no_speech','No qualified English speech was recognized.')
            def staged():
                for index in range(count):
                    row=session.get(f'v2-segment-{index}.json')
                    if row is None:raise LocalAiError('checkpoint_ineligible','CPU journal is incomplete.')
                    yield PhraseSegmentV2(row['start_sample'],row['end_sample'],row['text']),tuple(row['lineage']),row['anchor']
            tutorial=tutorial_speech.read_record(session,'v2-plan')
            if tutorial is None or tutorial['phrases']!=count:raise LocalAiError('checkpoint_ineligible','Tutorial phrase coverage plan is incomplete.')
            session.admit_records(4*count+8,12000*count)
            self.last_receipt['tutorial_v2']={k:v for k,v in tutorial.items() if k not in ('pauses','coverage')}
            self.last_receipt['tutorial_v2']['coverage_windows']=len(tutorial['coverage'])
            session.discard(extraction)  # CPU readers are closed; Retry reextracts.
            from mediaforge.application.services.tutorial_composition import compose
            return compose(self,request,session,control,progress,staged(),ledger,generation,count,tutorial)
        return self._compose(request,session,control,progress,rows,ledger,generation)

    def _journal_translation(self,session,index,segment,lineage,anchor,control,progress):
        prefix='v2-' if self.tts is not None else ''
        journal=dict(start_sample=segment.start_sample,end_sample=segment.end_sample,
            text=segment.text,lineage=list(lineage),anchor=anchor)
        accepted=session.get(f'{prefix}segment-{index}.json')
        if accepted is None:session.put(f'{prefix}segment-{index}.json',journal)
        elif accepted!=journal:raise LocalAiError('checkpoint_ineligible','Accepted speech lineage differs.')
        control.check();progress('translate',index,0)
        translated=session.get(f'{prefix}translate-{index}.json')
        english_sha=hashlib.sha256(segment.text.encode()).hexdigest()
        if translated is None:
            try:result=self.providers.translate(TranslationRequest(segment.text),control)
            except LocalAiError as error:
                reference=getattr(error,'reference_spans',())
                if reference:
                    name=f'{prefix}translation-refusal-{index}.json'
                    record=dict(english_sha256=english_sha,original_argos_partitions=[asdict(x) for x in reference],
                                code=str(error.code))
                    if session.get(name) is None:session.put(name,record)
                raise
            translated=dict(english_sha256=english_sha,text=result.text)
            if getattr(result,'glossary_policy',''):
                translated.update(glossary_policy=result.glossary_policy,
                    original_argos_partitions=[asdict(x) for x in result.reference_spans])
            if prefix:translated.update(schema='tutorial-translation-v2',partitions=[asdict(x) for x in result.spans])
            session.put(f'{prefix}translate-{index}.json',translated)
        if translated['english_sha256']!=english_sha:raise LocalAiError('checkpoint_ineligible','Translation lineage differs.')
        if prefix:
            if not isinstance(translated.get('text'),str) or not translated['text'].strip() or '\ufffd' in translated['text']:
                raise LocalAiError('translation_failed','A complete tutorial phrase has no usable Russian translation.')
            cursor=0
            for item in translated.get('partitions',()):
                if (item['start']!=cursor or not cursor<item['end']<=len(segment.text) or item['source']!=segment.text[cursor:item['end']]
                        or item['source'].strip() and not item['russian'].strip()):raise LocalAiError('translation_failed','Tutorial translation partition is incomplete.')
                cursor=item['end']
            if cursor!=len(segment.text):raise LocalAiError('translation_failed','Tutorial translation character coverage differs.')
        return translated

    def _compose(self,request,session,control,progress,rows,ledger,generation,engine=None,row_count=0,*,tutorial=None):
        plan=request.plan;total=plan.sample_count
        rate=self.tts.sample_rate if engine is not None else 22050
        prefix='v2-' if tutorial is not None else ''
        if tutorial is not None:
            value=tutorial['pauses'];pauses=phrase_timing.PauseMap(value['total'],tuple(map(tuple,value['hard'])),tuple(map(tuple,value['quiet'])),tuple(map(tuple,value['mandatory'])))
            if pauses.total!=total:raise LocalAiError('invalid_timing','Tutorial/output clocks differ.')
            warnings_initial={'estimated_phrase_timing'}
            if tutorial['uncertainty_count']:warnings_initial.add('recognition_uncertain')
        else:warnings_initial=set()
        current=next(rows,None)
        if current is None:raise LocalAiError('no_speech','No qualified English speech was recognized.')
        composed=session.name(prefix+'compose-'+generation+'.pcm');position=0;warnings=warnings_initial;segments=0
        voice_artifacts=[];schedule_sha=hashlib.sha256()
        with session.pcm(composed.name,total) as writer:
            while current is not None:
                following=next(rows,None);segment,lineage,anchor=current;index=segments;segments+=1
                if segments>5000:raise LocalAiError('resource_unavailable','Too many speech units.')
                if engine is None:
                    translated=self._journal_translation(session,index,segment,lineage,anchor,control,progress)
                else:
                    # CPU phases are closed. An incomplete journal cannot start
                    # another inference child beside the resident CUDA engine.
                    translated=session.get(f'{prefix}translate-{index}.json')
                    if translated is None or translated['english_sha256']!=hashlib.sha256(segment.text.encode()).hexdigest():
                        raise LocalAiError('checkpoint_ineligible','Completed CPU translation journal differs.')
                neutral=session.name(f'{prefix}neutral-{index}.pcm');voice=session.get(f'{prefix}voice-{index}.json')
                if tutorial is not None and voice is not None and not session.owns_name(neutral.name):
                    # Retirement can stop after one exact object. Drop only its
                    # still-owned cache row; an unknown lookalike is refused.
                    if neutral.exists():raise LocalAiError('storage_refused','Unowned tutorial neutral object.')
                    session.discard(session.name(f'{prefix}voice-{index}.json'));voice=None
                if voice is None:
                    if tutorial is not None and session.owns_name(neutral.name):session.sha(neutral);session.discard(neutral)
                    parts=[];size=0;units=[];text_cursor=0
                    for unit in synthesis_units(translated['text']):
                        control.check();progress('synthesize',index,row_count)
                        fragment=(engine.synthesize(unit,request.voice) if engine is not None else
                                  self.providers.synthesize(SynthesisRequest(unit),control) if request.voice=='ruslan'
                                  else self.edge.synthesize(unit,request,control,workspace=session))
                        if fragment.sample_rate!=rate or fragment.speaker!=request.voice:
                            raise LocalAiError('invalid_output','Voice identity/sample format differs.')
                        size+=len(fragment.pcm16)
                        if size>120*rate*2 or size>16*1024**2:raise LocalAiError('invalid_output','Voice unit exceeds fragment bound.')
                        parts.append(fragment.pcm16)
                        if tutorial is not None:
                            completion=fragment.completion
                            if completion is None or completion.reason!='normal_eos' or completion.terminal_eos is not True:
                                raise LocalAiError('invalid_output','Tutorial synthesis lacks authenticated terminal EOS.')
                            units.append(dict(first=text_cursor,last=text_cursor+len(unit),text_sha256=hashlib.sha256(unit.encode()).hexdigest(),pcm_sha256=hashlib.sha256(fragment.pcm16).hexdigest(),samples=fragment.samples,completion=asdict(completion)))
                            text_cursor+=len(unit)
                    session.put_bytes(neutral.name,b''.join(parts))
                    voice=dict(sha256=session.sha(neutral),bytes=size,text_sha256=hashlib.sha256(translated['text'].encode()).hexdigest())
                    if tutorial is not None:
                        if text_cursor!=len(translated['text']):raise LocalAiError('invalid_output','Tutorial synthesis character coverage differs.')
                        voice.update(schema='tutorial-voice-v2',units=units)
                    session.put(f'{prefix}voice-{index}.json',voice)
                progress('synthesize',index+1,row_count)
                if (voice['sha256']!=session.sha(neutral) or voice['bytes']!=neutral.stat().st_size
                        or voice['text_sha256']!=hashlib.sha256(translated['text'].encode()).hexdigest()):
                    raise LocalAiError('checkpoint_ineligible','Voice checkpoint differs.')
                if tutorial is not None:
                    cursor=0;voice_samples=0
                    if voice.get('schema')!='tutorial-voice-v2':raise LocalAiError('checkpoint_ineligible','Tutorial voice schema differs.')
                    for unit in voice.get('units',()):
                        if (unit['first']!=cursor or not cursor<unit['last']<=len(translated['text'])
                                or unit['text_sha256']!=hashlib.sha256(translated['text'][cursor:unit['last']].encode()).hexdigest()
                                or unit['completion']['reason']!='normal_eos' or unit['completion']['terminal_eos'] is not True):
                            raise LocalAiError('checkpoint_ineligible','Tutorial EOS/text coverage differs.')
                        cursor=unit['last'];voice_samples+=unit['samples']
                    if cursor!=len(translated['text']) or voice_samples*2!=voice['bytes']:raise LocalAiError('checkpoint_ineligible','Tutorial voice sample coverage differs.')
                    session.put(f'v2-eos-{generation}-{index}.json',dict(voice,ordinal=index,phrase_sha256=anchor['phrase']['sha256']))
                resampled=session.name(f'resample-{generation}-{index}.pcm')
                session.admit(120*48000*2+16*1024**2)
                self.audio.convert(neutral,resampled,control,rate=rate);session.track(resampled)
                raw=resampled.read_bytes()
                if len(raw)>16*1024**2:raise LocalAiError('invalid_output','Decoded voice exceeds bound.')
                import array
                values=array.array('h',raw)
                if not values or not any(values):raise LocalAiError('invalid_output','Silent neutral voice refused.')
                first=next(i for i,v in enumerate(values) if v);last=len(values)-next(i for i,v in enumerate(reversed(values)) if v)
                # Remove exact exterior zeros only. Every nonzero/internal sample
                # remains. Add full guards AFTER tempo, charged to source capacity.
                trimmed=raw[first*2:last*2];voice_input=session.put_bytes(f'trim-{generation}-{index}.pcm',trimmed)
                start=anchor['start']
                end=min(anchor['end'],following[2]['start'] if following is not None else total,total)
                self.last_receipt['fit_candidate']=dict(segment=index+1,start_sample=start,
                    slot_end_sample=end,neutral_samples=len(trimmed)//2)
                if tutorial is not None:
                    from types import SimpleNamespace
                    phrase=SimpleNamespace(ordinal=index,start=start,end=anchor['end'])
                    next_phrase=SimpleNamespace(start=following[2]['start']) if following is not None else None
                    gap=next_phrase.start-phrase.end if next_phrase else 0
                    loan=min(19200,max(0,gap-3840)) if 0<gap<48000 else 0
                    qualified=loan>0 and tutorial_speech.loan_qualified(session,pauses,phrase.end,phrase.end+loan)
                    slot=phrase_timing.slot_for(phrase,next_phrase,pauses,qualified)
                    self.last_receipt['fit_candidate']['slot']=asdict(slot)
                    try:decision=phrase_timing.fit_phrase(slot,len(trimmed)//2)
                    except Exception as error:
                        self.last_receipt['fit_candidate'].update(getattr(error,'timing_facts',{}));raise
                    end=decision.end
                else:decision=anchored_fit(start,end,len(trimmed)//2)
                body_capacity=decision.usable-1920
                self.last_receipt['fit_candidate'].update(usable_samples=decision.usable,ratio=str(decision.ratio))
                actual_ratio=decision.ratio
                if decision.accelerated_warning:warnings.add('speech_accelerated')
                fitted=voice_input
                if decision.ratio>1:
                    fitted=session.name(f'fit-{generation}-{index}.pcm')
                    count=self.audio.convert(voice_input,fitted,control,rate=48000,tempo=decision.ratio,
                        maximum=body_capacity+4800);session.track(fitted)
                    if count>body_capacity:
                        # At most one deterministic correction. Never cut a tail.
                        # WSOLA emits whole analysis blocks, not an exact n/r
                        # count. Reserve one 2048-sample analysis block in this
                        # ONE deterministic correction; still refuse above 1.25.
                        try:correction,proposed=corrected_tempo(decision.ratio,count,body_capacity)
                        except Exception as error:
                            if tutorial is None or getattr(error,'code',None)!='timing_unfit':raise
                            raise LocalAiError('phrase_timing_unfit',f'Phrase {index+1}: measured {count} samples exceed body {body_capacity} at the 1.25 tempo cap.') from error
                        self.last_receipt['fit_candidate'].update(measured_samples=count,proposed_correction=str(proposed),correction=str(correction))
                        # A conservative WSOLA-block reserve is not proof that
                        # the allowed cap cannot fit. Use the ONE correction at
                        # no more than 1.25; measured overflow still refuses.
                        fitted=session.name(f'fit-corrected-{generation}-{index}.pcm')
                        count=self.audio.convert(voice_input,fitted,control,rate=48000,tempo=correction,
                            maximum=body_capacity+4800);session.track(fitted)
                        if count>body_capacity:raise LocalAiError('phrase_timing_unfit' if tutorial is not None else 'timing_unfit',f'Phrase {index+1}: measured complete speech {count} samples still exceeds body {body_capacity}; no tail was cut.')
                        actual_ratio=correction
                if actual_ratio>Fraction(23,20):warnings.add('speech_accelerated')
                progress('compose',start,total);writer.silence(start-position)
                writer.silence(960)
                for block in session.read_blocks(fitted):writer.write(block)
                writer.silence(960)
                position=start+1920+fitted.stat().st_size//2
                if position>start+decision.usable:raise LocalAiError('phrase_timing_unfit' if tutorial is not None else 'timing_unfit','Voice exceeded its independent measured slot.')
                if tutorial is None:
                    session.put(f'fit-{generation}-{index}.json',dict(start=start,slot_end=end,samples=position-start,
                        tempo=str(actual_ratio),lineage=list(lineage),fitted_sha256=session.sha(fitted),policy=ALIGNMENT_POLICY,anchor=anchor))
                if tutorial is not None:
                    fit_record=dict(schema='tutorial-fit-v2',ordinal=index,phrase_sha256=anchor['phrase']['sha256'],slot=asdict(slot),
                        neutral_sha256=voice['sha256'],neutral_samples=len(trimmed)//2,tempo=str(actual_ratio),start=start,end=position,
                        loan_used=max(0,position-anchor['end']),fitted_sha256=session.sha(fitted))
                    session.put(f'v2-schedule-{generation}-{index}.json',fit_record);schedule_sha.update(tutorial_speech.encoded(fit_record))
                    voice_artifacts.extend((neutral,session.name(f'{prefix}voice-{index}.json')))
                for artifact in {resampled,voice_input,fitted}:session.discard(artifact)
                current=following
            writer.silence(total-position)
        session.track(composed)
        if composed.stat().st_size!=total*2:raise LocalAiError('validation_failed','Composition sample count differs.')
        if tutorial is not None:
            if segments!=tutorial['phrases']:raise LocalAiError('validation_failed','Tutorial phrase output coverage differs.')
            self.last_receipt['tutorial_v2'].update(synthesized_phrases=segments,schedule_sha256=schedule_sha.hexdigest(),mandatory_exclusions=pauses.mandatory)
            # Complete composition exists and is hashed; retire known neutral
            # objects/cache rows BEFORE allocating the full-length leveled copy.
            session.sha(composed)
            for artifact in voice_artifacts:session.discard(artifact)
        progress('level',0,total);leveled=session.name('speech-'+generation+'.pcm')
        (integrated,peak),level_warnings=self.audio.level(composed,leveled,control,total);session.track(leveled)
        if tutorial is None:
            self.audio.validate_source_silence(leveled,ledger,total,control)
            verify_estimated_silence(leveled,ledger,total,control)
        else:
            with leveled.open('rb') as stream:
                for a,b in pauses.mandatory:
                    stream.seek(a*2);remaining=(b-a)*2
                    while remaining:
                        control.check();raw=stream.read(min(32768,remaining))
                        if not raw or any(raw):raise LocalAiError('validation_failed','Tutorial composition invades a mandatory pause.')
                        remaining-=len(raw)
        if ledger.get('estimated_timing'):warnings.add('estimated_speech_boundaries')
        warnings.update(level_warnings);progress('level',total,total)
        from mediaforge.features.dub.alignment import floor,ceil
        protected=[(max(0,floor(Fraction(a*48000,ledger['rate']))),min(total,ceil(Fraction(b*48000,ledger['rate']))))
                   for a,b in ledger['zeros']]
        protected.extend((max(0,floor(Fraction(a)*3)),min(total,ceil(Fraction(b)*3)))
                         for a,b in ledger.get('estimated_zeros_16k',()))
        merged=[]
        if tutorial is not None:protected=pauses.mandatory
        for a,b in sorted(protected):
            if a>=b:continue
            if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(merged[-1][1],b))
            else:merged.append((a,b))
        return SpeechComposition(leveled,total,session.sha(leveled),peak,integrated,tuple(sorted(warnings)),session.path.name,
                                 protected_silence=tuple(merged))
