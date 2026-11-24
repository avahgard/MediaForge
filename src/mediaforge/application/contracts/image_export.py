"""Frozen source-based conversion request; no Viewer raster can enter this plan."""
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


@dataclass(frozen=True)
class NumberedOutput:
    prefix: str
    suffix: str
    extension: str
    start: int
    padding: int
    count: int

    def name(self,slot):
        if not 0<=slot<self.count:raise ValueError('Output slot outside frozen range.')
        return self.prefix+str(self.start+slot).zfill(self.padding)+self.suffix+self.extension

    @property
    def pattern(self):
        return self.prefix.replace('%','%%')+f'%0{self.padding}d'+self.suffix.replace('%','%%')+self.extension


@dataclass(frozen=True)
class PictureSnapshot:
    signature: object
    timeline: object
    ordinal: int | None
    source_generation: int
    player_generation: int
    offset_us: int


@dataclass(frozen=True)
class ImageExportRequest:
    source: Path
    destination: Path
    source_identity: object
    mode: str
    container: str
    width: int
    height: int
    source_width: int
    source_height: int
    start: int
    end: int
    rate: Fraction | None
    crop: object = None
    reformat: object = None
    matte: str = ''
    codec: str = 'h264'
    quality: int = 18
    prores_quality: int = 2
    snapshot: PictureSnapshot | None = None
    speed: str = 'balanced'
    h264_profile: str = 'auto'
    sar: Fraction = Fraction(1)
    jpeg_quality: int = 90
    png_compression: int = 6
    tiff_compression: str = 'deflate'
    control_bytes: int = 8*1024**2
    numbering: NumberedOutput | None = None
    replacement: object = None
    protected_sources: tuple = ()
    image_depth: str = '8'
    jpeg_subsampling: str = '444'
    webp_lossless: bool = True
    webp_quality: int = 90

    @property
    def count(self):return self.end-self.start

def jpeg_qscale(quality):
    if type(quality) is not int or not 1<=quality<=100:raise ValueError('JPEG Quality must be 1–100.')
    return 2+((100-quality)*29+49)//99

