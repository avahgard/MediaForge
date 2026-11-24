import subprocess,sys
from pathlib import Path
import pytest
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime


@pytest.mark.native
@pytest.mark.integration
def test_native_same_frame_source_output_and_fixed_footer(tmp_path):
    runtime=resolve_ffmpeg_runtime();source=tmp_path/'original 1024.mp4'
    subprocess.run([str(runtime.ffmpeg),'-v','error','-n','-f','lavfi','-i',
        'color=yellow:s=1024x768:r=25:d=0.6,drawbox=x=512:y=0:w=512:h=768:color=red:t=fill',
        '-c:v','libx264','-pix_fmt','yuv420p',str(source)],capture_output=True,check=True,timeout=20)
    result=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'ui_polish_native.py'),
        '--output',str(tmp_path/'paint'),'--source',str(source)],capture_output=True,text=True,
        encoding='utf-8',errors='replace',timeout=60)
    (tmp_path/'native-result.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode==77 and 'NATIVE SCREEN SKIP:' in result.stderr:pytest.skip(result.stderr)
    assert result.returncode==0,result.stdout+result.stderr
