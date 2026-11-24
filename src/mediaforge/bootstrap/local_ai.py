"""Lazy C2 composition: shares the caller's gate; never installs or loads on construction."""
from dataclasses import dataclass

from mediaforge.application.services.models import ModelService
from mediaforge.infrastructure.models.catalog import CANDIDATES
from mediaforge.infrastructure.models.providers import LocalProviders
from mediaforge.infrastructure.models.store import PortableModelStore


@dataclass(frozen=True)
class LocalAiServices:
    models:ModelService
    providers:LocalProviders


def compose_local_ai(paths,runtime,gate,*,specs=CANDIDATES):
    providers=LocalProviders(paths,runtime,gate)
    models=ModelService(specs,PortableModelStore(paths),runtime_fingerprint=runtime.manifest_sha256,smoke=providers.smoke)
    providers.models=models
    return LocalAiServices(models,providers)
