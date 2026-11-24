"""Real QML FC-01 changed-PNG/corrupt-mdat regressions; strict original oracles."""
import os,sys,subprocess
from pathlib import Path
import pytest


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('case',['changed-png','corrupt-video','existing-video-owner'])
def test_fc01_original_counterexample(tmp_path,case):
    repo=Path(__file__).resolve().parents[2];env=os.environ.copy();env.pop('QT_QPA_PLATFORM',None)
    env.update(QML_DISABLE_DISK_CACHE='1',PYTHONDONTWRITEBYTECODE='1')
    completed=subprocess.run([sys.executable,'-B',str(repo/'tests/native_image_review_watchdog.py'),str(repo/'tests/native_fc01_results.py'),str(tmp_path/'native'),case],
        cwd=repo,env=env,capture_output=True,text=True,timeout=80)
    (tmp_path/'watchdog.log').write_text(completed.stdout+completed.stderr,encoding='utf-8')
    assert completed.returncode==0,(tmp_path/'native/child.log').read_text(encoding='utf-8')
