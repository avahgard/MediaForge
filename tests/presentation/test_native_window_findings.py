import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
HARNESSES = ("pc2_dispatch_native", "native_workspace_regressions", "pc2_frame_native",
             "pc2_index_native", "pc2_workspace_native", "pc2_origin_shutdown_native",
             "pc3_closure_native", "trim_admission_native", "check_workspace_runtime")
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native C1 findings")


def run_probe(mode, harness, tmp_path):
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "windows"
    env["QML_DISABLE_DISK_CACHE"] = "1"
    env["TEMP"] = str(tmp_path / "temp Кириллица")
    env.pop("MEDIAFORGE_FFPROBE_PATH", None)
    command = [sys.executable, "-X", "utf8", str(ROOT / "tests/native_window_findings_probe.py"),
               mode, "--harness", harness, "--output", str(tmp_path)]
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
    diagnostics = result.stdout + result.stderr
    (tmp_path / "probe.log").write_text(diagnostics, encoding="utf-8")
    if result.returncode == 77 and "NATIVE SCREEN SKIP:" in diagnostics:
        pytest.skip(diagnostics)
    assert result.returncode == 0, diagnostics


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize("harness", HARNESSES)
@pytest.mark.parametrize("mode", ("resize-skip", "resize-error"))
def test_c1_f01_requested_placement_unwinds_native_resources(tmp_path, harness, mode):
    run_probe(mode, harness, tmp_path)


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize("mode", ("companion", "transient"))
def test_c1_f02_follower_tracks_owner_through_reassessment_and_reshow(tmp_path, mode):
    run_probe(mode, "pc2_dispatch_native", tmp_path)
