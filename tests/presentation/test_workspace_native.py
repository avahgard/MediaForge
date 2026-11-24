"""Native Windows regressions: real decoder, persistent QML VideoOutput and layout.

Fixtures use only the already authenticated project-managed runtime. No download
or install occurs. These are subprocesses so Qt's native GUI is not substituted
by the offscreen/fake-player unit-test environment.
"""

import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native Qt backend")


def require_native_success(result, diagnostics):
    if result.returncode == 77 and "NATIVE SCREEN SKIP:" in diagnostics:
        pytest.skip(diagnostics)
    assert result.returncode == 0, diagnostics


@pytest.fixture(scope="module")
def native_fixtures(tmp_path_factory):
    from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime

    runtime = resolve_ffmpeg_runtime()  # Authenticated existing executables, no provisioning.
    directory = tmp_path_factory.mktemp("native workspace Кириллица")
    from tests.native_sources import NativeSources

    from tests.synthetic_source_cache import cache_from_environment

    sources = NativeSources(directory, runtime, cache_directory=cache_from_environment())
    try:
        yield sources
    finally:
        sources.write_generation()


def run_native(mode, sources, tmp_path):
    directory = sources.for_mode(mode)
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "windows"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "tests/native_workspace_regressions.py"),
         "--mode", mode, "--fixtures", str(directory), "--output", str(tmp_path)],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=400,
    )
    require_native_success(result, result.stdout + result.stderr)


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize("mode", ["integral", "thirty", "fractional", "unreliable"])
def test_settled_adjacent_decoded_frames(native_fixtures, tmp_path, mode):
    run_native(mode, native_fixtures, tmp_path)


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.heavy
def test_native_minimum_size_bounds_collisions_and_reachability(native_fixtures, tmp_path):
    run_native("layout", native_fixtures, tmp_path)


@pytest.mark.native
@pytest.mark.integration
def test_native_icon_canvas_and_optical_centering(native_fixtures, tmp_path):
    run_native("icons", native_fixtures, tmp_path)


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.heavy
def test_native_product_check_long_cfr_priming_loop_input_and_fullscreen(native_fixtures, tmp_path):
    run_native("product", native_fixtures, tmp_path)


@pytest.mark.native
@pytest.mark.integration
def test_native_window_activation_before_adjacent_shortcut(native_fixtures, tmp_path):
    run_native("input", native_fixtures, tmp_path)


@pytest.mark.native
@pytest.mark.integration
def test_late_onset_timing_exact_indexed_global_ordinal_and_decoded_adjacency(native_fixtures, tmp_path):
    run_native('late-timing', native_fixtures, tmp_path)


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.heavy
def test_trim_excludes_out_from_actual_presentation_and_render_swaps(native_fixtures,tmp_path):
    run_native('trim-boundary',native_fixtures,tmp_path)


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.heavy
def test_crop_focused_other_field_real_inspector_wheel_scrolling(native_fixtures,tmp_path):
    run_native('wheel-fallback',native_fixtures,tmp_path)


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.heavy
def test_product_check_3_native_closure_matrix(native_fixtures,tmp_path):
    directory = native_fixtures.for_script("pc3_closure_native.py")
    env=os.environ.copy();env['QT_QPA_PLATFORM']='windows'
    result=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'tests/pc3_closure_native.py'),
                           '--fixtures',str(directory),'--output',str(tmp_path)],
                          cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf8',errors='replace',timeout=600)
    require_native_success(result,result.stdout+result.stderr)


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.heavy
def test_prepublication_trim_admission_native_matrix(native_fixtures,tmp_path):
    directory = native_fixtures.for_script("trim_admission_native.py")
    env=os.environ.copy();env['QT_QPA_PLATFORM']='windows'
    result=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'tests/trim_admission_native.py'),
                           '--fixtures',str(directory),'--output',str(tmp_path)],
                          cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf8',errors='replace',timeout=600)
    require_native_success(result,result.stdout+result.stderr)


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('script', [
    pytest.param('pc2_frame_native.py', marks=[pytest.mark.heavy, pytest.mark.performance]),
    pytest.param('pc2_workspace_native.py', marks=pytest.mark.heavy),
    pytest.param('pc2_index_native.py', marks=pytest.mark.heavy),
    pytest.param('pc2_origin_shutdown_native.py', marks=pytest.mark.heavy),
    'pc2_dispatch_native.py',
])
def test_product_check_2_complete_native_matrix(native_fixtures,tmp_path,script):
    directory = native_fixtures.for_script(script)
    env=os.environ.copy();env['QT_QPA_PLATFORM']='windows'
    if script=='pc2_origin_shutdown_native.py':
        # Native/Qt stderr and phase progress survive a blocked child. The
        # internal 60/90s diagnostic deadlines remain below the unchanged
        # outer 600s timeout; none of these failures can become a PASS.
        stdout_path,stderr_path=tmp_path/'stdout.log',tmp_path/'stderr.log'
        with stdout_path.open('wb',buffering=0) as stdout,stderr_path.open('wb',buffering=0) as stderr:
            result=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'tests'/script),
                '--fixtures',str(directory),'--output',str(tmp_path)],
                cwd=ROOT,env=env,stdout=stdout,stderr=stderr,timeout=600)
        require_native_success(result, stdout_path.read_text(encoding='utf-8',errors='replace')[-12000:]
            +stderr_path.read_text(encoding='utf-8',errors='replace')[-12000:])
        return
    result=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'tests'/script),
        '--fixtures',str(directory),'--output',str(tmp_path)],
        cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=600)
    require_native_success(result,result.stdout+result.stderr)
