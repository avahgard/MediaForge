"""Real Windows GUI sequence gate, independent sparse painted-pixel oracle."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.native
@pytest.mark.integration
def test_native_sequence_workspace(tmp_path):
    from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
    runtime=resolve_ffmpeg_runtime()
    video=tmp_path/'video.mp4'
    subprocess.run([str(runtime.ffmpeg),'-v','error','-n','-f','lavfi','-i',
                    'color=yellow:s=320x180:r=25:d=0.4','-c:v','libx264','-pix_fmt','yuv420p',str(video)],
                   check=True,capture_output=True,timeout=20)
    root=Path(__file__).resolve().parents[2]
    env=os.environ.copy();env.pop('QT_QPA_PLATFORM',None)
    args=[sys.executable,'-B',str(root/'tests/native_sequence_paint.py'),str(tmp_path/'native'),str(video)]
    if os.environ.get('MEDIAFORGE_SEQUENCE_FIXTURE'): args.append(os.environ['MEDIAFORGE_SEQUENCE_FIXTURE'])
    result=subprocess.run(args,
                          cwd=root,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=110)
    (tmp_path/'native.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode==77:pytest.skip(result.stderr)
    assert result.returncode==0,result.stdout+result.stderr
