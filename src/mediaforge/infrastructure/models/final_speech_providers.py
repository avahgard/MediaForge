"""Typed word-confidence evidence for approved final automatic timing only."""
import base64
import math
import sys
import re

from mediaforge.application.contracts.local_ai import LocalAiError,RecognitionResult,SpeechSegment
from mediaforge.infrastructure.models.dub_providers import SpeechProviders


class FinalSpeechProviders(SpeechProviders):
    def recognize_tutorial(self,request,control):
        """Authentic rows are canonical; low scores/word disagreement are quality."""
        row,spec=self._selected('whisper-base','recognize',dict(pcm16=base64.b64encode(request.pcm16).decode(),
            word_support=True,activity_support=True,tutorial_v2=True),control)
        count=len(request.pcm16)//2
        if row.get('samples')!=count or row.get('word_evidence')!='accepted-whisper-base-word-estimates-v2':
            raise LocalAiError('invalid_output','Tutorial native observation identity differs.')
        native=row.get('segments');confidence=row.get('word_segments');words=row.get('words')
        if not isinstance(native,list) or len(native)>128 or not isinstance(confidence,list) or len(confidence)!=len(native) or not isinstance(words,list) or len(words)>512:
            raise LocalAiError('invalid_output','Tutorial native row/metadata inventory differs.')
        for w in words:
            if (not isinstance(w,dict) or type(w.get('start')) is not int or type(w.get('end')) is not int or not 0<=w['start']<=w['end']<=count+320
                    or not isinstance(w.get('text'),str) or not w['text'].strip() or type(w.get('probability')) not in (int,float)
                    or not math.isfinite(w['probability']) or not 0<=w['probability']<=1):
                raise LocalAiError('invalid_output','Malformed optional native word metadata.')
        previous=0;covered=0;rows=[];uncertainty=[]
        for index,(s,c) in enumerate(zip(native,confidence)):
            if not isinstance(s,dict) or not isinstance(c,dict):raise LocalAiError('invalid_output','Malformed tutorial metadata row.')
            a,b=s.get('start'),s.get('end');text=s.get('text');first,last=c.get('word_first'),c.get('word_last')
            if (type(a) is not int or type(b) is not int or not previous<=a<b<=count
                    or not isinstance(text,str) or not text.strip() or len(text)>4000 or '\x00' in text
                    or type(first) is not int or type(last) is not int or first!=covered or not first<=last<=len(words)
                    or any(type(c.get(k)) not in (int,float) or not math.isfinite(c[k]) for k in ('avg_logprob','no_speech_prob'))
                    or not 0<=c['no_speech_prob']<=1):
                raise LocalAiError('invalid_output','Malformed native tutorial row clock/text/confidence.')
            previous=b;covered=last;selected=words[first:last];estimates=[]
            clean=lambda value:re.sub(r'\s+','',value)
            matches=clean(''.join(w['text'] for w in selected))==clean(text)
            if matches and selected:
                spans=[];cursor=0
                for w in selected:
                    n=len(clean(w['text']));spans.append((cursor,cursor+n,w));cursor+=n
                cursor=0
                for token in re.finditer(r'\S+\s*',text):
                    length=len(clean(token.group()));items=[w for x,y,w in spans if max(x,cursor)<min(y,cursor+length)]
                    estimates.append([max(a,min(w['start'] for w in items))*3+request.offset_samples*3,
                                      min(b,max(w['end'] for w in items))*3+request.offset_samples*3]);cursor+=length
            flags=[]
            if c['avg_logprob']<-.7 or c['no_speech_prob']>.1 or any(w['probability']<.8 for w in selected):flags.append('low_native_confidence')
            if not matches:flags.append('word_row_text_difference')
            if not selected:flags.append('word_metadata_absent')
            if flags:uncertainty.append(dict(row=index,flags=flags))
            rows.append(dict(start=(request.offset_samples+a)*3,end=(request.offset_samples+b)*3,text=text,estimates=estimates))
        if covered!=len(words):raise LocalAiError('invalid_output','Unaccounted native word metadata.')
        from mediaforge.features.dub.alignment import VAD_SHA256
        activity=row.get('activity_support',{});items=activity.get('observations')
        if activity.get('asset_sha256')!=VAD_SHA256 or not isinstance(items,list):
            raise LocalAiError('invalid_output','Tutorial activity asset identity differs.')
        observations=[]
        if count<1536:
            if items or activity.get('status')!='insufficient_frames':raise LocalAiError('invalid_output','Short VAD remainder was fabricated.')
        else:
            if len(items)!=2:raise LocalAiError('invalid_output','Both tutorial activity observations required.')
            for grid,item in zip((0,256),items):
                if not isinstance(item,dict):raise LocalAiError('invalid_output','Malformed tutorial activity row.')
                scores=item.get('scores')
                if item.get('grid')!=grid or not isinstance(scores,list) or len(scores)!=(count-grid)//512 or any(type(p) not in (int,float) or not math.isfinite(p) or not 0<=p<=1 for p in scores):
                    raise LocalAiError('invalid_output','Tutorial VAD grid clock differs.')
                observations.append(dict(offset=request.offset_samples+grid,scores=scores))
        return dict(schema='tutorial-asr-observation-v2',window=[request.offset_samples,request.offset_samples+count],
            model_revision=spec.revision,rows=rows,native_segments=native,confidence=confidence,words=words,
            activity=observations,uncertainty=uncertainty,recipe='base-cpu-int8-en-beam5-vad-wordtimestamps-no-previous-text')

    def binding(self):
        result=super().binding()
        return dict(result,portable_core=getattr(getattr(sys,'mediaforge_core_guard',None),'expected',None))

    def recognize_words(self,request,control):
        result,words,confidence,_=self._recognize_words(request,control,False)
        return result,words,confidence

    def recognize_words_with_activity(self,request,control):
        return self._recognize_words(request,control,True)

    def _recognize_words(self,request,control,activity):
        row,spec=self._selected('whisper-base','recognize',
            dict(pcm16=base64.b64encode(request.pcm16).decode(),word_support=True,activity_support=activity),control)
        count=len(request.pcm16)//2
        if (row.get('samples')!=count or row.get('word_evidence')!='accepted-whisper-base-word-estimates-v2'
                or row.get('word_time_basis')!='attention-estimates-not-acoustic-boundaries'):
            raise LocalAiError('invalid_output','Actual Whisper word evidence is unavailable.')
        words=[];previous=0
        for word in row.get('words',()):
            a,b,p=word['start'],word['end'],word['probability']
            if (type(a) is not int or type(b) is not int or not 0<=a<=b<=count or a<previous
                    or type(p) not in (int,float) or not math.isfinite(p) or not 0<=p<=1
                    or not isinstance(word['text'],str) or not word['text'].strip()):
                raise LocalAiError('invalid_output','Word evidence clock/confidence is invalid.')
            previous=a
            words.append(dict(start=a+request.offset_samples,end=b+request.offset_samples,
                              text=word['text'],probability=p))
        if not 0<len(words)<=512:raise LocalAiError('speech_boundary_uncertain','No bounded complete word evidence.')
        result=RecognitionResult(tuple(SpeechSegment(s['start']+request.offset_samples,s['end']+request.offset_samples,s['text'])
                                       for s in row['segments']),count,spec.revision)
        observations=None
        if activity:
            from mediaforge.features.dub.alignment import VAD_SHA256
            value=row.get('activity_support',{});items=value.get('observations')
            if value.get('asset_sha256')!=VAD_SHA256 or not isinstance(items,list) or len(items)!=2:
                raise LocalAiError('invalid_output','Combined recognition/activity identity differs.')
            observations=[]
            for grid,item in zip((0,256),items):
                scores=item.get('scores',())
                if (item.get('grid')!=grid or len(scores)!=(count-grid)//512
                        or any(type(p) not in (int,float) or not math.isfinite(p) or not 0<=p<=1 for p in scores)):
                    raise LocalAiError('invalid_output','Combined recognition/activity clock or confidence differs.')
                observations.append(dict(offset=request.offset_samples+grid,scores=scores))
        return result,words,tuple(row.get('word_segments',())),observations
