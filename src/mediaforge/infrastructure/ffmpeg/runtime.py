"""Manifest-backed FFmpeg paths and verification; never downloads or searches PATH."""

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from mediaforge.infrastructure.filesystem.profile import ProfilePaths, portable_runtime
from mediaforge.infrastructure.resources.paths import configured_ffprobe_path


class RuntimeErrorCode(str, Enum):
    NOT_PROVISIONED = "not_provisioned"
    MANIFEST_INVALID = "manifest_invalid"
    CONFIGURATION_INVALID = "configuration_invalid"
    EXECUTABLE_MISSING = "executable_missing"
    LAUNCH_FAILED = "launch_failed"
    TIMEOUT = "timeout"
    VERSION_MISMATCH = "version_mismatch"
    HASH_MISMATCH = "hash_mismatch"
    EXECUTABLE_HASH_MISMATCH = "executable_hash_mismatch"
    INTEGRITY_UNAVAILABLE = "integrity_unavailable"
    ARCHIVE_INVALID = "archive_invalid"
    DOWNLOAD_FAILED = "download_failed"
    SETUP_FAILED = "setup_failed"


class FfmpegRuntimeError(RuntimeError):
    def __init__(self, code: RuntimeErrorCode, message: str):
        super().__init__(message)
        self.code = code


def manifest_path() -> Path:
    portable=getattr(sys,'mediaforge_portable_executable',None)
    if portable is not None:
        return portable_runtime(Path(portable)) / 'external/manifests/ffmpeg.json'
    if getattr(sys, 'frozen', False):
        from mediaforge.infrastructure.filesystem.profile import local_path
        return local_path(Path(sys.executable)).parent / 'external/manifests/ffmpeg.json'
    # Development checkout resource, deliberately independent of cwd.
    # Packaged external assets are a later task; absence fails closed.
    return Path(__file__).resolve().parents[4] / "external/manifests/ffmpeg.json"


@dataclass(frozen=True)
class FfmpegManifest:
    runtime_id: str
    provider: str
    variant: str
    platform: str
    architecture: str
    version: str
    source_commit: str
    artifact: str
    primary_url: str
    mirror_url: str
    checksum_url: str
    sha256: str
    ffmpeg_path: str
    ffprobe_path: str
    ffmpeg_sha256: str
    ffprobe_sha256: str
    banner_marker: str
    license: str
    provenance: str

    @classmethod
    def load(cls, path: Path | None = None) -> "FfmpegManifest":
        try:
            data = json.loads((path or manifest_path()).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
                raise ValueError("Unexpected or missing manifest fields")
            if any(not isinstance(v, str) or not v.strip() for v in data.values()):
                raise ValueError("Manifest values must be nonempty strings")
            manifest = cls(**data)
            manifest.validate()
            return manifest
        except (OSError, TypeError, ValueError) as error:
            raise FfmpegRuntimeError(
                RuntimeErrorCode.MANIFEST_INVALID, f"Invalid FFmpeg manifest: {error}"
            ) from error

    def validate(self) -> None:
        # Structure/trust domain checks; the reviewed manifest owns the actual pin.
        if not re.fullmatch(r"\d+\.\d+\.\d+", self.version):
            raise ValueError("Expected a fixed release version")
        for field in ("sha256", "ffmpeg_sha256", "ffprobe_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", getattr(self, field)):
                raise ValueError(f"Expected a literal SHA-256: {field}")
        if not re.fullmatch(r"[0-9a-f]{10,40}", self.source_commit):
            raise ValueError("Invalid source commit")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", self.runtime_id):
            raise ValueError("Invalid runtime ID")
        root = f"ffmpeg-{self.version}-essentials_build"
        primary = f"https://www.gyan.dev/ffmpeg/builds/packages/{root}.zip"
        expected = {
            "provider": "gyan.dev", "variant": "release-essentials",
            "platform": "windows", "architecture": "x64",
            "artifact": f"{root}.zip", "primary_url": primary,
            "mirror_url": (
                "https://github.com/GyanD/codexffmpeg/releases/download/"
                f"{self.version}/{root}.zip"
            ),
            "checksum_url": primary + ".sha256",
            "ffmpeg_path": f"{root}/bin/ffmpeg.exe",
            "ffprobe_path": f"{root}/bin/ffprobe.exe",
            "banner_marker": f"{self.version}-essentials_build-www.gyan.dev",
        }
        for name, value in expected.items():
            if getattr(self, name) != value:
                raise ValueError(f"Unexpected {name}")

    def location(self, profile: ProfilePaths) -> Path:
        if not profile.cache.is_absolute():
            raise FfmpegRuntimeError(
                RuntimeErrorCode.CONFIGURATION_INVALID, "Profile must be absolute"
            )
        return profile.cache / "ffmpeg" / f"{self.runtime_id}-{self.version}-{self.sha256}"


@dataclass(frozen=True)
class FfmpegRuntime:
    ffmpeg: Path
    ffprobe: Path


def check_managed_path(path: Path) -> None:
    """Reject links/junctions rather than following a managed path elsewhere."""
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise FfmpegRuntimeError(
                RuntimeErrorCode.CONFIGURATION_INVALID,
                f"Runtime path contains a link/reparse point: {item}",
            )


class FfmpegRuntimeResolver:
    def __init__(self, manifest: FfmpegManifest, *, runner=None, logger=None):
        self.manifest = manifest
        self._run = runner or subprocess.run
        self._logger = logger or logging.getLogger("mediaforge")

    def resolve(self, profile: ProfilePaths, *, ffprobe_override: Path | None = None):
        try:
            if ffprobe_override is not None:
                if not ffprobe_override.is_absolute() or ffprobe_override.name.lower() != "ffprobe.exe":
                    raise FfmpegRuntimeError(
                        RuntimeErrorCode.CONFIGURATION_INVALID,
                        "Explicit override must be an absolute ffprobe.exe path",
                    )
                self._logger.warning("Using explicit development FFprobe override: %s", ffprobe_override)
                paths = FfmpegRuntime(
                    ffprobe_override.with_name("ffmpeg.exe"), ffprobe_override
                )
            else:
                return self.verify_directory(self.manifest.location(profile))
            return self._verify(paths)
        except OSError as error:
            raise FfmpegRuntimeError(
                RuntimeErrorCode.CONFIGURATION_INVALID, str(error)
            ) from error

    def verify_directory(self, root: Path) -> FfmpegRuntime:
        try:
            check_managed_path(root)
            if not root.exists():
                raise FfmpegRuntimeError(
                    RuntimeErrorCode.NOT_PROVISIONED, f"FFmpeg runtime is not provisioned: {root}"
                )
            return self._verify(FfmpegRuntime(
                root / self.manifest.ffmpeg_path, root / self.manifest.ffprobe_path
            ), managed=True)
        except OSError as error:
            raise FfmpegRuntimeError(
                RuntimeErrorCode.CONFIGURATION_INVALID, str(error)
            ) from error

    def _verify(self, paths: FfmpegRuntime, *, managed: bool = False) -> FfmpegRuntime:
        for executable in (paths.ffmpeg, paths.ffprobe):
            check_managed_path(executable)
            if not executable.is_file():
                raise FfmpegRuntimeError(
                    RuntimeErrorCode.EXECUTABLE_MISSING, f"Missing executable: {executable}"
                )
        if managed:
            # Authenticate BOTH files before launching either one. These immutable
            # project pins were derived from the authenticated archive, not cache metadata.
            for executable, expected in (
                (paths.ffmpeg, self.manifest.ffmpeg_sha256),
                (paths.ffprobe, self.manifest.ffprobe_sha256),
            ):
                try:
                    with executable.open("rb") as stream:
                        actual = hashlib.file_digest(stream, "sha256").hexdigest()
                except OSError as error:
                    self._logger.exception("Cannot verify executable integrity: %s", executable)
                    raise FfmpegRuntimeError(
                        RuntimeErrorCode.INTEGRITY_UNAVAILABLE,
                        f"Cannot hash {executable}: {error}",
                    ) from error
                if actual != expected:
                    self._logger.error("Executable SHA-256 mismatch for %s: %s", executable, actual)
                    raise FfmpegRuntimeError(
                        RuntimeErrorCode.EXECUTABLE_HASH_MISMATCH,
                        f"Executable SHA-256 mismatch for {executable}: expected {expected}, got {actual}",
                    )
        for executable in (paths.ffmpeg, paths.ffprobe):
            try:
                result = self._run(
                    [str(executable), "-version"], shell=False,
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=5.0, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired as error:
                self._logger.exception("FFmpeg version verification timed out: %s", executable)
                raise FfmpegRuntimeError(RuntimeErrorCode.TIMEOUT, str(executable)) from error
            except (OSError, subprocess.SubprocessError) as error:
                self._logger.exception("FFmpeg version verification could not launch: %s", executable)
                raise FfmpegRuntimeError(RuntimeErrorCode.LAUNCH_FAILED, str(executable)) from error
            if result.returncode:
                self._logger.error("Version check failed for %s: %s", executable, result.stderr)
                raise FfmpegRuntimeError(RuntimeErrorCode.LAUNCH_FAILED, str(executable))
            words = result.stdout.splitlines()[0].split() if result.stdout.splitlines() else []
            if words[:3] != [executable.stem.lower(), "version", self.manifest.banner_marker]:
                self._logger.error("Rejected version banner for %s: %s", executable, result.stdout)
                raise FfmpegRuntimeError(RuntimeErrorCode.VERSION_MISMATCH, str(executable))
            self._logger.info("Verified %s: %s", executable, result.stdout.splitlines()[0])
        return paths


def resolve_ffmpeg_runtime(*, environment=None, logger=None, profile=None) -> FfmpegRuntime:
    """Composition-root entry point; an invalid override never falls back."""
    env = os.environ if environment is None else environment
    try:
        profile = profile or ProfilePaths.from_environment(env)
        override = configured_ffprobe_path(env)
        portable=getattr(sys,'mediaforge_portable_executable',None)
        if getattr(sys, 'frozen', False) or portable is not None:
            if override is not None:
                raise ValueError('Development runtime override is unavailable in packaged mode.')
            root = portable_runtime(Path(portable or sys.executable))
            return FfmpegRuntimeResolver(FfmpegManifest.load(), logger=logger).verify_directory(
                root / 'external/ffmpeg')
    except ValueError as error:
        raise FfmpegRuntimeError(RuntimeErrorCode.CONFIGURATION_INVALID, str(error)) from error
    return FfmpegRuntimeResolver(FfmpegManifest.load(), logger=logger).resolve(
        profile, ffprobe_override=override
    )
