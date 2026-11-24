"""Collection contracts for mixed cases; never execute the selected native cases."""
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = "tests/presentation/test_workspace_native.py"
MATRIX = WORKSPACE + "::test_product_check_2_complete_native_matrix"


def collect(tmp_path, expression, *paths):
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--strict-markers",
         "-o", f"cache_dir={tmp_path / 'cache'}", "-m", expression, *paths],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return [line for line in result.stdout.splitlines() if line.startswith("tests/") and "::" in line]


@pytest.mark.integration
def test_performance_selects_only_the_measured_frame_gate(tmp_path):
    nodes = collect(tmp_path, "performance", WORKSPACE)
    assert nodes == [MATRIX + "[pc2_frame_native.py]"]


@pytest.mark.integration
def test_native_integration_without_heavy_preserves_small_matrix_cases(tmp_path):
    nodes = collect(tmp_path, "native and integration and not heavy", WORKSPACE)
    assert set(nodes) == {
        WORKSPACE + f"::test_settled_adjacent_decoded_frames[{mode}]"
        for mode in ("integral", "thirty", "fractional", "unreliable")
    } | {
        WORKSPACE + "::test_native_icon_canvas_and_optical_centering",
        WORKSPACE + "::test_native_window_activation_before_adjacent_shortcut",
        WORKSPACE + "::test_late_onset_timing_exact_indexed_global_ordinal_and_decoded_adjacency",
        MATRIX + "[pc2_dispatch_native.py]",
    }
    assert len(nodes) == len(set(nodes)) == 8


@pytest.mark.integration
def test_native_selector_excludes_fake_offscreen_and_geometry_scenarios(tmp_path):
    nodes = collect(tmp_path, "native",
        "tests/presentation/test_playback_origin_shutdown.py",
        "tests/presentation/test_trim_admission.py",
        "tests/presentation/test_native_frame_settlement.py",
        "tests/unit/test_native_window_policy.py",
        "tests/unit/test_native_sources.py",
        "tests/unit/test_native_capture_geometry.py",
        "tests/unit/test_native_phase_diagnostics.py",
    )
    assert nodes == [
        "tests/unit/test_native_phase_diagnostics.py::"
        "test_native_watchdog_retains_phase_and_stacks_when_native_call_holds_gil"
    ]
