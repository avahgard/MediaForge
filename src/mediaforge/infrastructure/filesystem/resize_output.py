"""Resize naming over the shared staging/publication primitive."""

from mediaforge.features.media.resize import ResizeError, ResizeErrorCode
from mediaforge.infrastructure.filesystem.staged_output import StagedOutput


class ResizeOutput(StagedOutput):
    def __init__(self, source, logger=None):
        super().__init__(source, "resize", lambda code: ResizeError(ResizeErrorCode(code)), logger=logger)
