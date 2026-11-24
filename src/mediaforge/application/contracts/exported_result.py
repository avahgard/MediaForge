"""Completed-export import description, never a Viewer raster or a success bypass."""
from dataclasses import dataclass
from fractions import Fraction
from typing import Protocol,ContextManager
from mediaforge.application.contracts.image_export import NumberedOutput
from mediaforge.features.media.export import OutputIdentity,OutputFileIdentity


@dataclass(frozen=True)
class ExportedResult:
    path: str
    kind: str
    width: int
    height: int
    format: str
    numbering: NumberedOutput | None = None
    rate: Fraction | None = None
    job_id: int = 0
    identity: OutputIdentity | None = None
    viewer_supported: bool = True


class PreparedResult(Protocol):
    def check(self) -> None: ...
    def close(self) -> None: ...
    def activating(self) -> ContextManager: ...
