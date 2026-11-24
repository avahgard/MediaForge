"""Exact, Qt-independent Reformat geometry shared by preview and encoding.

Continuous placement uses rational arithmetic. Scale products are quantized to
nearest even pixels (ties upward); explicit Format canvases are never rounded.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from mediaforge.features.media.resize import ResizeDimensions, ResizeError


PRESETS = (("HD", 1280, 720), ("FHD", 1920, 1080), ("2K DCI", 2048, 1080),
           ("UHD", 3840, 2160), ("4K DCI", 4096, 2160), ("8K UHD", 7680, 4320))


class GeometryError(ValueError):
    pass


def sample_aspect(value="1:1") -> Fraction:
    # FFprobe uses 0:1 / N/A for unspecified SAR, interpreted as square pixels
    # by the native renderer too. Malformed declared ratios fail closed.
    if value in (None, "N/A", "0:1", "0/1"):
        return Fraction(1)
    try:
        if not isinstance(value, str) or len(value) > 32:
            raise ValueError
        ratio = Fraction(value.replace(":", "/"))
        if ratio <= 0 or ratio.numerator > 2_147_483_647 or ratio.denominator > 2_147_483_647:
            raise ValueError
        return ratio
    except (ValueError, ZeroDivisionError):
        raise GeometryError("Invalid source sample aspect ratio.") from None


def _ceil(value):
    return -(-value.numerator // value.denominator)


@dataclass(frozen=True)
class EncodingGeometry:
    width: int
    height: int
    x: int
    y: int
    source_x: int
    source_y: int
    source_width: int
    source_height: int


def dimensions(width, height):
    try:
        ResizeDimensions(width, height).validate()
    except ResizeError:
        raise GeometryError("Use even integer dimensions from 16 to 7680, at most 33,177,600 pixels.") from None
    return ResizeDimensions(width, height)


@dataclass(frozen=True)
class ScaleRaster:
    output: ResizeDimensions
    desired_width: Fraction
    desired_height: Fraction

    @property
    def quantized(self):
        return (self.desired_width != self.output.width
                or self.desired_height != self.output.height)


def scale_raster(width: int, height: int, text: str) -> ScaleRaster:
    # Bound input length/exponent before performing any potentially large arithmetic.
    try:
        if not isinstance(text, str) or len(text) > 32:
            raise ValueError
        value = Decimal(text)
        if not value.is_finite() or not Decimal("0.000001") <= value <= Decimal("480"):
            raise ValueError
        factor = Fraction(value)
    except (InvalidOperation, ValueError, OverflowError):
        raise GeometryError("Enter a finite positive decimal multiplier (up to 480x).") from None
    w, h = width * factor, height * factor
    def even(value):
        nearest = value / 2 + Fraction(1, 2)
        return 2 * (nearest.numerator // nearest.denominator)
    # Quantize first, then validate the actual coded canvas. No search for a
    # nearby legal size and no clamping of a rejected rounded result.
    return ScaleRaster(dimensions(even(w), even(h)), w, h)


def scaled_dimensions(width: int, height: int, text: str):
    return scale_raster(width, height, text).output


def locked_dimension(value: int, input_axis: int, other_axis: int) -> int:
    if type(value) is not int or input_axis <= 0 or other_axis <= 0:
        raise GeometryError("Enter an integer dimension.")
    other = Fraction(value * other_axis, input_axis)
    if other.denominator != 1:
        raise GeometryError(f"Aspect lock derives a fractional dimension ({other}); choose an exact size or unlock.")
    dimensions(value, int(other))
    return int(other)


@dataclass(frozen=True)
class ReformatPlan:
    input_width: int
    input_height: int
    output: ResizeDimensions
    picture_width: Fraction
    picture_height: Fraction
    x: Fraction
    y: Fraction
    placement: str
    sar: Fraction = Fraction(1)

    def encoding_geometry(self):
        g = self.encoding_window()
        return g.width, g.height, g.x, g.y

    def encoding_window(self):
        sw, sh = self.input_width, self.input_height
        sx = sy = 0
        if self.placement == "stretch":
            w, h = self.output.width, self.output.height
        elif self.placement == "fit":
            # Containment wins at a fractional edge; error is <1 output pixel.
            w, h = max(1, int(self.picture_width)), max(1, int(self.picture_height))
        else:
            # Crop an enclosing, exactly centered source window BEFORE scaling.
            # This avoids allocating the potentially enormous virtual Fill raster.
            factor = max(Fraction(self.output.width, sw) / self.sar,
                         Fraction(self.output.height, sh))
            def enclosing(ideal, axis):
                size = min(axis, max(1, _ceil(ideal)))
                if (axis - size) % 2:
                    size = min(axis, size + 1)
                return size
            sw = enclosing(Fraction(self.output.width) / factor / self.sar, sw)
            sh = enclosing(Fraction(self.output.height) / factor, sh)
            sx, sy = (self.input_width - sw) // 2, (self.input_height - sh) // 2
            w, h = _ceil(sw * self.sar * factor), _ceil(sh * factor)
        return EncodingGeometry(w, h, (self.output.width - w) // 2,
                                (self.output.height - h) // 2, sx, sy, sw, sh)

    def encoding_steps(self):
        """Bounded Lanczos sampling of the window, followed by pad/crop.

        Fill can select a sub-sample source window at extreme aspect ratios.
        Repeated centered enlargement/cropping represents that zoom without
        allocating the virtual full picture. Each scaled raster is at most twice the
        canvas area; earlier crops have even extents and exactly shared centers.
        Only the final sampling/placement can differ by <=1 / <=0.5 pixel.
        """
        g = self.encoding_window()
        steps = []
        if self.placement != "fill":
            return (("scale", g.width, g.height),)
        horizontal = g.width > self.output.width
        extent = g.width if horizontal else g.height
        axis = self.output.width if horizontal else self.output.height
        fixed = g.height if horizontal else g.width
        sampled = min(extent, 2 * axis)
        def scale(size):
            return ("scale", size, fixed) if horizontal else ("scale", fixed, size)
        def crop(size):
            return (("crop", axis, fixed, (size-axis)//2, 0) if horizontal
                    else ("crop", fixed, axis, 0, (size-axis)//2))
        steps.append(scale(sampled))
        remainder = Fraction(extent, sampled)
        while remainder > 1:
            steps.append(crop(sampled))
            sampled = min(_ceil(axis * remainder), 2 * axis)
            steps.append(scale(sampled))
            remainder *= Fraction(axis, sampled)
        steps.append(crop(sampled))
        return tuple(steps)


def plan(width: int, height: int, output: ResizeDimensions, placement="stretch", sar="1:1") -> ReformatPlan:
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise GeometryError("No valid input raster.")
    dimensions(output.width, output.height)
    ratio = sample_aspect(sar)
    if placement == "stretch":
        pw, ph = Fraction(output.width), Fraction(output.height)
    elif placement in ("fit", "fill"):
        factors = (Fraction(output.width, width) / ratio, Fraction(output.height, height))
        factor = min(factors) if placement == "fit" else max(factors)
        pw, ph = width * ratio * factor, height * factor
    else:
        raise GeometryError("Choose Fit, Fill or Stretch.")
    return ReformatPlan(width, height, output, pw, ph,
                        (output.width - pw) / 2, (output.height - ph) / 2, placement, ratio)
