"""Bounded reuse contracts; tiny sentinels avoid native fixture generation here."""

import json
import multiprocessing
import os
from pathlib import Path
import stat
import subprocess
import time
from types import SimpleNamespace

import pytest

from tests import synthetic_source_cache as cache
from tests.native_sources import NativeSources


def fake_properties(path, runtime, name):
    return {"source": name, "valid": True}


def fake_hashes(runtime):
    return {"ffmpeg_sha256": cache.sha256(runtime.ffmpeg),
            "ffprobe_sha256": cache.sha256(runtime.ffprobe)}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "cache Кириллица"
    run = tmp_path / "fresh writable run"
    run.mkdir()
    ffmpeg, ffprobe = tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe"
    ffmpeg.write_bytes(b"managed ffmpeg")
    ffprobe.write_bytes(b"managed ffprobe")
    runtime = SimpleNamespace(ffmpeg=ffmpeg, ffprobe=ffprobe)
    generated = []

    def generate(command, **options):
        destination = Path(command[-1])
        generated.append(destination)
        destination.write_bytes((destination.name + str(len(generated))).encode())
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(cache, "authenticated_hashes", fake_hashes)
    monkeypatch.setattr(cache, "semantic_properties", fake_properties)
    monkeypatch.setattr(cache.subprocess, "run", generate)
    return SimpleNamespace(root=root, run=run, runtime=runtime, generated=generated)


def materialize(setup, name="long60.mp4", *, run_name=None):
    directory = setup.run if run_name is None else setup.run.parent / run_name
    directory.mkdir(exist_ok=True)
    sources = NativeSources(directory, setup.runtime, cache_directory=setup.root)
    sources.require(name)
    sources.write_generation()
    return sources


@pytest.mark.parametrize("name", cache.SOURCES)
def test_cold_then_warm_with_independent_writable_copies(setup, name):
    cold = materialize(setup, name)
    warm = materialize(setup, name, run_name="next run Кириллица")
    assert len(setup.generated) == 1
    assert cold.reuse[0]["mode"] == "cold" and cold.reuse[0]["encoded_bytes"] > 0
    assert warm.reuse[0]["mode"] == "warm" and warm.reuse[0]["encoded_bytes"] == 0
    source = setup.root / (name + ".entry") / name
    original = source.read_bytes()
    assert not source.stat().st_mode & stat.S_IWRITE
    for path in (cold.directory / name, warm.directory / name, source):
        assert path.stat().st_nlink == 1
    assert not os.path.samefile(source, warm.directory / name)
    (cold.directory / name).write_bytes(b"changed mutable source")
    assert source.read_bytes() == original == (warm.directory / name).read_bytes()
    report = json.loads((warm.directory / "native-source-generation.json").read_text())
    assert report["encoded_bytes"] == 0 and report["generated_bytes"] == len(original)


@pytest.mark.parametrize("field", ["recipe", "implementation", "ffmpeg", "ffprobe"])
def test_identity_invalidation_preserves_old_entry(setup, monkeypatch, field):
    first = materialize(setup)
    if field in ("ffmpeg", "ffprobe"):
        getattr(setup.runtime, field).write_bytes(b"different authenticated runtime sentinel")
    elif field == "recipe":
        original = NativeSources._base_command

        def changed(self, *args):
            command = original(self, *args)
            command[command.index("-g") + 1] = "251"
            return command

        monkeypatch.setattr(NativeSources, "_base_command", changed)
    else:
        original = cache.sha256
        monkeypatch.setattr(cache, "sha256", lambda path:
                            "new implementation" if path.name == "native_sources.py" else original(path))
    with pytest.warns(RuntimeWarning, match="identity changed"):
        next_run = materialize(setup, run_name="identity changed run")
    assert next_run.reuse[0]["mode"] == "cold" and len(setup.generated) == 2
    assert next_run.reuse[0]["identity"] != first.reuse[0]["identity"]
    assert len(list(setup.root.glob("*.invalid-*"))) == 1


@pytest.mark.parametrize("damage", ["bytes", "size", "manifest", "missing_manifest", "missing_media", "semantic"])
def test_invalid_entry_is_never_trusted(setup, monkeypatch, damage):
    materialize(setup)
    entry = setup.root / "long60.mp4.entry"
    media, manifest = entry / "long60.mp4", entry / "manifest.json"
    media.chmod(stat.S_IREAD | stat.S_IWRITE)
    manifest.chmod(stat.S_IREAD | stat.S_IWRITE)
    if damage == "bytes":
        media.write_bytes(b"x" * media.stat().st_size)
    elif damage == "size":
        media.write_bytes(b"truncated")
    elif damage == "manifest":
        manifest.write_text("{incomplete")
    elif damage == "missing_manifest":
        manifest.unlink()
    elif damage == "missing_media":
        media.unlink()
    else:
        metadata = json.loads(manifest.read_text())
        metadata["semantic"] = {"false": "historical PASS"}
        manifest.write_text(json.dumps(metadata))
    for path in (media, manifest):
        if path.exists(): path.chmod(stat.S_IREAD)
    with pytest.warns(RuntimeWarning, match="invalid entry"):
        next_run = materialize(setup, run_name="recovered run")
    assert next_run.reuse[0]["mode"] == "cold" and len(setup.generated) == 2
    assert len(list(setup.root.glob("*.invalid-*"))) == 1


def test_failed_generation_retained_and_fresh_store_recovers(setup, monkeypatch):
    good = cache.subprocess.run

    def fail(*args, **options):
        good(*args, **options)
        return SimpleNamespace(returncode=1, stderr="explicit failure")

    monkeypatch.setattr(cache.subprocess, "run", fail)
    sources = NativeSources(setup.run, setup.runtime, cache_directory=setup.root)
    for _ in range(2):
        with pytest.raises(cache.SourceCacheError, match="explicit failure"):
            sources.require("long60.mp4")
    assert len(setup.generated) == 1 and not (setup.root / "long60.mp4.entry").exists()
    assert not (setup.run / "long60.mp4").exists()
    monkeypatch.setattr(cache.subprocess, "run", good)
    with pytest.warns(RuntimeWarning, match="incomplete publication ignored"):
        recovered = materialize(setup)
    assert recovered.reuse[0]["mode"] == "cold"
    assert len(list(setup.root.glob("*.partial-*"))) == 1


def test_interrupted_atomic_publication_never_becomes_hit(setup, monkeypatch):
    rename = Path.rename

    def interrupt(path, target):
        if path.name.startswith("long60.mp4.partial-"):
            raise OSError("publication interrupted")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", interrupt)
    with pytest.raises(cache.SourceCacheError, match="publication interrupted"):
        materialize(setup)
    assert not (setup.root / "long60.mp4.entry").exists()
    assert not (setup.run / "long60.mp4").exists()
    monkeypatch.setattr(Path, "rename", rename)
    with pytest.warns(RuntimeWarning, match="incomplete publication"):
        materialize(setup)
    assert len(setup.generated) == 2


def test_valid_filename_in_run_never_establishes_cache_hit(setup):
    (setup.run / "long60.mp4").write_bytes(b"existing file")
    with pytest.raises(cache.SourceCacheError, match="overwrite existing"):
        materialize(setup)
    assert not setup.generated and not setup.root.exists()


def test_default_fresh_path_and_other_recipes_do_not_allocate_cache(setup):
    NativeSources(setup.run, setup.runtime).require("long60.mp4")
    NativeSources(setup.run, setup.runtime, cache_directory=setup.root).require("integral.mp4")
    assert len(setup.generated) == 2 and not setup.root.exists()


def test_lazy_small_requests_do_not_hash_runtime_or_allocate_cache(setup, monkeypatch):
    monkeypatch.setattr(cache, "authenticated_hashes", lambda *_: pytest.fail("eager runtime validation"))
    sources = NativeSources(setup.run, setup.runtime, cache_directory=setup.root)
    sources.for_mode("icons")
    assert [p.name for p in setup.generated] == ["integral.mp4"]
    assert not setup.root.exists()


@pytest.mark.parametrize("value", ["", "relative", " "])
def test_invalid_opt_in_is_explicit(monkeypatch, value):
    monkeypatch.setenv(cache.ENVIRONMENT, value)
    with pytest.raises(cache.SourceCacheError, match="absolute cache directory"):
        cache.cache_from_environment()


def test_opt_in_and_unset(monkeypatch, tmp_path):
    monkeypatch.delenv(cache.ENVIRONMENT, raising=False)
    assert cache.cache_from_environment() is None
    monkeypatch.setenv(cache.ENVIRONMENT, str(tmp_path / "Кириллица cache"))
    assert cache.cache_from_environment() == tmp_path / "Кириллица cache"


def test_reject_cache_destination_and_unapproved_recipe(setup):
    store = cache.SyntheticSourceCache(setup.root, setup.runtime)
    with pytest.raises(cache.SourceCacheError, match="not allowed"):
        store.materialize("integral.mp4", setup.run / "integral.mp4", [], 1)
    with pytest.raises(cache.SourceCacheError, match="outside"):
        store.materialize("long60.mp4", setup.root / "out.mp4", [], 1)
    assert not setup.root.exists()


def test_unverified_runtime_fails_before_launch(tmp_path, monkeypatch):
    runtime = SimpleNamespace(ffmpeg=tmp_path / "ffmpeg.exe", ffprobe=tmp_path / "ffprobe.exe")
    for field in ("ffmpeg", "ffprobe"):
        getattr(runtime, field).write_bytes(b"not authenticated")
    monkeypatch.setattr(cache.subprocess, "run", lambda *_a, **_kw: pytest.fail("unauthenticated launch"))
    with pytest.raises(cache.SourceCacheError, match="Managed ffmpeg executable SHA-256 mismatch"):
        cache.authenticated_hashes(runtime)


def test_both_executable_bytes_are_authenticated_each_time(tmp_path, monkeypatch):
    from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest

    runtime = SimpleNamespace(ffmpeg=tmp_path / "ffmpeg.exe", ffprobe=tmp_path / "ffprobe.exe")
    runtime.ffmpeg.write_bytes(b"ffmpeg sentinel")
    runtime.ffprobe.write_bytes(b"ffprobe sentinel")
    expected = fake_hashes(runtime)
    monkeypatch.setattr(FfmpegManifest, "load", lambda: SimpleNamespace(**expected))
    assert cache.authenticated_hashes(runtime) == expected
    runtime.ffprobe.write_bytes(b"tampered ffprobe")
    with pytest.raises(cache.SourceCacheError, match="Managed ffprobe executable SHA-256 mismatch"):
        cache.authenticated_hashes(runtime)


@pytest.mark.native
def test_hardlinked_cache_source_is_rejected(setup):
    materialize(setup)
    source = setup.root / "long60.mp4.entry" / "long60.mp4"
    os.link(source, setup.run / "external hardlink.mp4")
    with pytest.warns(RuntimeWarning, match="no hardlinks"):
        recovered = materialize(setup, run_name="hardlink rejection run")
    assert recovered.reuse[0]["mode"] == "cold"
    assert (setup.run / "external hardlink.mp4").read_bytes() != (recovered.directory / "long60.mp4").read_bytes()


@pytest.mark.native
@pytest.mark.parametrize("name", cache.SOURCES)
@pytest.mark.parametrize("contents", [b"", b"preserve external lock target"])
def test_hardlinked_lock_rejected_before_write_or_lock(tmp_path, monkeypatch, name, contents):
    # C22-F01: the Reviewer's empty external source and absent executable.
    root, run = tmp_path / "cache Кириллица", tmp_path / "fresh run"
    root.mkdir()
    run.mkdir()
    target = tmp_path / "external source.mp4"
    target.write_bytes(contents)
    lock = root / (name + ".lock")
    os.link(target, lock)
    before = target.stat()
    assert os.path.samefile(target, lock) and before.st_nlink == 2
    operations = []
    if os.name == "nt":
        import msvcrt
        monkeypatch.setattr(msvcrt, "locking", lambda *args: operations.append(args))
    else:
        import fcntl
        monkeypatch.setattr(fcntl, "flock", lambda *args: operations.append(args))
    runtime = SimpleNamespace(ffmpeg=tmp_path / "absent ffmpeg.exe",
                              ffprobe=tmp_path / "absent ffprobe.exe")
    destination = run / name
    with pytest.raises(cache.SourceCacheError, match="lock.*st_nlink=2"):
        cache.SyntheticSourceCache(root, runtime).materialize(
            name, destination, [str(runtime.ffmpeg), str(destination)], 150)
    after = target.stat()
    assert target.read_bytes() == lock.read_bytes() == contents
    for field in ("st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_ino", "st_dev", "st_nlink"):
        assert getattr(after, field) == getattr(before, field), field
    assert not operations
    assert list(root.iterdir()) == [lock] and not destination.exists()
    # The rejected opened handle is closed, without retiring either alias.
    with lock.open("r+b") as stream:
        assert os.fstat(stream.fileno()).st_nlink == 2


def test_nonregular_opened_lock_handle_rejected_before_initialization(tmp_path, monkeypatch):
    path = tmp_path / "fixture.lock"
    path.write_bytes(b"")
    descriptors = []

    def nonregular(descriptor):
        descriptors.append(descriptor)
        return SimpleNamespace(st_mode=stat.S_IFIFO, st_nlink=1)

    monkeypatch.setattr(cache.os, "fstat", nonregular)
    monkeypatch.setattr(cache.time, "monotonic", lambda: pytest.fail("lock acquisition admitted"))
    with pytest.raises(cache.SourceCacheError, match="lock.*regular file"):
        with cache._slot_lock(path, .1):
            pytest.fail("nonregular lock admitted")
    assert len(descriptors) == 1 and path.read_bytes() == b""


def test_semantically_bad_media_with_matching_hash_is_rejected(setup, monkeypatch):
    materialize(setup)
    source = setup.root / "long60.mp4.entry" / "long60.mp4"
    manifest = source.with_name("manifest.json")
    source.chmod(stat.S_IREAD | stat.S_IWRITE)
    manifest.chmod(stat.S_IREAD | stat.S_IWRITE)
    source.write_bytes(b"bad media with updated content identity")
    metadata = json.loads(manifest.read_text())
    metadata.update(bytes=source.stat().st_size, sha256=cache.sha256(source))
    manifest.write_text(json.dumps(metadata))
    source.chmod(stat.S_IREAD)
    manifest.chmod(stat.S_IREAD)

    def validate(path, runtime, name):
        if path.read_bytes().startswith(b"bad media"):
            raise cache.SourceCacheError("invalid media despite matching content manifest")
        return fake_properties(path, runtime, name)

    monkeypatch.setattr(cache, "semantic_properties", validate)
    with pytest.warns(RuntimeWarning, match="despite matching content"):
        recovered = materialize(setup, run_name="semantic rejection run")
    assert recovered.reuse[0]["mode"] == "cold"


def test_writable_cache_entry_is_not_reused(setup):
    materialize(setup)
    (setup.root / "long60.mp4.entry" / "long60.mp4").chmod(stat.S_IREAD | stat.S_IWRITE)
    with pytest.warns(RuntimeWarning, match="must be read-only"):
        recovered = materialize(setup, run_name="read-only recovery run")
    assert recovered.reuse[0]["mode"] == "cold"


def probe_json(name):
    width, height, rate, duration = cache.SOURCES[name]
    return {"streams": [
        {"codec_type": "video", "codec_name": "h264", "width": width, "height": height,
         "pix_fmt": "yuv420p", "sample_aspect_ratio": "1:1", "has_b_frames": 2,
         "nb_frames": str(rate * duration), "r_frame_rate": f"{rate}/1", "avg_frame_rate": f"{rate}/1",
         "start_time": "0.000000", "duration": str(duration), "time_base": "1/15360"},
        {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100", "channels": 1,
         "start_time": "0.000000", "duration": str(duration)}],
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "start_time": "0.000000", "duration": str(duration)}}


@pytest.mark.parametrize("name", cache.SOURCES)
@pytest.mark.parametrize("damage", [None, "count", "duration", "rate", "size", "codec", "audio", "origin", "malformed", "exit"])
def test_required_semantics_fail_closed(monkeypatch, tmp_path, name, damage):
    data = probe_json(name)
    if damage == "count": data["streams"][0]["nb_frames"] = "1"
    if damage == "duration": data["streams"][0]["duration"] = "NaN"
    if damage == "rate": data["streams"][0]["avg_frame_rate"] = "0/0"
    if damage == "size": data["streams"][0]["width"] = 16
    if damage == "codec": data["streams"][0]["codec_name"] = "hevc"
    if damage == "audio": data["streams"][1]["channels"] = 2
    if damage == "origin": data["streams"][0]["start_time"] = "5"
    monkeypatch.setattr(cache.subprocess, "run", lambda *_a, **_kw: SimpleNamespace(
        returncode=1 if damage == "exit" else 0, stderr="probe failure",
        stdout="{" if damage == "malformed" else json.dumps(data)))
    if damage is None:
        assert cache.semantic_properties(tmp_path / name, SimpleNamespace(ffprobe=Path("ffprobe.exe")), name)["video"]["nb_frames"] in ("10800", "15000")
    else:
        with pytest.raises(cache.SourceCacheError):
            cache.semantic_properties(tmp_path / name, SimpleNamespace(ffprobe=Path("ffprobe.exe")), name)


def concurrent_worker(root, directory, runtime, ready, queue):
    cache.authenticated_hashes = fake_hashes
    cache.semantic_properties = fake_properties

    def generate(command, **options):
        time.sleep(0.15)
        Path(command[-1]).write_bytes(b"single atomic source")
        return SimpleNamespace(returncode=0, stderr="")

    cache.subprocess.run = generate
    ready.wait(10)
    try:
        sources = NativeSources(directory, runtime, cache_directory=root)
        sources.require("long60.mp4")
        queue.put(sources.reuse[0])
    except Exception as error:
        queue.put({"error": str(error)})


def locked_worker(path, ready):
    with cache._slot_lock(path, 1):
        ready.set()
        time.sleep(60)


def interrupted_generation_worker(root, directory, runtime, ready):
    cache.authenticated_hashes = fake_hashes
    cache.semantic_properties = fake_properties

    def generate(command, **options):
        Path(command[-1]).write_bytes(b"partial encoder bytes")
        ready.set()
        time.sleep(60)
        return SimpleNamespace(returncode=0, stderr="")

    cache.subprocess.run = generate
    NativeSources(directory, runtime, cache_directory=root).require("long60.mp4")


@pytest.mark.native
@pytest.mark.integration
def test_killed_encoder_owner_leaves_only_partial_and_next_run_recovers(setup):
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=interrupted_generation_worker,
                              args=(setup.root, setup.run, setup.runtime, ready))
    process.start()
    try:
        assert ready.wait(10)
        assert not (setup.root / "long60.mp4.entry").exists()
        assert not (setup.run / "long60.mp4").exists()
        process.terminate()
        process.join(10)
        with pytest.warns(RuntimeWarning, match="incomplete publication ignored"):
            recovered = materialize(setup)
        assert recovered.reuse[0]["mode"] == "cold"
        incomplete = list(setup.root.glob("*.partial-*"))
        assert len(incomplete) == 1
        assert (incomplete[0] / "long60.mp4").read_bytes() == b"partial encoder bytes"
    finally:
        if process.is_alive(): process.terminate()
        process.join(10)


@pytest.mark.native
@pytest.mark.integration
def test_four_process_cold_race_publishes_once(setup):
    context = multiprocessing.get_context("spawn")
    ready, queue = context.Event(), context.Queue()
    processes = []
    try:
        for index in range(4):
            directory = setup.run.parent / f"concurrent Кириллица {index}"
            directory.mkdir()
            process = context.Process(target=concurrent_worker, args=(setup.root, directory, setup.runtime, ready, queue))
            process.start()
            processes.append(process)
        ready.set()
        results = [queue.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        assert sorted(r["mode"] for r in results) == ["cold", "warm", "warm", "warm"]
        assert len({r["sha256"] for r in results}) == 1
        assert not list(setup.root.glob("*.partial-*"))
        assert len(list(setup.root.glob("*.entry"))) == 1
    finally:
        for process in processes:
            if process.is_alive(): process.terminate()
            process.join(10)
        queue.close()


@pytest.mark.native
@pytest.mark.integration
def test_lock_timeout_is_explicit_and_process_death_releases_lock(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    path = tmp_path / "fixture.lock"
    process = context.Process(target=locked_worker, args=(path, ready))
    process.start()
    try:
        assert ready.wait(10)
        with pytest.raises(cache.SourceCacheError, match="Timed out waiting"):
            with cache._slot_lock(path, 0.1):
                pytest.fail("concurrent lock admitted")
        process.terminate()
        process.join(10)
        with cache._slot_lock(path, 1):
            assert path.exists()
    finally:
        if process.is_alive(): process.terminate()
        process.join(10)
