"""C1 application boundary. No job/provider/render API is implied yet."""

from pathlib import Path
from typing import Protocol

from mediaforge.features.dub.contracts import (
    AudioFormat, AudioTrack, ClockFacts, DubControl, DubError, DubSourceDescriptor,
    OutputMode, OutputPlan, OutputRoute, Reason, Refusal, SourceIdentity, SourceKind,
    VideoFacts,
)


class DubSourceInspector(Protocol):
    def inspect(self, source: Path, control: DubControl) -> DubSourceDescriptor: ...
