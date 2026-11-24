"""Explicit development provisioning for the one reviewed FFmpeg ZIP."""

import hashlib
import http.client
import logging
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import urllib.error
from urllib.parse import urlsplit
import urllib.request
import zipfile
import zlib

from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.ffmpeg.runtime import (
    FfmpegManifest, FfmpegRuntime, FfmpegRuntimeError, FfmpegRuntimeResolver,
    RuntimeErrorCode, check_managed_path,
)


# The authenticated 9.0.2 ZIP is 114,768,076 bytes. Bound staging before hashing.
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
ARTIFACT_HOSTS = {
    "www.gyan.dev": frozenset({"www.gyan.dev"}),
    "github.com": frozenset({"github.com", "release-assets.githubusercontent.com"}),
}


class _ArtifactRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_artifact_url(newurl, self.hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_artifact_url(url, hosts):
    try:
        parts = urlsplit(url)
        if (parts.scheme == "https" and parts.hostname in hosts
                and parts.port in (443, None) and parts.username is None
                and parts.password is None):
            return
    except ValueError:
        pass
    raise FfmpegRuntimeError(
        RuntimeErrorCode.DOWNLOAD_FAILED, "FFmpeg artifact endpoint is outside the authorized HTTPS sources",
    )


def _open_artifact(url, *, timeout):
    hosts = ARTIFACT_HOSTS.get(urlsplit(url).hostname, ())
    _check_artifact_url(url, hosts)
    return urllib.request.build_opener(_ArtifactRedirect(hosts)).open(url, timeout=timeout)


def verify_archive(archive: Path, manifest: FfmpegManifest) -> str:
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != manifest.sha256:
        raise FfmpegRuntimeError(
            RuntimeErrorCode.HASH_MISMATCH,
            f"Archive SHA-256 mismatch: expected {manifest.sha256}, got {digest}",
        )
    return digest


def _download(manifest: FfmpegManifest, target: Path, logger) -> None:
    failures = []
    for url in (manifest.primary_url, manifest.mirror_url):
        logger.info("Downloading pinned FFmpeg artifact: %s", url)
        try:
            response = _open_artifact(url, timeout=30)
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            failures.append(f"{url}: {error}")
            logger.warning("Artifact unavailable: %s", failures[-1])
            continue
        source_failed = False
        received = 0
        try:
            with response, target.open("wb") as stream:
                while True:
                    # Only a source read failure can authorize a mirror request.
                    try:
                        chunk = response.read(1024 * 1024)
                    except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
                        failures.append(f"{url}: {error}")
                        logger.warning("Artifact read failed: %s", failures[-1])
                        source_failed = True
                        break
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > MAX_ARCHIVE_BYTES:
                        raise FfmpegRuntimeError(
                            RuntimeErrorCode.DOWNLOAD_FAILED, "FFmpeg artifact exceeds the staging size limit",
                        )
                    stream.write(chunk)
        except OSError as error:
            raise FfmpegRuntimeError(
                RuntimeErrorCode.SETUP_FAILED, f"Cannot write archive {target}: {error}"
            ) from error
        if source_failed:
            continue
        # A completed download with wrong bytes is a hard failure, not a mirror retry.
        try:
            digest = verify_archive(target, manifest)
        except OSError as error:
            raise FfmpegRuntimeError(
                RuntimeErrorCode.SETUP_FAILED, f"Cannot read local archive {target}: {error}"
            ) from error
        logger.info("Locally verified archive SHA-256: %s", digest)
        return
    raise FfmpegRuntimeError(RuntimeErrorCode.DOWNLOAD_FAILED, "\n".join(failures))


def _extract(archive: Path, staging: Path, manifest: FfmpegManifest) -> None:
    expected = {manifest.ffmpeg_path, manifest.ffprobe_path}
    # Preserve upstream license/readme when present, without unpacking unrelated tools.
    root = PurePosixPath(manifest.ffmpeg_path).parts[0]
    allowed = expected | {f"{root}/LICENSE", f"{root}/README.txt"}
    try:
        with zipfile.ZipFile(archive) as package:
            seen = set()
            for info in package.infolist():
                # ZipInfo normalizes backslashes on Windows; inspect the raw name.
                name = info.orig_filename
                path = PurePosixPath(name)
                mode = info.external_attr >> 16
                if (
                    path.is_absolute() or ".." in path.parts or "\\" in name
                    or ":" in name or "\x00" in name
                    or any(p.endswith((".", " ")) for p in path.parts)
                    or stat.S_ISLNK(mode) or name.casefold() in seen
                ):
                    raise ValueError(f"Unsafe/duplicate archive member: {name!r}")
                seen.add(name.casefold())
            names = {item.filename for item in package.infolist() if not item.is_dir()}
            if not expected <= names:
                raise ValueError("Expected FFmpeg/FFprobe payload is missing")
            for name in sorted(allowed & names):
                info = package.getinfo(name)
                if info.file_size > 512 * 1024 * 1024:
                    raise ValueError("Runtime payload exceeds size bound")
                destination = staging / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with package.open(info) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output)
    except (ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError, EOFError, zlib.error) as error:
        raise FfmpegRuntimeError(RuntimeErrorCode.ARCHIVE_INVALID, str(error)) from error


def provision_ffmpeg(profile: ProfilePaths, *, manifest=None, runner=None, logger=None) -> FfmpegRuntime:
    manifest = manifest or FfmpegManifest.load()
    logger = logger or logging.getLogger("mediaforge")
    resolver = FfmpegRuntimeResolver(manifest, runner=runner, logger=logger)
    target = manifest.location(profile)
    try:
        check_managed_path(target)
        if target.exists():
            # Invalid existing installations are reported, never silently overwritten.
            return resolver.verify_directory(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".ffmpeg-staging-", dir=target.parent) as work:
            staging = Path(work)
            archive = staging / manifest.artifact
            _download(manifest, archive, logger)
            _extract(archive, staging, manifest)
            resolver.verify_directory(staging)
            # Windows rename does not replace an existing target; do not merge installs.
            if target.exists():
                raise FfmpegRuntimeError(RuntimeErrorCode.SETUP_FAILED, "Runtime target appeared during setup")
            staging.rename(target)
        logger.info("Published verified FFmpeg runtime: %s", target)
        return FfmpegRuntime(target / manifest.ffmpeg_path, target / manifest.ffprobe_path)
    except FfmpegRuntimeError:
        raise
    except (OSError, ValueError) as error:
        raise FfmpegRuntimeError(RuntimeErrorCode.SETUP_FAILED, str(error)) from error
