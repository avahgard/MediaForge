"""Bounded read-only measurement for the portable Data display."""
from dataclasses import dataclass
from datetime import datetime
import shutil
import stat
from time import monotonic

from mediaforge.features.media.export import ExportError
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.filesystem.profile import local_path


@dataclass(frozen=True)
class Usage:
    state: str
    bytes: int = 0


@dataclass(frozen=True)
class StorageSnapshot:
    models: Usage
    cache: Usage
    available: Usage
    measured_at: datetime


def _measure(*roots):
    used, count, read_any, partial = 0, 0, False, False
    folders = list(roots)
    deadline = monotonic() + 4
    while folders:
        folder = folders.pop()
        try:
            local_path(folder)
            with ReadLocks() as locks:
                locks.parents(folder)
                for item in folder.iterdir():
                    count += 1
                    if count > 100000 or monotonic() >= deadline:
                        return Usage('partial' if read_any else 'unavailable', used)
                    try:
                        info = item.lstat()
                    except OSError:
                        partial = True
                        continue
                    if info.st_file_attributes & 0x400:
                        partial = True
                    elif stat.S_ISDIR(info.st_mode):
                        folders.append(item)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        used += info.st_size
                        read_any = True
                    else:
                        partial = True
                read_any = True
        except (OSError, ValueError, ExportError):
            partial = True
    return Usage('complete' if not partial else 'partial' if read_any else 'unavailable', used)


def measure_storage(paths):
    models = _measure(paths.models)
    cache = _measure(paths.downloads, paths.cache)
    try:
        available = Usage('complete', shutil.disk_usage(local_path(paths.root)).free)
    except (OSError, ValueError):
        available = Usage('unavailable')
    return StorageSnapshot(models, cache, available, datetime.now().astimezone())
