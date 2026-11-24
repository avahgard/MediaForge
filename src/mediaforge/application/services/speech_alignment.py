"""Bounded C3 source-unit reconciliation; providers and PCM owner are injected."""
from dataclasses import asdict
from fractions import Fraction
from mediaforge.application.contracts.local_ai import LocalAiError,RecognitionRequest,SpeechSegment
from mediaforge.features.dub.speech import normalized,windows
from mediaforge.features.dub.alignment import POLICY,ceil,floor,qualify_core

def refuse(detail):raise LocalAiError('speech_boundary_uncertain',detail)

def anchored_rows(rows,support,ledger,extraction,samples,providers,session,control,progress):
    zeros=[(Fraction(a*16000,ledger['rate']),Fraction(b*16000,ledger['rate'])) for a,b in ledger['zeros']]
    pending=next(rows,None);used=0;active_window=None;activity=None
    def hits(segment):return [i for i,s in enumerate(support) if s.start<segment.end_sample and s.end>segment.start_sample]
    def pcm(a,b):
        if not 0<=a<b<=samples or b-a>480000:refuse('Boundary observation exceeds its qualified source window.')
        with extraction.open('rb') as stream:stream.seek(a*2);raw=stream.read((b-a)*2)
        if len(raw)!=(b-a)*2:refuse('Boundary source representation is incomplete.')
        return raw
    while pending is not None:
        control.check();segment,lineage=pending;indices=hits(segment)
        if not indices or indices[0]!=used:refuse('ASR/source support assignment is incomplete or ambiguous.')
        block=[pending];last=indices[-1];pending=next(rows,None)
        while pending is not None:
            other=hits(pending[0])
            if not other:refuse('ASR row has no source support.')
            if other[0]>last:break
            if other[0]<used:refuse('ASR source units overlap an earlier accepted unit.')
            last=max(last,other[-1]);block.append(pending);pending=next(rows,None)
            if len(block)>64:refuse('Utterance reanalysis exceeds its row bound.')
        text=' '.join(s.text for s,_ in block);lineage=tuple(n for _,ns in block for n in ns)
        if len(text.encode('utf-8'))>4096 or support[last].end-support[used].start>480000:
            refuse('Ambiguous source utterance exceeds the bounded reanalysis span.')
        fresh=[]
        if last==used:
            fresh=[SpeechSegment(block[0][0].start_sample,block[-1][0].end_sample,text)]
        else:
            # One predetermined re-recognition per affected support island. Keep
            # real context from adjacent hard pauses, never another utterance.
            for i in range(used,last+1):
                s=support[i];a=max(0,ceil(s.start)-32000);b=min(samples,floor(s.end)+32000)
                if i:a=max(a,ceil((support[i-1].end+s.start)/2))
                if i+1<len(support):b=min(b,floor((s.end+support[i+1].start)/2))
                key=f'boundary-recognize-{i}.json';cached=session.get(key)
                if cached is None:
                    progress('recognize',a,samples)
                    result=providers.recognize(RecognitionRequest(pcm(a,b),a),control)
                    cached=dict(window=[a,b],rows=[asdict(row) for row in result.segments])
                    session.put(key,cached)
                if cached['window']!=[a,b] or not 0<len(cached['rows'])<=64:refuse('Reanalysis identity or row count differs.')
                units=[SpeechSegment(**row) for row in cached['rows']]
                if any(not a<=row.start_sample<row.end_sample<=b for row in units):refuse('Reanalysis clock escaped its source context.')
                fresh.append(SpeechSegment(units[0].start_sample,units[-1].end_sample,' '.join(row.text for row in units)))
            if normalized(' '.join(s.text for s in fresh))!=normalized(text):refuse('Reanalysis does not corroborate complete ordered word coverage.')
        for i,raw in zip(range(used,last+1),fresh):
            s=support[i]
            if abs(s.start-raw.start_sample)>8000:refuse('Local source/ASR correction exceeds 500 ms.')
            # Predetermined core26/context2 window; never search for a favorable
            # VAD phase. Require this window to cover the complete source unit.
            index=int(((s.start+s.end)/2)//416000)
            window=next((w for j,w in enumerate(windows(samples)) if j==index),None)
            if window is None or window.start>s.start or window.end<s.end:refuse('Complete source support lacks bounded activity context.')
            if active_window!=index:
                key=f'activity-{index}.json';activity=session.get(key)
                if activity is None:
                    progress('activity',window.start,samples)
                    activity=providers.activity(pcm(window.start,window.end),window.start,control)
                    session.put(key,activity)
                active_window=index
            evidence=qualify_core(s,activity,zeros,window=(window.start,window.end))
            anchor=dict(start=ceil(s.start*3),end=floor(s.end*3),source_start=str(s.start),source_end=str(s.end),
                correction=str(s.start-raw.start_sample),raw=[asdict(item) for item,_ in block],
                reanalysis=asdict(raw) if last>used else None,activity=evidence,policy=POLICY)
            if anchor['end']<=anchor['start']:refuse('Source support has no exact 48 kHz capacity.')
            yield raw,lineage,anchor
        used=last+1
    if used!=len(support):refuse('Nonzero source support remains without corroborated complete speech.')
