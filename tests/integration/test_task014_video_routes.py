"""Real small mux/encode/decode/publication controls; no AI inference."""
from pathlib import Path
from fractions import Fraction
from dataclasses import asdict
import hashlib,json,os,subprocess
import numpy as np
import pytest
from mediaforge.features.dub.contracts import DubControl,OutputMode
from mediaforge.features.dub.planning import output_plan
from mediaforge.application.contracts.speech import SpeechComposition
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegRuntimeResolver,FfmpegManifest
from mediaforge.infrastructure.ffmpeg.dub_probe import FfprobeDubInspector
from mediaforge.infrastructure.ffmpeg.dub_export import DubExportExecutor

pytestmark=[pytest.mark.native,pytest.mark.integration]


@pytest.mark.parametrize('profile',['high','main','baseline','high-vfr','vp9'])
@pytest.mark.parametrize('mode',[OutputMode.SAME_AS_SOURCE,OutputMode.COMPATIBLE_MP4])
def test_mp4_profile_picture_clock_audio_and_source_preservation(tmp_path,profile,mode):
    runtime=FfmpegRuntimeResolver(FfmpegManifest.load()).verify_directory(Path(os.environ['MEDIAFORGE_TEST_RUNTIME_ROOT']))
    paths=ProfilePaths.for_test(tmp_path/'portable/Data/v2');paths.prepare()
    path=tmp_path/'ordinary source.mp4'
    encoding=['-c:v','libvpx-vp9'] if profile=='vp9' else ['-c:v','libx264','-profile:v',profile.split('-')[0]]
    if profile=='high-vfr':encoding+=['-vf',"select='not(mod(n,3))'",'-fps_mode','vfr']
    command=[str(runtime.ffmpeg),'-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=96x64:rate=25:duration=2',
        '-f','lavfi','-i','sine=frequency=400:sample_rate=48000:duration=2',*encoding,
        '-pix_fmt','yuv420p','-threads','1','-c:a','aac',str(path)]
    result=subprocess.run(command,capture_output=True,timeout=20,cwd=paths.temporary,env=paths.child_environment())
    assert result.returncode==0,result.stderr
    original=hashlib.sha256(path.read_bytes()).hexdigest()
    source=FfprobeDubInspector(runtime,paths,qualified_mux_metadata=True).inspect(path,DubControl())
    if profile=='vp9' and mode==OutputMode.SAME_AS_SOURCE:
        from mediaforge.features.dub.contracts import DubError
        with pytest.raises(DubError,match='not qualified'):output_plan(source,mode)
        assert hashlib.sha256(path.read_bytes()).hexdigest()==original
        return
    plan=output_plan(source,mode)
    assert source.video.clock.records==(17 if profile=='high-vfr' else 50)
    assert plan.duration==source.video.clock.end-source.video.clock.first
    audio=tmp_path/'complete internal.pcm'
    pcm=np.rint(np.sin(np.arange(plan.sample_count)*2*np.pi*523/48000)*3000).astype('<i2').tobytes()
    audio.write_bytes(pcm)
    composition=SpeechComposition(audio,plan.sample_count,hashlib.sha256(pcm).hexdigest(),-1.7,-18.,(),'fixture')
    target=tmp_path/'dubbed.mp4';executor=DubExportExecutor(paths,runtime)
    if profile=='high-vfr' and mode==OutputMode.COMPATIBLE_MP4:
        # This source's B-frame packet durations contain a short penultimate
        # display interval. libx264 regenerates that interval. Keep the exact
        # oracle and refuse rather than publishing a subtly retimed result.
        from mediaforge.features.media.export import ExportError
        with pytest.raises(ExportError,match='timestamps/durations changed during compatible encoding'):
            executor.execute(source,plan,composition,target,DubControl())
        assert not target.exists()
        assert hashlib.sha256(path.read_bytes()).hexdigest()==original
        (tmp_path/'refusal-receipt.json').write_text(json.dumps(dict(
            source_sha256=original,refusal='encoded VFR interval mismatch',published=False)))
        return
    output=executor.execute(source,plan,composition,target,DubControl())
    assert output.output==target and executor.last_receipt['status']=='validated_published'
    assert executor.last_receipt['video_stream_copy']==(mode==OutputMode.SAME_AS_SOURCE)
    assert executor.last_receipt['decoded']['samples']>=plan.sample_count
    assert hashlib.sha256(path.read_bytes()).hexdigest()==original
    (tmp_path/'route-receipt.json').write_text(json.dumps(dict(source_sha256=original,
        route=asdict(plan.route),export=executor.last_receipt),indent=2,default=str))
