"""Retain one bounded public-fixture ASR/VAD/waveform observation, no TTS."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from uuid import uuid4
import wave

from mediaforge.application.contracts.local_ai import RecognitionRequest
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.application.services.speech_islands import island_window
from mediaforge.bootstrap.dub_models import compose_dub_models
from mediaforge.bootstrap.speech import compose_speech
from mediaforge.features.dub.contracts import DubControl,OutputMode
from mediaforge.features.dub.planning import output_plan
from mediaforge.features.dub.practical_timing import admit_hard_island
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
from mediaforge.infrastructure.ffmpeg.dub_probe import FfprobeDubInspector
from mediaforge.infrastructure.models.qwen_tts import QwenTts
from fractions import Fraction


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--island',type=int,required=True)
    args=p.parse_args();paths=ProfilePaths.for_test(args.root);paths.activate_temporary()
    if args.output.exists() or not args.output.is_relative_to(paths.root/'fixtures'):p.error('New private fixture receipt required.')
    gate=MediaProcessingGate();control=DubControl();components=compose_dub_models(paths,gate)
    runtime=resolve_ffmpeg_runtime(profile=paths);tts=QwenTts(paths,components.requirements.qwen)
    speech=compose_speech(components.local_ai,runtime,tts=tts)
    source=FfprobeDubInspector(runtime,paths).inspect(args.source,control)
    plan=output_plan(source,OutputMode.VIDEO_AUDIO_ONLY if source.video else OutputMode.AUDIO)
    if plan.duration>90:raise ValueError('This diagnostic is bounded to the declared short/minute fixture.')
    extraction=paths.root/'fixtures'/('timing-extract-'+uuid4().hex+'.pcm')
    count=speech.audio.extract(args.source,plan,extraction,control)
    track=next(t for t in source.audio_tracks if t.index==plan.audio_index)
    ledger,support=speech.audio.source_support(args.source,plan,track,extraction,control)
    current=support[args.island];begin,end=island_window(support,args.island,count)
    with extraction.open('rb') as stream:stream.seek(begin*2);raw=stream.read((end-begin)*2)
    with speech.providers.session(control),speech.providers.selected_models(('whisper-base',),control):
        recognized,words,confidence=speech.providers.recognize_words(RecognitionRequest(raw,begin),control)
        activity=speech.providers.activity(raw,begin,control)
    result=dict(source_sha256=source.identity.sha256,source=[str(current.start),str(current.end)],
        window=[begin,end],words=words,confidence=confidence,activity=activity,ledger=ledger,
        text=' '.join(s.text for s in recognized.segments),rate=16000)
    zeros=[(Fraction(a*16000,ledger['rate']),Fraction(b*16000,ledger['rate'])) for a,b in ledger['zeros']]
    try:
        parts,pauses=admit_hard_island(current,activity,zeros,(begin,end),words,result['text'],raw)
        result.update(admitted=True,parts=[dict(text=v.text,start=str(v.start),end=str(v.end)) for v in parts])
    except Exception as error:result.update(admitted=False,code=str(getattr(error,'code','timing_failed')),detail=str(error))
    wav_path=args.output.with_suffix('.wav')
    with wav_path.open('xb') as stream,wave.open(stream,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(raw)
    result.update(pcm=str(wav_path),retired=not gate.busy and not gate.failure)
    with args.output.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:result.get(k) for k in ('admitted','source','window','detail','retired')},ensure_ascii=False))


if __name__=='__main__':main()
