"""Staging and atomic non-replacing publication shared by Media operations."""

import logging
from itertools import count
import os
from pathlib import Path
import tempfile


def publish_new(staged: Path, target: Path) -> Path:
    if os.name == "nt":
        # Windows rename fails if destination exists; unlike replace it never
        # overwrites. Both paths are on the source filesystem.
        os.rename(staged, target)
    else:
        # POSIX rename replaces; an atomic hard-link creation does not.
        os.link(staged, target)
    return target


class StagedOutput:
    def __init__(self, source: Path, operation, error, publisher=publish_new, logger=None):
        self._operation = operation
        self._error = error
        self._publisher = publisher
        self.source = source
        self.directory: Path | None = None
        self.staged: Path | None = None
        self._logger = logger or logging.getLogger("mediaforge")
        self.cleanup_guard = lambda: True

    def __enter__(self):
        try:
            self.directory = Path(tempfile.mkdtemp(prefix=f".mediaforge-{self._operation}-", dir=self.source.parent))
            self.staged = self.directory / ("partial" + self.source.suffix)
        except OSError as error:
            raise self._error("output_unavailable") from error
        return self

    def publish(self, control) -> Path:
        assert self.staged is not None
        # No exists-then-replace window. Every candidate is claimed by one
        # filesystem operation, including collisions created by another writer.
        for index in count():
            control.check()
            number = "" if index == 0 else f"_{index:03d}"
            target = self.source.with_name(f"{self.source.stem}_{self._operation}{number}{self.source.suffix}")
            try:
                return control.publish(lambda: self._publisher(self.staged, target))
            except FileExistsError:
                continue
            except OSError as error:
                raise self._error("publication_failed") from error

    def __exit__(self, *_args):
        # Only delete paths created by this instance; never glob user files.
        if not self.cleanup_guard():
            self._logger.error("Retaining staging because process termination failed: %s", self.directory)
            return
        try:
            if self.staged is not None:
                self.staged.unlink(missing_ok=True)
            if self.directory is not None:
                self.directory.rmdir()
        except OSError:
            self._logger.exception("Could not clean media staging: %s", self.directory)
