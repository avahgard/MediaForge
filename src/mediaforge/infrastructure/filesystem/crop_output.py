"""Crop naming over the shared staging/publication primitive."""

from mediaforge.features.media.crop import CropError, CropErrorCode
from mediaforge.infrastructure.filesystem.staged_output import StagedOutput


class CropOutput(StagedOutput):
    def __init__(self, source, logger=None):
        super().__init__(source, "crop", lambda code: CropError(CropErrorCode(code)), logger=logger)
