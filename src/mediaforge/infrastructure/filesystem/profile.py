"""Executable-relative portable storage; explicit isolated development roots only."""

from dataclasses import dataclass
import ctypes
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Mapping
from uuid import uuid4


class ProfileUnavailableError(ValueError):
    code = "portable_data_unavailable"


def local_path(path: Path) -> Path:
    """Validate before resolving: resolving first would hide junctions/aliases."""
    path = Path(path)
    if (not path.is_absolute() or str(path).startswith(('\\\\', '//'))
            or '..' in path.parts or len(str(path)) > 30000 or len(path.parts) > 128
            or any(p.endswith((' ', '.')) or ':' in p for p in path.parts[1:])):
        raise ValueError('Use an absolute, unambiguous local path.')
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError(f'Reparse/link paths are unsupported: {part}')
    resolved = path.resolve()
    if str(resolved).startswith(('\\\\', '//')):
        raise ValueError('Resolved network/device paths are unsupported.')
    if os.name == 'nt':
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        api.GetDriveTypeW.restype = ctypes.c_uint
        if api.GetDriveTypeW(resolved.anchor) not in (2, 3):
            raise ValueError('Select a writable local removable or fixed disk.')
    return resolved


def portable_runtime(executable: Path) -> Path:
    """Executable-relative runtime selected by the authenticated launcher."""
    return local_path(getattr(sys, 'mediaforge_portable_runtime', executable.parent))


@dataclass(frozen=True)
class ProfilePaths:
    root: Path

    def __post_init__(self):
        object.__setattr__(self, 'root', local_path(self.root))

    @classmethod
    def from_executable(cls, executable: Path):
        executable = local_path(executable)
        if not executable.is_file() or executable.stat().st_nlink != 1:
            raise ValueError('The deployed executable must have one unambiguous file identity.')
        return cls(executable.parent / 'Data' / 'v2')

    @classmethod
    def for_test(cls, root: Path):
        """An explicit root, never inferred from user profile variables or cwd."""
        return cls(root)

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None):
        portable=getattr(sys,'mediaforge_portable_executable',None)
        if portable is not None:
            return cls.from_executable(Path(portable))
        if getattr(sys, 'frozen', False):
            return cls.from_executable(Path(sys.executable))
        env = os.environ if environment is None else environment
        value = env.get('MEDIAFORGE_DATA_ROOT')
        if not value:
            raise ValueError('Development requires an explicit absolute MEDIAFORGE_DATA_ROOT; '
                             'extract MediaForge to a writable local folder for portable use.')
        return cls.for_test(Path(value))

    def prepare(self) -> None:
        """Refuse unwritable storage; exercise create/write/rename/delete, no fallback."""
        from mediaforge.features.media.export import ExportError
        from mediaforge.infrastructure.filesystem.image_output import ReadLocks
        try:
            local_path(self.root)
            with ReadLocks() as locks:
                for folder in (self.root, self.config, self.data, self.cache, self.logs,
                               self.models, self.manifests, self.downloads, self.sessions, self.temporary):
                    local_path(folder)
                    # Hold each ancestor before constructing/using its child;
                    # a junction/rename race cannot redirect the write probe.
                    for part in reversed((folder, *folder.parents)):
                        if not part.exists():
                            part.mkdir()
                        locks.add(part, directory=True)
                    probe = folder / ('.write-check-' + uuid4().hex)
                    renamed = probe.with_suffix('.renamed')
                    try:
                        with probe.open('xb') as file:
                            file.write(b'MediaForge portable root check')
                            file.flush()
                        probe.rename(renamed)
                        renamed.unlink()
                    finally:
                        probe.unlink(missing_ok=True)
                        renamed.unlink(missing_ok=True)
        except (OSError, ValueError, ExportError) as error:
            raise ProfileUnavailableError('Extract MediaForge to a writable local folder. '
                                          f'Portable data root is unavailable: {self.root}') from error
        from mediaforge.infrastructure.logging.protected import ProtectedLogs
        try:
            with ProtectedLogs(self.logs, create=False):
                pass
        except (OSError, ValueError, ExportError) as error:
            raise ProfileUnavailableError('Check that the portable application logs are accessible, '
                                          f'ordinary files and close any program locking them: {self.logs}') from error

    def child_environment(self, environment: Mapping[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ if environment is None else environment)
        env.pop('QML_FORCE_DISK_CACHE',None)
        env.update(TEMP=str(self.temporary), TMP=str(self.temporary),
                   TMPDIR=str(self.temporary), QML_DISABLE_DISK_CACHE='1',
                   QML_DISK_CACHE_PATH=str(self.cache/'qml'),
                   QT_DISABLE_SHADER_DISK_CACHE='1', QSG_RHI_DISABLE_SHADER_DISK_CACHE='1',
                   QSG_RHI_PIPELINE_CACHE_SAVE='',QSG_RHI_PIPELINE_CACHE_LOAD='',
                   QT_SHADER_CACHE_PATH=str(self.cache/'shaders'))
        return env

    def activate_temporary(self) -> None:
        """Bootstrap calls once, before Qt/owned workers can create ordinary temp."""
        self.prepare()
        os.environ.update(self.child_environment({}))
        tempfile.tempdir = str(self.temporary)

    @property
    def temporary(self) -> Path:
        return self.root / 'tmp'

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def manifests(self) -> Path:
        return self.root / 'manifests'

    @property
    def downloads(self) -> Path:
        return self.root / 'downloads'

    @property
    def sessions(self) -> Path:
        return self.root / 'sessions'
