import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "win32", reason="Windows native window placement")
@pytest.mark.parametrize("no_fit", [False, True], ids=["placement", "no-fit-cleanup"])
def test_secondary_monitor_native_window_policy(tmp_path, no_fit):
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "windows"
    env["LOCALAPPDATA"] = str(tmp_path / "profile Кириллица")
    env["TEMP"] = str(tmp_path / "temp with spaces")
    env["QML_DISABLE_DISK_CACHE"] = "1"
    result = subprocess.run([sys.executable, "-X", "utf8",
                             str(ROOT / "tests/native_window_policy_probe.py"),
                             "--output", str(tmp_path / "evidence"), *(["--no-fit"] if no_fit else [])],
                            cwd=ROOT, env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
    diagnostics = result.stdout + result.stderr
    (tmp_path / "probe.log").write_text(diagnostics, encoding="utf-8")
    if no_fit:
        assert result.returncode == 77 and "NATIVE NO-FIT CLEANUP VERIFIED" in diagnostics, diagnostics
        return
    if result.returncode == 77 and "NATIVE SCREEN SKIP:" in diagnostics:
        pytest.skip(diagnostics)
    assert result.returncode == 0, diagnostics
