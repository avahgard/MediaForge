"""Resolve installed resources and explicit external-tool configuration."""

import os
from pathlib import Path
from typing import Mapping


def qml_root() -> Path:
    return Path(__file__).resolve().parents[2] / "presentation" / "qml" / "Main.qml"


def configured_ffprobe_path(
    environment: Mapping[str, str] | None = None,
) -> Path | None:
    """Return only the explicitly configured FFprobe path; never inspect PATH."""
    env = os.environ if environment is None else environment
    raw_path = env.get("MEDIAFORGE_FFPROBE_PATH", "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        raise ValueError("MEDIAFORGE_FFPROBE_PATH must be an absolute path")
    return path
