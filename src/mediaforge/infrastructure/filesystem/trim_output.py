"""Trim naming over the shared staging/publication primitive."""

# Retain the accepted Trim module's publication seam for its regression probes.
from mediaforge.infrastructure.filesystem.staged_output import StagedOutput, publish_new, tempfile
from mediaforge.features.media.trim import TrimError, TrimErrorCode


class TrimOutput(StagedOutput):
    def __init__(self, source, logger=None):
        super().__init__(source, "trim", lambda code: TrimError(TrimErrorCode(code)),
                         publisher=lambda staged, target: publish_new(staged, target), logger=logger)
