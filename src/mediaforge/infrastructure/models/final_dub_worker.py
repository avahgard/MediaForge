"""Final Dub: expose word evidence ALREADY computed by the accepted C2 run.

No model/inference/sampling change. The unchanged C3 entry still supplies native
Windows facts and the unchanged C2 entry owns resolver, offline audit and I/O.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import sys

C3_SHA256='3d38176d8942118da1780eeb20ac55de93c60852d50c27f0ad0f8e4cf98ce7ea'


def word_evidence(word,index):
    """Keep refusal exact and diagnostic; never put recognized text in errors."""
    values=(word.start,word.end,word.probability)
    try:valid_numbers=all(math.isfinite(v) for v in values)
    except (TypeError,ValueError):valid_numbers=False
    valid_text=isinstance(word.word,str) and bool(word.word.strip()) and len(word.word)<=500
    if (not valid_numbers or not 0<=word.start<=word.end
            or not 0<=word.probability<=1 or not valid_text):
        facts=dict(index=index,start=repr(word.start),end=repr(word.end),
                   probability=repr(word.probability),text_type=type(word.word).__name__,
                   text_length=len(word.word) if isinstance(word.word,str) else None,
                   valid_numbers=valid_numbers,valid_text=valid_text)
        raise ValueError('invalid_word_evidence '+json.dumps(facts,separators=(',',':')))
    return dict(start=round(word.start*16000),end=round(word.end*16000),
                text=word.word,probability=float(word.probability))


def main():
    core=os.environ.get('MEDIAFORGE_CORE_ROOT');digest=os.environ.get('MEDIAFORGE_CORE_MANIFEST_SHA256')
    if bool(core)!=bool(digest):raise ValueError('incomplete_core_capability')
    if core:
        helper=Path(__file__).with_name('core_loader.py');raw=helper.read_bytes()
        if len(raw)>16384 or hashlib.sha256(raw).hexdigest()!='62d683b6ae8ce946b2866a7317f43b759912661e4c1c7540ae31b4448a31f36a':
            raise ValueError('core_loader_integrity')
        namespace=dict(__name__='_mediaforge_private_stdlib',__file__=str(helper))
        exec(compile(raw,str(helper),'exec',dont_inherit=True),namespace)
        namespace['install'](core,digest)
    path=Path(__file__).with_name('dub_worker.py')
    with path.open('rb') as stream:raw=stream.read(65537)
    if len(raw)>65536 or hashlib.sha256(raw).hexdigest()!=C3_SHA256:
        raise ValueError('runtime_integrity')
    c3=dict(__name__='_mediaforge_c3_final_delegate',__file__=str(path))
    exec(compile(raw,str(path),'exec',dont_inherit=True),c3)
    verified=c3['verified_delegate']
    def delegate():
        namespace=verified();original=namespace['run']
        def run(request):
            if request.get('word_support') is not True:
                return original(request)
            if request.get('action')!='recognize':raise ValueError('unsupported_word_support')
            # This import is reached INSIDE the original worker's resolver and
            # audit setup, never before native/source authentication.
            import faster_whisper
            actual=faster_whisper.WhisperModel;words=[];segments=[]
            class WordEvidenceAdapter:
                def __init__(self,*args,**kwargs):self.engine=actual(*args,**kwargs)
                def transcribe(self,*args,**kwargs):
                    if kwargs.get('word_timestamps') is not True:raise ValueError('word_evidence_unavailable')
                    stream,info=self.engine.transcribe(*args,**kwargs)
                    def observed():
                        for segment in stream:
                            values=(segment.avg_logprob,segment.no_speech_prob)
                            if not all(math.isfinite(v) for v in values):raise ValueError('invalid_word_evidence')
                            evidence=dict(avg_logprob=values[0],no_speech_prob=values[1])
                            if request.get('tutorial_v2') is True:evidence['word_first']=len(words)
                            segments.append(evidence)
                            if len(segments)>128:raise ValueError('word_evidence_limit')
                            for word in segment.words or ():
                                if len(words)>=512:raise ValueError('word_evidence_limit')
                                words.append(word_evidence(word,len(words)))
                            if request.get('tutorial_v2') is True:evidence['word_last']=len(words)
                            yield segment
                    return observed(),info
            faster_whisper.WhisperModel=WordEvidenceAdapter
            try:
                result=original(request)
                result.update(words=words,word_segments=segments,
                    word_evidence='accepted-whisper-base-word-estimates-v2',
                    word_time_basis='attention-estimates-not-acoustic-boundaries')
                if request.get('activity_support') is True:
                    # The recognition call has returned and its engine has no
                    # retained session. Run the unchanged authenticated C3 VAD
                    # under the SAME resolver, audit hooks and owning CpuJob.
                    if request.get('tutorial_v2') is True and result['samples']<1536:
                        result['activity_support']=dict(asset_sha256=c3['VAD_SHA256'],observations=[],status='insufficient_frames')
                    else:result['activity_support']=c3['activity'](request)
                return result
            finally:faster_whisper.WhisperModel=actual
        namespace['run']=run
        return namespace
    c3['verified_delegate']=delegate
    return c3['main']()


if __name__=='__main__':
    try:code=main()
    except Exception as error:
        sys.stdout.write(json.dumps(dict(ok=False,detail=type(error).__name__+': '+str(error)[:512])));code=2
    raise SystemExit(code)
