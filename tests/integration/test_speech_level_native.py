"""Actual fixed-filter level/latency qualification; no AI or final publication."""
import array
from fractions import Fraction
import math
import os
from pathlib import Path

import pytest

from mediaforge.features.dub.contracts import DubControl
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest,FfmpegRuntimeResolver
from mediaforge.infrastructure.ffmpeg.speech import SpeechAudio,PcmWriter
from mediaforge.application.services.speech import JobControl

pytestmark=[pytest.mark.native,pytest.mark.integration]

def test_fixed_limiter_count_level_peak_silence_and_gain_ceiling(tmp_path):
    root=os.environ.get('MEDIAFORGE_TEST_RUNTIME_ROOT')
    if not root:pytest.skip('Explicit authenticated FFmpeg root required.')
    runtime=FfmpegRuntimeResolver(FfmpegManifest.load()).verify_directory(Path(root))
    paths=ProfilePaths.for_test(tmp_path/'portable/Data/v2');paths.prepare()
    audio=SpeechAudio(paths,runtime);c=DubControl();n=48000*8
    source=paths.temporary/'impulses-and-tone.pcm'
    # Complete deterministic source with leading/intermediate/trailing silence,
    # steady content and short peaks that require actual limiting after gain.
    raw=array.array('h',(int(5000*math.sin(i*2*math.pi*440/48000)) if 48000<=i<3*48000 or 4*48000<=i<6*48000 else 0 for i in range(n)))
    for i in range(48000,3*48000,2400):raw[i]=30000
    with PcmWriter(source,c,n) as writer:writer.write(raw.tobytes())
    target=paths.temporary/'leveled.pcm'
    (integrated,peak),warnings=audio.level(source,target,c,n)
    assert target.stat().st_size==n*2
    assert -17<=integrated<=-15 and peak<=-1.3 and not warnings
    data=array.array('h',target.read_bytes())
    assert max(abs(x) for x in data)<32767
    # Do not require resampler impulse tails to be zero inside its guard region;
    # require the interior of all known silence intervals to remain exact zero.
    assert not any(data[:47000]) and not any(data[3*48000+1000:4*48000-1000]) and not any(data[6*48000+1000:])
    assert abs(next(i for i,v in enumerate(data) if v)-48000)<100  # bounded resampler ringing, no 5ms lookahead shift
    quiet=paths.temporary/'quiet.pcm'
    q=array.array('h',(int(70*math.sin(i*2*math.pi*440/48000)) for i in range(2*48000)))
    with PcmWriter(quiet,c,len(q)) as writer:writer.write(q.tobytes())
    quiet_out=paths.temporary/'quiet-level.pcm'
    before=audio.measure(quiet,c)
    (level,peak),warnings=audio.level(quiet,quiet_out,c,len(q))
    assert level-before[0]<=12.2 and 'low_level_speech' in warnings
    assert quiet_out.stat().st_size==2*len(q) and peak<=-1.3

def test_cancel_owned_ffmpeg_pipe_reaps_readers_job_and_partial_pcm(tmp_path):
    root=os.environ.get('MEDIAFORGE_TEST_RUNTIME_ROOT')
    if not root:pytest.skip('Explicit authenticated FFmpeg root required.')
    runtime=FfmpegRuntimeResolver(FfmpegManifest.load()).verify_directory(Path(root))
    paths=ProfilePaths.for_test(tmp_path/'portable/Data/v2');paths.prepare()
    audio=SpeechAudio(paths,runtime);original=DubControl();control=JobControl(original)
    target=paths.temporary/'cancelled.pcm';seen=[]
    with pytest.raises(Exception) as error:
        with PcmWriter(target,control,8*48000) as writer:
            def consume(block):
                writer.write(block);seen.append(len(block));original.cancel()
            audio.pipe(['-v','error','-re','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=8',
                '-ac','1','-f','s16le','pipe:1'],control,consume=consume,maximum=8*48000*2)
    assert seen and getattr(error.value,'code',None)=='cancelled' and not control.cleanup_failed
    assert not target.exists() and not list(paths.temporary.glob('.pcm-*'))
    assert audio.receipts[-1]['retired'] and len(audio.receipts[-1]['limits'])==1
