"""Exact libx264 inventory check shared by Crop and Resize."""

import re


def has_libx264(inventory: str) -> bool:
    return any(re.fullmatch(r"\s*V[A-Z.]{5}\s+libx264(?:\s+.*)?", line)
               for line in inventory.splitlines())
