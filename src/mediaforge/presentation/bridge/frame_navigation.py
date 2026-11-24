"""Stream eligibility only; neither a global ordinal nor adjacent-frame proof."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class FrameNavigation:
    rate: float
    count: int

    @classmethod
    def from_basis(cls, rate: float | None, count: int | None):
        if (rate is None or not math.isfinite(rate) or not 0 < rate <= 1000
                or type(count) is not int or count < 2):
            return None
        return cls(rate, count)
