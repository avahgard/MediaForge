"""C1 source protection and destination planning; no output writes or publisher."""

import hashlib
from pathlib import Path
import stat

from mediaforge.features.dub.contracts import DubError, Reason, SourceIdentity
from mediaforge.features.media.export import ExportError
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.filesystem.profile import local_path


def identity(path, control):
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 64 * 1024**3:
        raise DubError(Reason.INVALID_SOURCE, 'Select a nonempty bounded ordinary media file.')
    digest = hashlib.sha256()
    with path.open('rb') as file:
        while block := file.read(1024 * 1024):
            control.check()
            digest.update(block)
    after = path.stat()
    values = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if values(before) != values(after):
        raise DubError(Reason.SOURCE_CHANGED, 'Source changed while hashing.')
    return SourceIdentity(path, *values(after), digest.hexdigest())


class ProtectedDubSource:
    """Lease is scoped to inspection; future Start must reacquire and compare it."""
    def __init__(self, source: Path, control):
        self.control = control
        self.locks = ReadLocks()
        try:
            self.path = local_path(source)
        except (ValueError, OSError) as error:
            raise DubError(Reason.UNSAFE_PATH, 'Select an unambiguous local file, without links/reparse paths.') from error

    def __enter__(self):
        try:
            self.control.check()
            self.locks.parents(self.path.parent)
            self.locks.add(self.path)
            self.identity = identity(self.path, self.control)
            return self
        except BaseException as error:
            self.close()
            if isinstance(error, ExportError):
                raise DubError(Reason.SOURCE_BUSY if error.code == 'source_busy' else Reason.UNSAFE_PATH,
                               'Source cannot be protected against writes/replacement.') from error
            if isinstance(error, OSError):
                raise DubError(Reason.INVALID_SOURCE, 'Source file is unavailable.') from error
            raise

    def close(self):
        try:
            self.locks.close()
        except ExportError as error:
            self.control.retain(self)
            raise DubError(Reason.LIFECYCLE_FAILED, 'Source handles remain owned; disable processing.',
                           retained_resources=(self,)) from error

    def __exit__(self, kind, error, traceback):
        try:
            if kind is None and identity(self.path, self.control) != self.identity:
                raise DubError(Reason.SOURCE_CHANGED, 'Source changed during inspection.')
        finally:
            self.close()


def destination_plan(source: SourceIdentity, route, folder: Path, *,
                     protected_sources: tuple[SourceIdentity, ...] = (), filename: str | None = None) -> Path:
    """Advisory pre-Start plan. C4 must lock/revalidate destination at publication."""
    try:
        folder = local_path(folder)
        if not folder.is_dir():
            raise ValueError('Select an existing output directory.')
        name = filename or (source.path.stem + '_RU' + route.suffix)
        if (Path(name).name != name or any(c in name for c in '<>:"/\\|?*')
                or name.endswith((' ', '.')) or Path(name).suffix.lower() != route.suffix.lower()
                or name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',
                    *(f'COM{i}' for i in range(1,10)), *(f'LPT{i}' for i in range(1,10))}):
            raise ValueError('Choose an unambiguous filename with the planned suffix.')
        path = local_path(folder / name)
        for item in (source, *protected_sources):
            if str(path).casefold() == str(item.path).casefold():
                raise DubError(Reason.SOURCE_ALIAS, 'Output must be distinct from every protected source.')
            if path.exists():
                info = path.stat()
                if (info.st_dev, info.st_ino) == (item.device, item.file_id):
                    raise DubError(Reason.SOURCE_ALIAS, 'Output aliases a protected source.')
        if path.exists():
            raise DubError(Reason.DESTINATION_EXISTS, 'Choose a new filename before Start; output is never overwritten.')
        return path
    except (ValueError, OSError) as error:
        raise DubError(Reason.UNSAFE_PATH, str(error)) from error
