"""Asynchronous optional-component commands; Qt never owns installation logic."""
from dataclasses import dataclass, replace
from threading import RLock, Thread

from mediaforge.application.contracts.models import ModelError
from mediaforge.features.media.processing_control import ProcessingControl


@dataclass(frozen=True)
class ModelsOperation:
    generation: int = 0
    state: str = 'idle'
    action: str = ''
    component: str = ''
    received: int = 0
    total: int = 0
    code: str = ''
    message: str = ''
    phase: str = ''
    object_name: str = ''


class ModelsControl(ProcessingControl):
    def settle(self):
        """Linearize completed mutation status against late Cancel, no file publish."""
        with self._gate:
            if self._cancelled:
                raise self._cancelled_error()
            self._published = True


class OptionalModelsService:
    """One explicit operation, bounded lifetime, cancel and honest readiness."""
    def __init__(self, resources):
        self.resources = resources
        self._lock = RLock()
        self._job = ModelsOperation()
        self._thread = None
        self._control = None
        self._closed = False
        self._plan = None

    @property
    def busy(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def snapshot(self):
        with self._lock:
            return self._job

    def inventory(self):
        return self.resources.inventory()

    def requirements(self):
        return self.resources.sizes()

    @property
    def pending_plan(self):
        with self._lock:
            return self._plan

    def _start(self, action, invoke, *, component=''):
        with self._lock:
            if self._closed or self.busy or self.resources.gate.failure or self.resources.gate.busy:
                return False
            generation = self._job.generation + 1
            self._job = ModelsOperation(generation, 'running', action, component)
            self._plan = None
            control = ModelsControl(lambda: ModelError('cancelled', 'Models operation cancelled; refresh inventory for any already verified components.'))
            self._control = control
            def work():
                try:
                    result = invoke(control)
                    control.settle()
                    plan = result if action in ('plan', 'apply') else None
                    if plan is not None:
                        outcome = dict(state='consent', message=plan.blocked_reason)
                    else:
                        message = 'Verified component bytes installed. Hardware is checked before inference readiness.'
                        if action == 'verify':
                            rows = result if isinstance(result, (list, tuple)) else self.inventory()
                            verified = sum(row['state'] == 'verified_unqualified' for row in rows)
                            message = f'Offline check complete. {verified} of {len(rows)} components verified.'
                        outcome = dict(state='completed', message=message)
                except Exception as error:
                    code = str(getattr(error, 'code', 'component_failed'))
                    outcome = dict(state='cancelled' if code == 'cancelled' else 'failed',
                                   code=code, message=str(error))
                    plan = None
                with self._lock:
                    if self._job.generation == generation:
                        self._plan = plan
                        self._job = replace(self._job, **outcome)
            self._thread = Thread(target=work, name='MediaForge Models', daemon=False)
            self._thread.start()
            return True

    def install(self, accepted_network_bytes, *, selected=None):
        # The bridge supplies confirmation of the displayed immutable profile.
        # Starting the app/entering a page never invokes this command.
        if (hasattr(self.resources, 'acquisition') or type(accepted_network_bytes) is not int
                or accepted_network_bytes != self.requirements()['network']):
            raise ModelError('consent_required', 'Confirm the current optional download requirements.')
        return self._start('install', lambda c: self.resources.install(c, self._progress, selected=selected))

    def remove(self, key, *, discard_damaged=False):
        if not any(row['id'] == key for row in self.inventory()):
            raise ModelError('unknown_model', 'Choose an installed optional component.')
        return self._start('remove', lambda c: self.resources.remove(key, c,discard_damaged=True)
                           if discard_damaged else self.resources.remove(key,c))

    def verify(self):
        return self._start('verify',lambda c:self.resources.verify(c,self._progress))

    def prepare_plan(self, selected, repair):
        return self._start('plan', lambda c: self.resources.acquisition.prepare(selected, repair, c, self._progress),
                           component=selected)

    def confirm_plan(self, expected):
        with self._lock:
            plan = self._plan
            if plan is None or plan is not expected or plan.blocked_reason:
                return False
            return self._start('apply', lambda c: self.resources.acquisition.dispatch(plan, c, self._progress),
                               component=plan.selected)

    def dismiss_plan(self):
        with self._lock:
            if self._plan is not None:
                self._plan = None
                self._job = replace(self._job, state='idle')

    def _progress(self, stage, name, count, total, object_name=''):
        with self._lock:
            same_object = (self._job.phase, self._job.component, self._job.object_name) == (stage, name, object_name)
            value = self._job.received + count if same_object and count else count
            if not 0 <= value <= total:
                raise ModelError('invalid_output', 'Optional progress exceeded frozen object size.')
            self._job = replace(self._job, phase=stage, component=name, object_name=object_name,
                                received=value, total=total)

    def cancel(self):
        with self._lock:
            if self.busy and self._control.cancel():
                self._job = replace(self._job, state='cancelling')

    def shutdown(self):
        with self._lock:
            self._closed = True
            self.cancel()
            thread = self._thread
        if thread is not None:
            thread.join(25)  # bounded HTTP timeout plus retirement
            if thread.is_alive():
                self.resources.gate.quarantine(self, 'Models worker did not retire at shutdown.')
                raise ModelError('lifecycle_failed', 'Models worker still owns resources.', retained_resources=(self,))
        if self.resources.gate.failure:
            raise ModelError('lifecycle_failed', self.resources.gate.failure, retained_resources=tuple(self.resources.retained))
