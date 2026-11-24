"""Narrow first-video-stream duration extraction shared by Crop and Resize."""

import math
import re


def positive_duration(value) -> float:
    if isinstance(value, bool):
        raise ValueError("Invalid video duration")
    duration = float(value)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid video duration")
    return duration


def same_video_duration_tag(video: dict) -> float:
    tags = video.get("tags", {})
    if not isinstance(tags, dict):
        raise ValueError("Invalid video tags")
    value = next((item for key, item in tags.items() if key.upper() == "DURATION"), None)
    if not isinstance(value, str) or not re.fullmatch(r"\d+:[0-5]\d:[0-5]\d(?:\.\d+)?", value):
        raise ValueError("Missing video duration")
    hours, minutes, seconds = value.split(":")
    return positive_duration(int(hours) * 3600 + int(minutes) * 60 + float(seconds))


def first_video_duration(video: dict) -> float:
    """Resize policy: valid primary, otherwise the same-video tag fallback."""
    try:
        return positive_duration(video.get("duration"))
    except (TypeError, ValueError, OverflowError):
        return same_video_duration_tag(video)
