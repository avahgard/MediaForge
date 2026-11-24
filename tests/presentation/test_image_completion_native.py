"""Production GUI completion proof using compact disposable sources."""
import os,sys,subprocess
from pathlib import Path
import pytest


@pytest.mark.native
@pytest.mark.integration
def test_native_image_product_completion(tmp_path):
    from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
    runtime=resolve_ffmpeg_runtime();video=tmp_path/'video.mp4'
    subprocess.run([str(runtime.ffmpeg),'-v','error','-n','-f','lavfi','-i','color=yellow:s=320x180:r=25:d=0.4','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True,capture_output=True,timeout=15)
    env=os.environ.copy();env.pop('QT_QPA_PLATFORM',None);env['QML_DISABLE_DISK_CACHE']='1'
    result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve().parents[1]/'native_image_completion.py'),str(tmp_path/'native'),str(video)],capture_output=True,text=True,encoding='utf-8',errors='replace',env=env,timeout=120)
    (tmp_path/'native.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode==77:pytest.skip(result.stderr)
    assert result.returncode==0,result.stdout+result.stderr
