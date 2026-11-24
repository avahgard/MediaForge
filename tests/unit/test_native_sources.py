"""Lazy selection must preserve recipe and wrapper dependency contracts."""

from collections import Counter
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import native_sources
from tests.native_sources import NativeSources
from tests.presentation import test_workspace_native as wrappers


CASES = [
    ("integral", {"integral.mp4"}),
    ("thirty", {"thirty.mp4"}),
    ("fractional", {"fractional.mp4"}),
    ("unreliable", {"variable.mp4"}),
    ("layout", {"integral.mp4", "fractional.mp4", "variable.mp4"}),
    ("icons", {"integral.mp4"}),
    ("product", {"integral.mp4", "long60.mp4"}),
    ("input", {"thirty.mp4"}),
    ("late-timing", {"integral.mp4", "cfr-control.mp4", "late-vfr.mp4"}),
    ("trim-boundary", {"integral.mp4"}),
    ("wheel-fallback", {"long60.mp4"}),
    ("pc3_closure_native.py", {"integral.mp4"}),
    ("trim_admission_native.py", {"integral.mp4", "fractional.mp4"}),
    ("pc2_frame_native.py", {"integral.mp4", "fractional.mp4", "long60.mp4",
                              "practical.mp4", "variable.mp4", "cfr-control.mp4", "late-vfr.mp4"}),
    ("pc2_workspace_native.py", {"integral.mp4"}),
    ("pc2_index_native.py", {"practical.mp4", "integral.mp4", "variable.mp4",
                              "cfr-control.mp4", "late-vfr.mp4"}),
    ("pc2_origin_shutdown_native.py", {"integral.mp4", "origin5.mp4", "practical.mp4"}),
    ("pc2_dispatch_native.py", {"integral.mp4"}),
]


@pytest.fixture
def recipe_spy(monkeypatch):
    generated = []

    def generate(self, name):
        if name == "late-vfr.mp4":
            assert (self.directory / "cfr-control.mp4").read_bytes() == b"cfr-control.mp4"
            (self.directory / "late-vfr-fixture.json").write_text("{}")
        if name == "origin5.mp4":
            assert (self.directory / "integral.mp4").read_bytes() == b"integral.mp4"
        generated.append(name)
        (self.directory / name).write_bytes(name.encode())

    monkeypatch.setattr(NativeSources, "_generate", generate)
    return generated


def invoke(case, sources, output):
    if case == "pc3_closure_native.py":
        wrappers.test_product_check_3_native_closure_matrix(sources, output)
    elif case == "trim_admission_native.py":
        wrappers.test_prepublication_trim_admission_native_matrix(sources, output)
    elif case.endswith(".py"):
        wrappers.test_product_check_2_complete_native_matrix(sources, output, case)
    else:
        wrappers.run_native(case, sources, output)


@pytest.mark.parametrize("case,expected", CASES)
def test_actual_wrapper_requests_only_its_dependencies(tmp_path, monkeypatch, recipe_spy, case, expected):
    directory = tmp_path / "native workspace Кириллица"
    directory.mkdir()
    sources = NativeSources(directory, None)
    output = tmp_path / "fresh output"
    output.mkdir()

    def child(command, **options):
        assert Path(command[command.index("--fixtures") + 1]) == directory
        assert set(p.name for p in directory.glob("*.mp4")) == expected
        assert options["timeout"] == (600 if case.endswith(".py") else 400)
        assert options["env"]["QT_QPA_PLATFORM"] == "windows"
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(wrappers.subprocess, "run", child)
    invoke(case, sources, output)
    invoke(case, sources, output)
    assert Counter(recipe_spy) == Counter({name: 1 for name in expected})


def test_mixed_full_module_requests_generate_each_recipe_once(tmp_path, monkeypatch, recipe_spy):
    sources = NativeSources(tmp_path, None)
    monkeypatch.setattr(wrappers.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout="", stderr=""))
    for case, _ in CASES + list(reversed(CASES)):
        invoke(case, sources, tmp_path)
    assert Counter(recipe_spy) == Counter({name: 1 for name in native_sources.DEPENDENCIES})
    assert recipe_spy.index("cfr-control.mp4") < recipe_spy.index("late-vfr.mp4")
    assert recipe_spy.index("integral.mp4") < recipe_spy.index("origin5.mp4")
    sources.write_generation()
    report = json.loads((tmp_path / "native-source-generation.json").read_text())
    assert len(report["recipes"]) == 9
    assert all(record["succeeded"] and record["seconds"] >= 0 for record in report["recipes"])
    assert report["generated_bytes"] == sum(p.stat().st_size for p in tmp_path.glob("*.mp4")) + 2


def test_new_store_never_uses_existing_files_as_cross_run_hits(tmp_path, recipe_spy):
    (tmp_path / "integral.mp4").write_bytes(b"old entry")
    first = NativeSources(tmp_path, None)
    assert not recipe_spy
    first.require("integral.mp4")
    first.require("integral.mp4")
    second = NativeSources(tmp_path, None)
    second.require("integral.mp4")
    assert recipe_spy == ["integral.mp4", "integral.mp4"]


def test_dependency_only_then_dependent_keeps_control_once(tmp_path, recipe_spy):
    sources = NativeSources(tmp_path, None)
    sources.require("cfr-control.mp4", "integral.mp4")
    sources.require("late-vfr.mp4", "origin5.mp4")
    sources.require("origin5.mp4", "late-vfr.mp4")
    assert recipe_spy == ["cfr-control.mp4", "integral.mp4", "late-vfr.mp4", "origin5.mp4"]


def test_unknown_request_allocates_nothing(tmp_path, recipe_spy):
    sources = NativeSources(tmp_path, None)
    with pytest.raises(KeyError):
        sources.require("integral.mp4", "typo.mp4")
    with pytest.raises(KeyError):
        sources.for_mode("unknown")
    with pytest.raises(KeyError):
        sources.for_script("unknown.py")
    assert not recipe_spy and not list(tmp_path.iterdir())


def test_failed_dependency_is_not_retried_or_published_as_complete(tmp_path, monkeypatch):
    attempts = []
    error = RuntimeError("generator failed")

    def generate(self, name):
        attempts.append(name)
        (self.directory / name).write_bytes(b"partial")
        raise error

    monkeypatch.setattr(NativeSources, "_generate", generate)
    sources = NativeSources(tmp_path, None)
    for _ in range(2):
        with pytest.raises(RuntimeError) as failure:
            sources.require("origin5.mp4")
        assert failure.value is error
    assert attempts == ["integral.mp4"]
    assert not (tmp_path / "origin5.mp4").exists()
    sources.write_generation()
    report = json.loads((tmp_path / "native-source-generation.json").read_text())
    assert report["generated_bytes"] == 7 and not report["recipes"][0]["succeeded"]


@pytest.mark.parametrize("name,rate,size,duration,preset,timeout,vfr", [
    ("integral.mp4", "25", "320x180", 4, "ultrafast", 150, False),
    ("fractional.mp4", "24000/1001", "320x180", 4, "ultrafast", 150, False),
    ("variable.mp4", "25", "320x180", 4, "ultrafast", 150, True),
    ("long60.mp4", "60", "1024x768", 180, "ultrafast", 150, False),
    ("thirty.mp4", "30", "320x180", 4, "ultrafast", 150, False),
    ("practical.mp4", "25", "1280x720", 600, "veryfast", 600, False),
])
def test_base_recipe_exact_command(tmp_path, monkeypatch, name, rate, size, duration, preset, timeout, vfr):
    commands = []

    def run(command, **options):
        commands.append((command, options))
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(native_sources.subprocess, "run", run)
    sources = NativeSources(tmp_path, SimpleNamespace(ffmpeg=Path("authenticated ffmpeg.exe")))
    sources.require(name)
    expected = ["authenticated ffmpeg.exe", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={duration}",
                "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
    if vfr:
        expected += ["-vf", r"select=not(mod(n\,2))+not(mod(n\,5))", "-fps_mode:v", "vfr"]
    expected += ["-c:v", "libx264", "-preset", preset, "-g", "250", "-bf", "2", "-pix_fmt",
                 "yuv420p", "-c:a", "aac", "-shortest", str(tmp_path / name)]
    assert commands == [(expected, {"capture_output": True, "text": True, "timeout": timeout})]


def test_origin_recipe_exact_stream_copy_command(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(native_sources.subprocess, "run", lambda command, **options:
                        commands.append((command, options)) or SimpleNamespace(returncode=0, stderr=""))
    sources = NativeSources(tmp_path, SimpleNamespace(ffmpeg=Path("authenticated ffmpeg.exe")))
    sources.require("origin5.mp4")
    assert commands[-1] == (["authenticated ffmpeg.exe", "-hide_banner", "-loglevel", "error",
        "-i", str(tmp_path / "integral.mp4"), "-c", "copy", "-output_ts_offset", "5",
        str(tmp_path / "origin5.mp4")], {"capture_output": True, "text": True, "timeout": 30})
