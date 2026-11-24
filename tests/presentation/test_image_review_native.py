"""Native QML typing and menu gate with finite out-of-process containment."""
import os,sys,subprocess
from pathlib import Path
import pytest
from PySide6.QtGui import QImage,QColor


@pytest.mark.native
@pytest.mark.integration
def test_image_review_keyboard_and_menu_with_external_watchdog(tmp_path):
    repo=Path(__file__).resolve().parents[2];sources=tmp_path/'sources';sources.mkdir()
    for i in range(3):
        image=QImage(128,96,QImage.Format_RGBA8888);image.fill(QColor(200,40+i,80,128));assert image.save(str(sources/f'frame{i:04d}.png'))
    env=os.environ.copy();env.pop('QT_QPA_PLATFORM',None);env['QML_DISABLE_DISK_CACHE']='1';env['PYTHONDONTWRITEBYTECODE']='1'
    result=subprocess.run([sys.executable,'-B',str(repo/'tests/native_image_review_watchdog.py'),str(repo/'tests/native_image_review_corrections.py'),str(tmp_path/'native'),str(sources/'frame0000.png')],
        env=env,cwd=repo,capture_output=True,text=True,timeout=80)
    (tmp_path/'watchdog.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    assert result.returncode==0,(tmp_path/'native/child.log').read_text(encoding='utf-8')
