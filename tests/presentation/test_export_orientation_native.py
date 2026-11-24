from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.native
@pytest.mark.integration
def test_display_matrix_90_and_270_preserve_native_upstream_viewer(tmp_path):
    script=Path(__file__).resolve().parents[1]/'export_orientation_native.py'
    result=subprocess.run([sys.executable,str(script),'--output',str(tmp_path)],capture_output=True,
                          text=True,encoding='utf-8',errors='replace',timeout=60)
    if result.returncode==77 and 'NATIVE SCREEN SKIP:' in result.stderr:
        pytest.skip(result.stderr)
    assert result.returncode==0,result.stdout+result.stderr
