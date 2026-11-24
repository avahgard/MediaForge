"""Final Dub optional requirements and accepted C2/C3 provider composition."""
from dataclasses import dataclass
from pathlib import Path
import sys

from mediaforge.application.contracts.models import ModelSpec
from mediaforge.application.services.optional_models import OptionalModelsService
from mediaforge.bootstrap.local_ai import compose_local_ai
from mediaforge.infrastructure.models.dub_requirements import DubRequirements
from mediaforge.infrastructure.models.providers import WorkerRuntime
from mediaforge.infrastructure.models.final_dub_runtime import FinalDubWorkerRuntime,ENTRY


@dataclass(frozen=True)
class DubModels:
    requirements: object
    operations: object
    local_ai: object


def compose_dub_models(paths, gate, *, python=None):
    """Composition is lazy: no acquisition, native smoke or model load here."""
    requirements = DubRequirements(paths, gate)
    cpu = requirements.cpu
    fingerprint = cpu.profile.data['runtime_manifest_sha256']
    specs = tuple(ModelSpec(s.model_id, s.revision, s.members, fingerprint, True, 'production')
                  for s in cpu.profile.models)
    runtime = FinalDubWorkerRuntime(Path(python or sys._base_executable),
                            cpu.store.location(cpu.profile.runtime),
                            paths.manifests / cpu.profile.data['manifest_name'], fingerprint,ENTRY)
    local = compose_local_ai(paths, runtime, gate, specs=specs)
    return DubModels(requirements, OptionalModelsService(requirements), local)
