"""F-002: Qt signal failure survives one-shot guards and reaches process result."""

from pathlib import Path

import pytest


@pytest.mark.integration
@pytest.mark.parametrize("operation", ["crop", "trim"])
@pytest.mark.parametrize("failure", [False, True])
def test_about_to_quit_records_error_and_duplicate_shutdown_is_idempotent(run_python, operation, failure):
    result = run_python(f'''
import logging
from PySide6.QtCore import QTimer
from mediaforge.bootstrap import application as bootstrap
from mediaforge.presentation.bridge.media import MediaViewModel
class UseCase:
    busy = False
    calls = 0
    def shutdown(self):
        self.calls += 1
        if {failure!r}: raise RuntimeError("controlled shutdown failure")
case = UseCase()
models, signals = [], []
def build(logger, *, profile, gate, runtime):
    model = MediaViewModel(None, logger=logger, **{{{operation + '_use_case'!r}: case}})
    models.append(model)
    return model
def load(engine, source, logger):
    def observed():
        signals.append(models[0].shutdown_error)
    bootstrap.QGuiApplication.instance().aboutToQuit.connect(observed)
    QTimer.singleShot(0, bootstrap.QGuiApplication.instance().quit)
bootstrap.build_media_view_model = build
bootstrap.load_shell = load
code = bootstrap.main([])
model = models[0]
assert code == {2 if failure else 0}, code
assert len(signals) == 1 and (signals[0] is not None) == {failure!r}
first_error = model.shutdown_error
for target in (model, getattr(model, {operation!r})):
    for _ in range(2):
        try: target.shutdown()
        except RuntimeError as error:
            assert {failure!r} and error is first_error
        else: assert not {failure!r}
assert case.calls == 1
assert model.shutdown_error is first_error
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
log = (ProfilePaths.from_environment().logs / "application.log").read_text(encoding="utf-8")
if {failure!r}:
    assert "controlled shutdown failure" in log
    assert "Application shutdown: 0" not in log
else:
    assert "Application shutdown: 0" in log
''')
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
@pytest.mark.parametrize("operation", ["crop", "trim"])
@pytest.mark.parametrize("refuses", [False, True])
def test_actual_owned_child_shutdown_reaches_bootstrap_result(run_python, operation, refuses):
    support = str(Path(__file__).resolve().parents[1] / "unit")
    result = run_python(f'''
import sys
from pathlib import Path
sys.path.insert(0, {support!r})
from crop_support import CountingGate, metadata, setup
from test_processing_shutdown_failure import controlled_factory, make_service, start
from PySide6.QtCore import QTimer
from mediaforge.bootstrap import application as bootstrap
from mediaforge.presentation.bridge.media import MediaViewModel
work = Path.cwd()
request, pair = setup(work)
gate = CountingGate()
phase = {2 if operation == 'crop' else 0}
factory, calls, children, entered = controlled_factory({operation!r}, phase, {refuses!r})
service = make_service({operation!r}, pair, factory, gate)
service.set_source(metadata(request.source))
models = []
def build(logger, *, profile, gate, runtime):
    model = MediaViewModel(None, logger=logger, **{{{operation + '_use_case'!r}: service}})
    models.append(model)
    return model
def load(engine, source, logger):
    assert start(service, {operation!r}) and entered.wait(2)
    QTimer.singleShot(0, bootstrap.QGuiApplication.instance().quit)
bootstrap.build_media_view_model = build
bootstrap.load_shell = load
assert bootstrap.main([]) == {2 if refuses else 0}
assert not service.busy and len(calls) == phase + 1
assert gate.busy == {refuses!r}
assert gate.released == {0 if refuses else 1}
assert (models[0].shutdown_error is not None) == {refuses!r}
assert (children[-1].poll() is None) == {refuses!r}
assert bool(list(work.glob(".mediaforge-*/partial.mp4"))) == {refuses!r}
assert service.snapshot().state == {('Failed' if refuses else 'Cancelled')!r}
''')
    assert result.returncode == 0, result.stderr
