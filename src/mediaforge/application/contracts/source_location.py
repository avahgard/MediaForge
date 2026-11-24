"""Narrow platform port for revealing an existing source, never executing it."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RevealResult:
    succeeded: bool
    message: str = ''


class SourceLocationRevealer(Protocol):
    def reveal(self, source_path: str) -> RevealResult: ...
