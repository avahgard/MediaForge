"""A blocked diagnostic must preserve evidence and remain a failed child."""
import json
from pathlib import Path
import subprocess
import sys
import pytest


HELPERS = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_watchdog_captures_blocked_phase_threads_and_reaps_owned_child(tmp_path):
    output = tmp_path / "watchdog"
    code = f'''
import sys, threading, subprocess
from types import SimpleNamespace
sys.path.insert(0, {str(HELPERS)!r})
from native_phase_diagnostics import NativePhaseDiagnostics
retained = SimpleNamespace(factory=subprocess.Popen)
d = NativePhaseDiagnostics({str(output)!r}, timeout=.5, native_timeout=5)
d.track_factory(retained, 'factory')
d.case = 'injected-python-block'
with d.phase_scope('owned-child-and-block'):
    subprocess.Popen([sys.executable, '-c', 'import threading; threading.Event().wait()'])
    retained.factory([sys.executable, '-c', 'import threading; threading.Event().wait()'])
    threading.Event().wait()
'''
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", code], capture_output=True,
                            text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 124, result.stdout + result.stderr
    snapshot = json.loads((output / "watchdog.json").read_text(encoding="utf-8"))
    assert snapshot["case"] == "injected-python-block" and snapshot["phase"] == "owned-child-and-block"
    assert len(snapshot["owned_subprocesses"]) == 2
    journal = [json.loads(line) for line in (output / "phases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert journal[-1]["event"] == "watchdog-owned-child-cleanup"
    assert all(child["returncode"] is not None for child in journal[-1]["owned_subprocesses"])
    assert "threading.py" in (output / "thread-stacks.log").read_text(encoding="utf-8")


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "win32", reason="Windows native GIL-held diagnostic control")
def test_native_watchdog_retains_phase_and_stacks_when_native_call_holds_gil(tmp_path):
    output = tmp_path / "native-block"
    code = f'''
import sys, ctypes
sys.path.insert(0, {str(HELPERS)!r})
from native_phase_diagnostics import NativePhaseDiagnostics
d = NativePhaseDiagnostics({str(output)!r}, timeout=.2, native_timeout=.6)
d.case = 'injected-native-block'
with d.phase_scope('gil-held-native-call'):
    ctypes.PyDLL('kernel32').Sleep(5000)
'''
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", code], capture_output=True,
                            text=True, encoding="utf-8", timeout=10)
    assert result.returncode != 0, result.stdout + result.stderr
    phases = [json.loads(line) for line in (output / "phases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert phases[-1]["phase"] == "gil-held-native-call"
    assert phases[-1]["event"] == "phase-snapshot"
    stacks = (output / "thread-stacks.log").read_text(encoding="utf-8")
    assert "<string>" in stacks and "native_phase_diagnostics.py" in stacks
