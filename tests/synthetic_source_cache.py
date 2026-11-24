"""Opt-in test-only reuse of long60/practical. Never return a cache source.

Two live slots; abandoned staging and quarantined invalid entries are evidence,
not hits. No retention/deletion policy is implemented here. Kernel file locks
serialize each slot and release even if the owning process dies.
"""

from contextlib import contextmanager
import errno
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import time
import uuid
import warnings


SOURCES = {"long60.mp4": (1024, 768, 60, 180), "practical.mp4": (1280, 720, 25, 600)}
ENVIRONMENT = "MEDIAFORGE_TEST_SOURCE_CACHE"


class SourceCacheError(RuntimeError):
    pass


def cache_from_environment():
    value = os.environ.get(ENVIRONMENT)
    if value is None:
        return None
    path = Path(value)
    if not value.strip() or not path.is_absolute():
        raise SourceCacheError(f"{ENVIRONMENT} must be an absolute cache directory")
    return path


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _plain_path(path):
    # Reject junctions/symlinks, including ancestors; never traverse unknown links.
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise SourceCacheError(f"Link/reparse point in fixture path: {item}")


def authenticated_hashes(runtime):
    from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest, check_managed_path

    manifest = FfmpegManifest.load()
    result = {}
    for name in ("ffmpeg", "ffprobe"):
        path = getattr(runtime, name)
        check_managed_path(path)
        actual = sha256(path)
        if actual != getattr(manifest, name + "_sha256"):
            raise SourceCacheError(f"Managed {name} executable SHA-256 mismatch: {path}")
        result[name + "_sha256"] = actual
    return result


@contextmanager
def _slot_lock(path, timeout):
    _plain_path(path)
    with path.open("a+b") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise SourceCacheError(f"Fixture cache lock must be a regular file: {path}")
        if info.st_nlink != 1:
            raise SourceCacheError(f"Fixture cache lock must have exactly one link (st_nlink={info.st_nlink}): {path}")
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise SourceCacheError(f"Cannot lock fixture cache: {path}: {error}") from error
                if time.monotonic() >= deadline:
                    raise SourceCacheError(f"Timed out waiting for fixture cache lock: {path}") from error
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def semantic_properties(path, runtime, name):
    result = subprocess.run([str(runtime.ffprobe), "-v", "error", "-show_streams",
                             "-show_format", "-of", "json", str(path)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=30)
    if result.returncode:
        raise SourceCacheError(f"FFprobe failed ({result.returncode}) for {path}: {result.stderr}")
    try:
        data = json.loads(result.stdout)
        streams = data["streams"]
        if len(streams) != 2:
            raise ValueError("expected exactly one video and one audio stream")
        video, audio = streams
        width, height, rate, duration = SOURCES[name]
        exact = {"codec_type": "video", "codec_name": "h264", "width": width,
                 "height": height, "pix_fmt": "yuv420p", "sample_aspect_ratio": "1:1",
                 "has_b_frames": 2, "nb_frames": str(rate * duration)}
        for field, expected in exact.items():
            if video[field] != expected:
                raise ValueError(f"video {field}: expected {expected}, got {video[field]}")
        for field in ("r_frame_rate", "avg_frame_rate"):
            if Fraction(video[field]) != rate:
                raise ValueError(f"video {field}: expected {rate}, got {video[field]}")
        audio_exact = {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100", "channels": 1}
        for field, expected in audio_exact.items():
            if audio[field] != expected:
                raise ValueError(f"audio {field}: expected {expected}, got {audio[field]}")
        for label, values in (("video", video), ("audio", audio), ("format", data["format"])):
            if Fraction(values["start_time"]) != 0 or abs(Fraction(values["duration"]) - duration) > Fraction(1, 10):
                raise ValueError(f"{label} start/duration differs from recipe")
        if "mp4" not in data["format"]["format_name"].split(","):
            raise ValueError("expected MP4 container")
        return {"video": {field: video[field] for field in (*exact, "r_frame_rate", "avg_frame_rate", "start_time", "duration", "time_base")},
                "audio": {field: audio[field] for field in (*audio_exact, "start_time", "duration")},
                "format": {field: data["format"][field] for field in ("format_name", "start_time", "duration")}}
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as error:
        raise SourceCacheError(f"Invalid semantic media properties for {name} at {path}: {error}") from error


class SyntheticSourceCache:
    def __init__(self, directory, runtime, *, lock_timeout=700):
        self.directory = Path(directory)
        self.runtime = runtime
        self.lock_timeout = lock_timeout

    def _identity(self, name, command, timeout):
        return {"schema": 1, "recipe": name, "parameters": command[1:-1],
                "generation_timeout": timeout,
                "implementation": {p.name: sha256(p) for p in (
                    Path(__file__), Path(__file__).with_name("native_sources.py"))},
                "runtime": authenticated_hashes(self.runtime)}

    def _validate(self, entry, name, identity):
        _plain_path(entry)
        media, metadata = entry / name, entry / "manifest.json"
        _plain_path(media)
        _plain_path(metadata)
        manifest = json.loads(metadata.read_text(encoding="utf-8"))
        if manifest["identity"] != identity:
            raise SourceCacheError("recipe/implementation/runtime identity changed")
        if not media.is_file() or media.stat().st_nlink != 1:
            raise SourceCacheError("cache media must be a regular file with no hardlinks")
        if media.stat().st_mode & stat.S_IWRITE or metadata.stat().st_mode & stat.S_IWRITE:
            raise SourceCacheError("cache source and manifest must be read-only")
        if media.stat().st_size != manifest["bytes"] or sha256(media) != manifest["sha256"]:
            raise SourceCacheError("content size/SHA-256 mismatch")
        properties = semantic_properties(media, self.runtime, name)
        if properties != manifest["semantic"]:
            raise SourceCacheError("semantic manifest mismatch")
        return manifest

    def materialize(self, name, destination, command, timeout):
        if name not in SOURCES:
            raise SourceCacheError(f"Fixture reuse is not allowed for {name}")
        destination = Path(destination)
        if not self.directory.is_absolute() or not destination.is_absolute():
            raise SourceCacheError("Cache and run paths must be absolute")
        _plain_path(self.directory)
        _plain_path(destination)
        cache = self.directory.resolve()
        if cache == destination.resolve() or cache in destination.resolve().parents:
            raise SourceCacheError("Run destination must be outside the fixture cache")
        if destination.exists():
            raise SourceCacheError(f"Refuse to overwrite existing run source: {destination}")
        self.directory.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        diagnostics = []
        entry = self.directory / (name + ".entry")
        try:
            with _slot_lock(self.directory / (name + ".lock"), self.lock_timeout):
                identity = self._identity(name, command, timeout)
                mode, encoded_bytes, encode_seconds = "warm", 0, 0.0
                # Abandoned staging is never selected, replaced or erased.
                if list(self.directory.glob(name + ".partial-*")):
                    diagnostics.append("abandoned incomplete publication ignored; evidence retained")
                manifest = None
                if entry.exists():
                    try:
                        manifest = self._validate(entry, name, identity)
                    except (OSError, ValueError, KeyError, TypeError, SourceCacheError) as error:
                        _plain_path(entry)
                        quarantine = self.directory / (name + ".invalid-" + uuid.uuid4().hex)
                        entry.rename(quarantine)
                        diagnostics.append(f"invalid entry: {error}; preserved at {quarantine}")
                if manifest is None:
                    mode = "cold"
                    stage = self.directory / (name + ".partial-" + uuid.uuid4().hex)
                    stage.mkdir()
                    media = stage / name
                    generation_started = time.perf_counter()
                    result = subprocess.run([*command[:-1], str(media)], capture_output=True,
                                            text=True, timeout=timeout)
                    encode_seconds = time.perf_counter() - generation_started
                    if result.returncode:
                        raise SourceCacheError(f"Generation failed ({result.returncode}); retained {stage}: {result.stderr}")
                    properties = semantic_properties(media, self.runtime, name)
                    encoded_bytes = media.stat().st_size
                    manifest = {"identity": identity, "bytes": encoded_bytes, "sha256": sha256(media), "semantic": properties}
                    with media.open("r+b") as stream:
                        os.fsync(stream.fileno())
                    with (stage / "manifest.json").open("x", encoding="utf-8") as stream:
                        json.dump(manifest, stream, indent=2)
                        stream.flush()
                        os.fsync(stream.fileno())
                    # Read-only sources plus copyfile (never hardlinks/copy2).
                    media.chmod(stat.S_IREAD)
                    (stage / "manifest.json").chmod(stat.S_IREAD)
                    stage.rename(entry)  # Same filesystem, complete directory publication.
                source = entry / name
                copy_started = time.perf_counter()
                # Exclusive destination creation; keep partial run copies on failure.
                with source.open("rb") as reader, destination.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
                if destination.stat().st_nlink != 1 or destination.stat().st_size != manifest["bytes"] or sha256(destination) != manifest["sha256"]:
                    raise SourceCacheError(f"Run copy integrity failed: {destination}")
                copy_seconds = time.perf_counter() - copy_started
                record = {"recipe": name, "mode": mode, "seconds": time.perf_counter() - started,
                          "encode_seconds": encode_seconds, "copy_seconds": copy_seconds,
                          "encoded_bytes": encoded_bytes, "copied_bytes": manifest["bytes"],
                          "sha256": manifest["sha256"], "identity": identity, "diagnostics": diagnostics}
                for diagnostic in diagnostics:
                    warnings.warn(f"Synthetic fixture cache {name}: {diagnostic}", RuntimeWarning)
                return record
        except Exception as error:
            raise SourceCacheError(f"Synthetic fixture cache {name} failed at {entry}: {error}") from error
