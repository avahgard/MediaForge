"""Tiny native counterexample; no model load, source selection or hour claim."""
import array,json,os
from fractions import Fraction as F
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.features.dub.contracts import DubControl
from mediaforge.infrastructure.ffmpeg.qwen_speech import QwenSpeechAudio
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest,FfmpegRuntimeResolver
from mediaforge.infrastructure.filesystem.profile import ProfilePaths

pytestmark=[pytest.mark.native,pytest.mark.integration]


def test_native_resampler_boundary_correspondence_and_tamper_refusal(tmp_path):
    root=os.environ.get('MEDIAFORGE_TEST_RUNTIME_ROOT')
    if not root:pytest.skip('Explicit authenticated managed FFmpeg required.')
    paths=ProfilePaths.for_test(tmp_path/'Source correspondence Юникод/Data/v2');paths.prepare()
    runtime=FfmpegRuntimeResolver(FfmpegManifest.load()).verify_directory(Path(root))
    audio=QwenSpeechAudio(paths,runtime);control=DubControl()
    # Non-grid-aligned step after exactly 448 source zeros, matching the observed
    # counterexample topology. Native support is never shortened to hide ringing.
    values=array.array('f',[0.]*448+[.25]*4800+[0.]*(48000-5248))
    raw=paths.temporary/'source.f32';raw.write_bytes(values.tobytes())
    source=paths.temporary/'source.wav'
    audio.pipe(['-v','error','-f','f32le','-ar','48000','-ac','1','-i',str(raw),
        '-c:a','pcm_f32le',str(source)],control,timeout=10)
    plan=NS(duration=F(1),origin=F(0),audio_index=0)
    track=NS(codec='pcm_f32le',sample_rate=48000,channels=1)
    recognition=paths.temporary/'recognition.pcm';audio.extract(source,plan,recognition,control)
    before=recognition.read_bytes();pcm=array.array('h',before)
    assert any(pcm[:149]) # old point-mapped exact-zero predicate demonstrably fails
    ledger,units=audio.source_support(source,plan,track,recognition,control)
    assert ledger['zeros']==[(0,448),(5248,48000)]
    assert units[0].native_start==448 and units[0].native_end==5248
    assert recognition.read_bytes()==before
    # Same length, one changed sample both inside silence and inside speech.
    for index in (20,300,15990):
        changed=bytearray(before);changed[index*2]^=1;recognition.write_bytes(changed)
        with pytest.raises(LocalAiError,match='bytes differ'):
            audio.verify_extraction(source,plan,recognition,control)
    recognition.write_bytes(before);audio.verify_extraction(source,plan,recognition,control)
    assert all(row['retired'] for row in audio.receipts)
    (tmp_path/'receipt.json').write_text(json.dumps(dict(native_zeros=ledger['zeros'],
        first_resampled_nonzero=next(i for i,v in enumerate(pcm) if v),
        samples=len(pcm),tamper_offsets=[20,300,15990],children_retired=True)),encoding='utf-8')
