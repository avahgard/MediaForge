from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_settings_page_has_no_startup_acquisition_and_displays_explicit_consent(isolated_environment, tmp_path):
    # Use the real registry and application. The install plan is dismissed
    # before dispatch so this qualification does not acquire production models.
    environment = dict(isolated_environment)
    environment.pop('MEDIAFORGE_FFPROBE_PATH', None)
    result = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'tests/native_settings.py'), str(tmp_path / 'evidence')],
        cwd=ROOT, env=environment, timeout=35, capture_output=True, text=True, encoding='utf-8', errors='replace',
    )
    assert result.returncode == 0, result.stdout + result.stderr
    restarted = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'tests/native_settings.py'), str(tmp_path / 'restart'),
         '--restart', 'Light'],
        cwd=ROOT, env=environment, timeout=20, capture_output=True, text=True, encoding='utf-8', errors='replace',
    )
    assert restarted.returncode == 0, restarted.stdout + restarted.stderr
