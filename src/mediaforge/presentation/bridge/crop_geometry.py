"""Display-to-coded geometry for the interactive crop overlay."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int


def even(value: float) -> int:
    return round(value / 2) * 2


def clamp_even(value: float, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum // 2 * 2, even(value)))


def pointer_to_coded(px: float, py: float, content_x: float, content_y: float,
                     content_width: float, content_height: float,
                     source_width: int, source_height: int) -> tuple[float, float] | None:
    if (content_width <= 0 or content_height <= 0
            or px < content_x or py < content_y
            or px > content_x + content_width or py > content_y + content_height):
        return None
    return ((px - content_x) / content_width * source_width,
            (py - content_y) / content_height * source_height)


def drag_rect(start: Rect, mode: str, delta_x: float, delta_y: float,
              source_width: int, source_height: int) -> Rect:
    """Snap pointer movement to legal even geometry, anchoring opposite edges."""
    max_x = source_width // 2 * 2
    max_y = source_height // 2 * 2
    if mode == "move":
        return Rect(clamp_even(start.x + delta_x, 0, max_x - start.width),
                    clamp_even(start.y + delta_y, 0, max_y - start.height),
                    start.width, start.height)
    left, top = start.x, start.y
    right, bottom = start.x + start.width, start.y + start.height
    if "left" in mode:
        left = clamp_even(left + delta_x, 0, right - 16)
    if "right" in mode:
        right = clamp_even(right + delta_x, left + 16, max_x)
    if "top" in mode:
        top = clamp_even(top + delta_y, 0, bottom - 16)
    if "bottom" in mode:
        bottom = clamp_even(bottom + delta_y, top + 16, max_y)
    return Rect(left, top, right - left, bottom - top)
