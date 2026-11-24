from pathlib import Path
import subprocess,sys
import pytest
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime


@pytest.mark.native
@pytest.mark.integration
def test_native_inspector_gutter_and_wheel_without_processing(tmp_path):
    repo=Path(__file__).resolve().parents[2]
    fixture=tmp_path/'synthetic 1024 Кириллица.mp4'
    runtime=resolve_ffmpeg_runtime()
    subprocess.run([str(runtime.ffmpeg),'-v','error','-n','-f','lavfi','-i',
        'color=yellow:s=1024x768:r=25:d=0.6,drawbox=x=512:y=0:w=512:h=768:color=red:t=fill',
        '-c:v','libx264','-pix_fmt','yuv420p',str(fixture)],capture_output=True,check=True,timeout=30)
    result=subprocess.run([sys.executable,str(repo/'tests/task010_final_ui_native.py'),'--output',str(tmp_path/'paint'),
            '--source',str(fixture)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
    if result.returncode==77 and 'NATIVE SCREEN SKIP:' in result.stderr: pytest.skip(result.stderr)
    assert result.returncode==0,result.stdout+result.stderr
