"""One inventory facade for the two isolated CPU/CUDA optional profiles."""
from mediaforge.application.contracts.models import ModelError
from mediaforge.infrastructure.models.acquisition import DubAcquisition
from mediaforge.infrastructure.models.optional_resources import (
    OptionalResources, load_cpu_profile, MAX_ACQUISITION, MAX_DISK,
)


class DubRequirements:
    def __init__(self, paths, gate):
        self.paths, self.gate = paths, gate
        self.cpu = OptionalResources(paths, gate, profile=load_cpu_profile())
        self.qwen = OptionalResources(paths, gate)
        self.profiles = (self.cpu, self.qwen)
        self._owners = {s.model_id: p for p in self.profiles for s in p.specs}
        if len(self._owners) != sum(len(p.specs) for p in self.profiles):
            raise ModelError('unqualified', 'Duplicate optional resource identities.')
        self.acquisition = DubAcquisition(self)

    @property
    def retained(self):
        return [owner for profile in self.profiles for owner in profile.retained]

    def inventory(self):
        return tuple(row for profile in self.profiles for row in profile.inventory())

    def sizes(self):
        rows = [p.sizes() for p in self.profiles]
        network = sum(r['network'] for r in rows)
        installed = sum(r['installed'] for r in rows)
        peak = network + installed + 2 * 1024**3
        if network > MAX_ACQUISITION or peak > MAX_DISK:
            raise ModelError('disk_budget', 'Combined Dub requirements exceed the approved envelope.')
        return dict(network=network, installed=installed, peak=peak, ready=False)

    def install(self, control, progress=lambda *args: None, *, selected=None):
        chosen = set(selected) if selected is not None else set(self._owners)
        if not chosen or chosen - set(self._owners):
            raise ModelError('unknown_model', 'Unknown optional requirements selection.')
        token=self.gate.acquire()
        if token is None:raise ModelError('in_use','Another operation owns processing.')
        try:
            for profile in self.profiles:
                keys = chosen & {s.model_id for s in profile.specs}
                if keys:
                    control.check()
                    profile.install(control, progress, selected=keys,gate_owner=token)
        finally:
            if not self.gate.failure:self.gate.release(token)
        return self.inventory()

    def verify(self,control,progress=lambda *args:None):
        token=self.gate.acquire()
        if token is None:raise ModelError('in_use','Another operation owns processing.')
        try:
            for profile in self.profiles:
                profile.verify(control, progress, gate_owner=token, complete_inventory=True)
        finally:
            if not self.gate.failure:self.gate.release(token)
        return self.inventory()

    def remove(self, key, control, *, discard_damaged=False):
        if key not in self._owners:
            raise ModelError('unknown_model', 'Unknown optional component.')
        return self._owners[key].remove(key, control,discard_damaged=discard_damaged)
