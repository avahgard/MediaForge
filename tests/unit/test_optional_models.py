from threading import Event
import time

import pytest

from mediaforge.application.contracts.models import ModelError
from mediaforge.application.services.optional_models import OptionalModelsService, ModelsControl
from mediaforge.application.services.processing_gate import MediaProcessingGate


class Resources:
    def __init__(self):
        self.gate = MediaProcessingGate()
        self.retained = []
        self.calls = []
        self.entered = Event()
        self.release = Event()

    def inventory(self):
        return (dict(id='qwen', state='missing'),)

    def sizes(self):
        return dict(network=123, installed=456, peak=789, ready=False)

    def install(self, control, progress, selected=None):
        self.calls.append(('install', selected))
        self.entered.set()
        while not self.release.wait(.005):
            control.check()
        progress('download', 'fixture', 123, 123)

    def remove(self, key, control):
        self.calls.append(('remove', key))


def await_idle(service):
    end = time.monotonic() + 2
    while service.busy and time.monotonic() < end:
        time.sleep(.005)
    assert not service.busy


def test_inventory_and_constructor_never_start_installation():
    resources = Resources()
    service = OptionalModelsService(resources)
    assert service.inventory()[0]['state'] == 'missing'
    assert not service.requirements()['ready']
    assert resources.calls == []


def test_download_requires_exact_current_disclosure_confirmation():
    resources = Resources()
    service = OptionalModelsService(resources)
    with pytest.raises(ModelError, match='Confirm'):
        service.install(122)
    assert not resources.calls


def test_cancel_settles_worker_before_allowing_explicit_retry():
    resources = Resources()
    service = OptionalModelsService(resources)
    assert service.install(123)
    assert resources.entered.wait(1)
    assert not service.install(123)
    service.cancel()
    await_idle(service)
    assert service.snapshot().state == 'cancelled'
    resources.release.set()
    assert service.install(123)
    await_idle(service)
    assert service.snapshot().state == 'completed'
    assert 'inference readiness' in service.snapshot().message
    assert service.snapshot().generation == 2
    service.shutdown()
    assert not service.install(123)


def test_backend_failure_cannot_be_reported_as_completed():
    resources = Resources()
    def fail(control, progress, selected=None):
        raise ModelError('corrupt', 'Changed optional member.')
    resources.install = fail
    service = OptionalModelsService(resources)
    assert service.install(123)
    await_idle(service)
    assert service.snapshot().state == 'failed'
    assert service.snapshot().code == 'corrupt'


def test_cancel_after_settled_component_result_is_not_accepted():
    control = ModelsControl(lambda: ModelError('cancelled', 'cancelled'))
    control.settle()
    assert not control.cancel()
    control.check()
