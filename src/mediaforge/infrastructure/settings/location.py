"""Reserved application preferences location; no feature settings or I/O."""

from pathlib import Path

from mediaforge.infrastructure.filesystem.profile import ProfilePaths


def settings_file(profile: ProfilePaths) -> Path:
    return profile.config / "settings.json"
