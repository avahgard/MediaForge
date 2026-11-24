"""Immutable authorization for optional component acquisition."""
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class DownloadAuthority:
    url: str
    size: int
    sha256: str
    publisher: str
    hosts: frozenset[str]


@dataclass(frozen=True)
class ComponentAcquisition:
    key: str
    revision: str
    destination: Path
    action: str
    companion: Path | None
    companion_action: str
    installed_bytes: int
    cached_assets: tuple[str, ...] = ()
    damaged_assets: tuple[Path, ...] = ()
    reset_intent: bool = False


@dataclass(frozen=True)
class AcquisitionPlan:
    selected: str
    repair: bool
    components: tuple[ComponentAcquisition, ...]
    retained_components: tuple[str, ...]
    publishers: tuple[str, ...]
    network_bytes: int
    installed_bytes: int
    retained_bytes: int
    retained_data_bytes: int
    temporary_bytes: int
    required_bytes: int
    available_bytes: int
    downloads: tuple[DownloadAuthority, ...]
    blocked_reason: str = ''

    def authorizes(self, current):
        # A free-space sample may move without changing the operation.
        # Changed scope, reuse, publishers or bounds always need consent.
        return not current.blocked_reason and self == replace(
            current, available_bytes=self.available_bytes, retained_data_bytes=self.retained_data_bytes)
