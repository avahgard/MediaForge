import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "win32", reason="Windows Qt multimedia")
def test_reformat_actual_live_scene_processing_and_input(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "windows"
    result = subprocess.run([sys.executable, "-X", "utf8", str(root / "tests/reformat_native.py"),
                             "--output", str(tmp_path)], cwd=root, env=env,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
    (tmp_path / "stdout.txt").write_text(result.stdout, encoding="utf-8")
    (tmp_path / "stderr.txt").write_text(result.stderr, encoding="utf-8")
    if result.returncode == 77 and "NATIVE SCREEN SKIP:" in result.stderr:
        pytest.skip(result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
