"""Deterministic complete-utterance STT at measured hard-silence boundaries.

Final Qwen route only. Source support is established BEFORE recognition; there
is no search for a favorable ASR/VAD phase, text reroll or artificial word seam.
The accepted source-support/VAD predicate and source-clock anchors stay intact.
"""
from dataclasses import asdict
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from mediaforge.application.contracts.local_ai import LocalAiError,RecognitionRequest,SpeechSegment
from mediaforge.features.dub.alignment import Support,ceil,floor,qualify_core,POLICY as ANCHOR_POLICY
from mediaforge.features.dub.practical_timing import admit_hard_island,POLICY as PRACTICAL_POLICY
from mediaforge.features.dub.quiet_support import estimated_support,POLICY as QUIET_POLICY
from mediaforge.features.dub.observation_join import windows as observation_windows,join as join_observations,POLICY as OBSERVATION_POLICY
from mediaforge.features.dub.speech import normalized
from mediaforge.features.dub import observation_envelopes
from mediaforge.application.services.tutorial_speech import source_phrase_rows,binding as tutorial_binding

POLICY='source-phrase-stt-v7-coherent-envelope-hard-silence-then-verified-phrase-pauses'


def binding():
    from mediaforge.features.dub import practical_timing
    from mediaforge.features.dub import quiet_support
    from mediaforge.features.dub import observation_join
    return dict(policy=POLICY,sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                quiet=QUIET_POLICY,quiet_sha256=hashlib.sha256(Path(quiet_support.__file__).read_bytes()).hexdigest(),
                admission=PRACTICAL_POLICY,admission_sha256=hashlib.sha256(Path(practical_timing.__file__).read_bytes()).hexdigest(),
                envelopes=observation_envelopes.POLICY,envelopes_sha256=hashlib.sha256(Path(observation_envelopes.__file__).read_bytes()).hexdigest(),
                observation=OBSERVATION_POLICY,observation_sha256=hashlib.sha256(Path(observation_join.__file__).read_bytes()).hexdigest())


def refuse(detail):
    raise LocalAiError('speech_boundary_uncertain',detail)


def island_window(support,index,samples):
    current=support[index]
    if current.end-current.start>52*16000:
        refuse('A complete source utterance exceeds two bounded observations; no word clipping.')
    begin=max(0,ceil(current.start)-32000)
    end=min(samples,floor(current.end)+32000)
    if index:
        begin=max(begin,ceil((support[index-1].end+current.start)/2))
    if index+1<len(support):
        end=min(end,floor((current.end+support[index+1].start)/2))
    if not 0<=begin<end<=samples or end-begin>56*16000 or begin>current.start or end<current.end:
        refuse('Complete source support lacks one bounded context window.')
    return begin,end


def observe_complete(stream,begin,end,index,providers,session,control,progress,samples,*,identity=None):
    observations=[]
    parents=[]
    for part,(a,b) in enumerate(observation_windows(begin,end)):
        control.check();name=f'envelope-native-{index}-{part}.json';cached=session.get(name)
        if cached is None:
            progress('recognize',a,samples);stream.seek(a*2);raw=stream.read((b-a)*2)
            if len(raw)!=(b-a)*2:refuse('Complete native observation context is unavailable.')
            result,words,confidence,activity=providers.recognize_words_with_activity(RecognitionRequest(raw,a),control)
            if result.window_samples!=b-a:refuse('Recognition context clock differs.')
            cached=dict(window=[a,b],rows=[asdict(row) for row in result.segments],
                words=words,confidence=confidence,activity=activity,policy=POLICY,identity=identity)
            session.put(name,cached)
        if cached.get('window')!=[a,b] or cached.get('policy')!=POLICY or cached.get('identity')!=identity:
            refuse('Native observation checkpoint identity differs.')
        rows=cached.get('rows',());confidence=cached.get('confidence',());words=cached.get('words',())
        if (not 0<len(rows)<=64 or len(confidence)!=len(rows) or any(not -0.7<=c.get('avg_logprob',float('-inf'))<=0
                or not 0<=c.get('no_speech_prob',1)<=.1 for c in confidence)):
            refuse('Native recognition confidence is insufficient for automatic timing admission.')
        if normalized(' '.join(r['text'] for r in rows))!=normalized(' '.join(w['text'] for w in words)):
            refuse('Native complete words do not corroborate their original recognized rows.')
        row_indices=observation_envelopes.native_word_rows(rows,words)
        digest=hashlib.sha256(json.dumps(cached,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
        parents.append(tuple(dict(checkpoint=name,sha256=digest,word=i,row=row_indices[i]) for i in range(len(words))))
        observations.append(cached)
    if len(observations)==1:return dict(observations[0],word_parents=parents[0])
    joined=join_observations(*observations,begin,end)
    # join() selects these same whole objects after its unchanged seam checks.
    refs=[]
    for word in joined['words']:
        part=0 if Fraction(word['start']+word['end'],2)<begin+28*16000 else 1
        matches=[i for i,w in enumerate(observations[part]['words']) if w is word]
        if len(matches)!=1:refuse('Joined word lacks one immutable native parent.')
        refs.append(parents[part][matches[0]])
    return dict(joined,window=[begin,end],word_parents=tuple(refs))


def prepare_envelopes(support,ledger,samples):
    zeros=[(Fraction(a*16000,ledger['rate']),Fraction(b*16000,ledger['rate'])) for a,b in ledger['zeros']]
    hard_support=tuple(support)
    support,combined,estimated_centers=estimated_support(ledger,support)
    brackets=[(Fraction(a*16000,ledger['rate']),Fraction(b*16000,ledger['rate'])) for a,b in combined] if combined else zeros
    if not 0<len(support)<=5000:refuse('No bounded set of source speech islands.')
    envelopes=observation_envelopes.plan(support,samples)
    return dict(support=support,hard_support=hard_support,zeros=zeros,brackets=brackets,envelopes=envelopes,
                fingerprint=observation_envelopes.fingerprint(envelopes,support,samples),
                # Quiet proposals schedule observations. They are NOT already
                # qualified pauses or independently complete linguistic units.
                proposed_pauses=[(Fraction(a*16000,ledger['rate']),Fraction(b*16000,ledger['rate'])) for a,b in estimated_centers],
                pauses=[])


def phrase_supports(prepared,index):
    """Intersect one coherent observation with immutable HARD source supports.

    Provisional nonzero quiet cuts inside it are validated jointly only after
    complete ASR/word evidence exists. An original hard zero is never bridged.
    Envelope edges still use their original proposed brackets and must pass the
    unchanged outer waveform/VAD/word-confidence predicate before yielding.
    """
    envelope=prepared['envelopes'][index]
    first=prepared['support'][envelope.first];last=prepared['support'][envelope.last]
    pieces=[];origins=[]
    for ordinal,hard in enumerate(prepared['hard_support']):
        if hard.native_rate!=first.native_rate or hard.native_rate!=last.native_rate:
            refuse('Coherent phrase source-rate identity differs.')
        a=max(hard.native_start,first.native_start);b=min(hard.native_end,last.native_end)
        if a<b:
            pieces.append(Support(Fraction(a*16000,hard.native_rate),Fraction(b*16000,hard.native_rate),a,b,hard.native_rate))
            origins.append(ordinal)
    if not pieces or len(pieces)>5000:refuse('Coherent phrase lacks bounded original hard-source coverage.')
    return tuple(pieces),tuple(origins)


def bind_envelope_plan(prepared,session):
    value=dict(prepared['fingerprint'],recognition=binding())
    previous=session.get('observation-plan.json')
    if previous is None:session.put('observation-plan.json',value)
    elif previous!=value:refuse('Frozen observation plan/checkpoint identity differs.')


def admit_envelope(prepared,index,stream,samples,providers,session,control,progress):
    """Atomic envelope admission: no child row escapes before all children pass.

    The diagnostic controller calls this SAME production operation and propagates
    its first error. Context contains no transcript and never grants retry trust.
    """
    envelope=prepared['envelopes'][index];begin,end=envelope.begin,envelope.stop
    state=dict(envelope=index,first=envelope.first,last=envelope.last,
               source_range=[str(envelope.start),str(envelope.end)],window=[begin,end],stage='observation')
    try:
        control.check()
        identity=dict(plan=prepared['fingerprint'],envelope=envelope.record(),runtime=providers.binding(),recognition=binding())
        cached=observe_complete(stream,begin,end,index,providers,session,control,progress,samples,identity=identity)
        state['stage']='phrase_assignment'
        pieces,origins=phrase_supports(prepared,index)
        phrase=observation_envelopes.Envelope(0,len(pieces)-1,pieces[0].start,pieces[-1].end,begin,end)
        views=observation_envelopes.assign(phrase,pieces,cached['words'],
                                          ' '.join(r['text'] for r in cached['rows']))
        stream.seek(begin*2);raw=stream.read((end-begin)*2)
        if len(raw)!=(end-begin)*2:refuse('Complete envelope PCM context is unavailable.')
        output=[];pauses=[]
        for view in views:
            control.check();source=pieces[view.index];text=view.text
            state.update(stage='timing',support_index=origins[view.index],source_range=[str(source.start),str(source.end)])
            if len(text)>4000 or len(text.encode('utf-8'))>4096:refuse('Complete utterance text exceeds its bounded contract.')
            if abs(source.start-view.words[0]['start'])>8000 or abs(source.end-view.words[-1]['end'])>8000:
                refuse('Complete source/ASR edge disagreement exceeds 500 ms.')
            activity=cached.get('activity')
            if activity is None:refuse('Combined native word/activity evidence is incomplete.')
            outer=not any(b==source.start for a,b in prepared['zeros']) or not any(a==source.end for a,b in prepared['zeros'])
            parts,excluded=admit_hard_island(source,activity,prepared['brackets'],(begin,end),view.words,text,raw,
                estimated_outer=outer,file_samples=samples,hard_zeros=prepared['zeros'])
            pauses.extend(excluded)
            if len(pauses)>5000:refuse('Estimated pause ledger exceeds its bound.')
            for part_index,part in enumerate(parts):
                if not any(w['start']<w['end'] for w in part.words):
                    refuse('Point-only word estimates lack an independently corroborated speech part.')
                refs=[dict(cached['word_parents'][view.word_indices[i]],envelope_word=view.word_indices[i])
                      for i,w in enumerate(view.words) if any(w is item for item in part.words)]
                if len(refs)!=len(part.words):refuse('Derived part lacks complete native parent references.')
                anchor=dict(start=ceil(part.start*3),end=floor(part.end*3),source_start=str(part.start),
                    source_end=str(part.end),correction=str(part.start-part.words[0]['start']),
                    raw=[],reanalysis=None,activity=part.evidence,
                    derived=dict(schema='support-view-v1-native-parent-references',envelope=index,
                                 plan=prepared['fingerprint']['sha256'],support_index=origins[view.index],words=refs,
                                 phrase_piece=[source.native_start,source.native_end,source.native_rate],
                                 word_time_basis='estimates-not-acoustic-boundaries'),
                    policy=ANCHOR_POLICY,recognition_policy=POLICY,estimated=part.estimated)
                if anchor['end']<=anchor['start']:refuse('Source speech has no exact 48 kHz capacity.')
                output.append((SpeechSegment(part.words[0]['start'],part.words[-1]['end'],part.text),(index,origins[view.index],part_index),anchor))
        return tuple(output),tuple(pauses)
    except BaseException as error:
        if not hasattr(error,'admission_context'):
            error.admission_context=dict(state,**getattr(error,'word_facts',{}))
        raise


def source_island_rows(support,ledger,extraction,samples,providers,session,control,progress):
    control.check();prepared=prepare_envelopes(support,ledger,samples)
    bind_envelope_plan(prepared,session)
    pauses=list(prepared['pauses']);estimated=bool(pauses)
    with extraction.open('rb') as stream:
        for index in range(len(prepared['envelopes'])):
            rows,excluded=admit_envelope(prepared,index,stream,samples,providers,session,control,progress)
            pauses.extend(excluded)
            if len(pauses)>5000:refuse('Estimated pause ledger exceeds its bound.')
            for row in rows:
                estimated|=row[2]['estimated']
                yield row
    timing=dict(policy=PRACTICAL_POLICY,estimated=estimated,pauses=[[str(a),str(b)] for a,b in pauses])
    raw=json.dumps(timing,sort_keys=True,separators=(',',':')).encode('utf-8')
    if len(raw)>2*1024**2:refuse('Qualified timing ledger exceeds its bound.')
    fingerprint=dict(schema='timing-ledger-fingerprint-v1',sha256=hashlib.sha256(raw).hexdigest(),
                     bytes=len(raw),pauses=len(pauses),policy=PRACTICAL_POLICY)
    previous=session.get('timing-ledger.json')
    if previous is None:session.put('timing-ledger.json',fingerprint)
    elif previous!=fingerprint:refuse('Qualified timing ledger differs from its checkpoint.')
    ledger['estimated_timing']=estimated
    ledger['estimated_zeros_16k']=timing['pauses']
    progress('recognize',samples,samples)


def verify_estimated_silence(path,ledger,total,control):
    with path.open('rb') as stream:
        for left,right in ledger.get('estimated_zeros_16k',()):
            begin=floor(Fraction(left)*3);end=ceil(Fraction(right)*3)
            if not 0<=begin<end<=total:refuse('Estimated silence escaped the source clock.')
            stream.seek(begin*2);remaining=(end-begin)*2
            while remaining:
                control.check();raw=stream.read(min(remaining,32768))
                if not raw or any(raw):raise LocalAiError('timing_unfit','Output invades a qualified estimated pause.')
                remaining-=len(raw)
