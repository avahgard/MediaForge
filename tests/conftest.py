import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def isolated_environment(tmp_path):
    env = os.environ.copy()
    env['MEDIAFORGE_DATA_ROOT'] = str(tmp_path / 'Portable data Кириллица')
    env["LOCALAPPDATA"] = str(tmp_path / "User profile Кириллица")
    env["TEMP"] = str(tmp_path / "Temporary files Временные")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.pop("QML_DISABLE_DISK_CACHE", None)
    return env


@pytest.fixture
def run_python(isolated_environment, tmp_path):
    def run(code):
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=tmp_path,
            env=isolated_environment, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        return result
    return run
