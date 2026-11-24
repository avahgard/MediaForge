from dataclasses import replace
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.request
import zipfile

import pytest

from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.ffmpeg import provisioning
from mediaforge.infrastructure.ffmpeg.runtime import (
    FfmpegManifest, FfmpegRuntimeError, FfmpegRuntimeResolver, RuntimeErrorCode,
    manifest_path, resolve_ffmpeg_runtime,
)


FAKE_EXECUTABLES = {
    "ffmpeg.exe": b"fake ffmpeg executable",
    "ffprobe.exe": b"fake ffprobe executable",
}


@pytest.fixture
def manifest():
    # Synthetic executable bytes have independent test-only pins. Never rewrite
    # production hashes or mock hashing itself to make a fake process pass.
    return replace(
        FfmpegManifest.load(),
        ffmpeg_sha256=hashlib.sha256(FAKE_EXECUTABLES["ffmpeg.exe"]).hexdigest(),
        ffprobe_sha256=hashlib.sha256(FAKE_EXECUTABLES["ffprobe.exe"]).hexdigest(),
    )


@pytest.fixture
def profile(tmp_path):
    return ProfilePaths.for_test(tmp_path / "Профиль с пробелами")


def version_runner(manifest, calls=None):
    def run(command, **kwargs):
        if calls is not None:
            calls.append((command, kwargs))
        banner = f"{Path(command[0]).stem} version {manifest.banner_marker} Copyright"
        return subprocess.CompletedProcess(command, 0, banner + "\n", "")
    return run


def install_fake(manifest, profile):
    root = manifest.location(profile)
    for name in (manifest.ffmpeg_path, manifest.ffprobe_path):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(FAKE_EXECUTABLES[path.name])
    return root


def archive_bytes(manifest, extra=None, missing=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        for name in (manifest.ffmpeg_path, manifest.ffprobe_path):
            if name != missing:
                package.writestr(name, FAKE_EXECUTABLES[Path(name).name])
        if extra:
            member = zipfile.ZipInfo("placeholder")
            member.filename = extra  # Keep malicious backslashes on Windows.
            package.writestr(member, b"must not escape or be executed")
    return buffer.getvalue()


def serve(monkeypatch, payload, calls=None):
    def open_url(url, **kwargs):
        if calls is not None:
            calls.append(url)
        assert kwargs["timeout"] == 30
        return io.BytesIO(payload)
    monkeypatch.setattr(provisioning, "_open_artifact", open_url)


def test_manifest_exact_architect_pins():
    manifest = FfmpegManifest.load()
    assert manifest.provider == "gyan.dev"
    assert manifest.variant == "release-essentials"
    assert (manifest.platform, manifest.architecture) == ("windows", "x64")
    assert manifest.version == "9.0.2"
    assert manifest.source_commit == "946fcce07b"
    assert manifest.artifact == "ffmpeg-9.0.2-essentials_build.zip"
    assert manifest.sha256 == "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"
    assert manifest.primary_url == "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip"
    assert manifest.mirror_url == "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip"
    assert manifest.checksum_url == manifest.primary_url + ".sha256"
    assert manifest.ffmpeg_path == "ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe"
    assert manifest.ffprobe_path == "ffmpeg-9.0.2-essentials_build/bin/ffprobe.exe"
    assert manifest.banner_marker == "9.0.2-essentials_build-www.gyan.dev"
    assert manifest.ffmpeg_sha256 == "3256173f3f8bffd7df12227c68adf68025edb1832273a9530688a7bb1ed8edec"
    assert manifest.ffprobe_sha256 == "f0d36ecbbdd3bcfac3efa078c96c7271c2e68b3810595552ac3b7f17e9a65c52"


@pytest.mark.parametrize("field", list(FfmpegManifest.__dataclass_fields__))
def test_missing_manifest_field_is_controlled(tmp_path, field):
    data = json.loads(manifest_path().read_text(encoding="utf-8"))
    del data[field]
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(FfmpegRuntimeError) as error:
        FfmpegManifest.load(source)
    assert error.value.code is RuntimeErrorCode.MANIFEST_INVALID


@pytest.mark.parametrize("field,value", [
    ("sha256", "placeholder"), ("version", "latest"), ("source_commit", "unknown"),
    ("ffmpeg_sha256", "placeholder"), ("ffprobe_sha256", "0" * 63),
    ("ffmpeg_sha256", None), ("ffprobe_sha256", ""),
    ("provider", "other"), ("variant", "full"), ("architecture", "x86"),
    ("primary_url", "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"),
    ("mirror_url", "https://example.org/ffmpeg.zip"),
    ("ffmpeg_path", "../ffmpeg.exe"), ("ffprobe_path", "C:/ffprobe.exe"),
    ("runtime_id", "../escape"), ("version", None), ("license", ""),
])
def test_invalid_manifest_fields_are_controlled(tmp_path, field, value):
    data = json.loads(manifest_path().read_text(encoding="utf-8"))
    data[field] = value
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(FfmpegRuntimeError) as error:
        FfmpegManifest.load(source)
    assert error.value.code is RuntimeErrorCode.MANIFEST_INVALID


@pytest.mark.parametrize("content", [None, "{", "[]"])
def test_missing_or_malformed_manifest(tmp_path, content):
    path = tmp_path / "manifest.json"
    if content is not None:
        path.write_text(content)
    with pytest.raises(FfmpegRuntimeError) as error:
        FfmpegManifest.load(path)
    assert error.value.code is RuntimeErrorCode.MANIFEST_INVALID


def test_resolution_is_lazy_deterministic_and_cwd_independent(manifest, profile, monkeypatch, tmp_path):
    expected = manifest.location(profile)
    monkeypatch.chdir(tmp_path)
    assert FfmpegManifest.load().location(profile) == expected
    assert manifest.location(profile) == expected
    assert expected.is_relative_to(profile.cache)
    assert manifest.version in expected.name and manifest.sha256 in expected.name
    assert not profile.root.exists()
    with pytest.raises(FfmpegRuntimeError) as error:
        FfmpegRuntimeResolver(manifest).resolve(profile)
    assert error.value.code is RuntimeErrorCode.NOT_PROVISIONED
    assert not profile.root.exists()


def test_valid_runtime_resolves_both_and_invokes_safely(manifest, profile):
    root = install_fake(manifest, profile)
    calls = []
    runtime = FfmpegRuntimeResolver(manifest, runner=version_runner(manifest, calls)).resolve(profile)
    assert runtime.ffmpeg == root / manifest.ffmpeg_path
    assert runtime.ffprobe == root / manifest.ffprobe_path
    assert len(calls) == 2
    for command, kwargs in calls:
        assert isinstance(command, list) and len(command) == 2
        assert command[1] == "-version"
        assert "Профиль с пробелами" in command[0]
        assert kwargs["shell"] is False
        assert kwargs["timeout"] == 5.0
        assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


@pytest.mark.parametrize("member", ["ffmpeg_path", "ffprobe_path"])
def test_missing_executable(manifest, profile, member):
    root = install_fake(manifest, profile)
    (root / getattr(manifest, member)).unlink()
    with pytest.raises(FfmpegRuntimeError) as error:
        FfmpegRuntimeResolver(manifest).resolve(profile)
    assert error.value.code is RuntimeErrorCode.EXECUTABLE_MISSING


@pytest.mark.parametrize("banner", ["", "ffmpeg version 8.0", "ffmpeg version 9.0.2-essentials_build-www.gyan.dev-evil"])
def test_wrong_version_rejected(manifest, profile, banner):
    install_fake(manifest, profile)
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, banner, "")
    with pytest.raises(FfmpegRuntimeError) as error:
        FfmpegRuntimeResolver(manifest, runner=run).resolve(profile)
    assert error.value.code is RuntimeErrorCode.VERSION_MISMATCH


@pytest.mark.parametrize("failure,code", [
    (OSError("bad executable"), RuntimeErrorCode.LAUNCH_FAILED),
    (subprocess.TimeoutExpired("version", 5), RuntimeErrorCode.TIMEOUT),
])
def test_process_exceptions_are_controlled(manifest, profile, failure, code):
    install_fake(manifest, profile)
    def run(*args, **kwargs):
        raise failure
    with pytest.raises(FfmpegRuntimeError) as error:
        FfmpegRuntimeResolver(manifest, runner=run).resolve(profile)
    assert error.value.code is code


def test_nonzero_version_process_is_rejected(manifest, profile):
    install_fake(manifest, profile)
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "failed")
    with pytest.raises(FfmpegRuntimeError) as error:
        FfmpegRuntimeResolver(manifest, runner=run).resolve(profile)
    assert error.value.code is RuntimeErrorCode.LAUNCH_FAILED


def test_override_verifies_companion_and_does_not_fall_back(manifest, profile):
    root = install_fake(manifest, profile)
    resolver = FfmpegRuntimeResolver(manifest, runner=version_runner(manifest))
    assert resolver.resolve(profile, ffprobe_override=root / manifest.ffprobe_path).ffmpeg == root / manifest.ffmpeg_path
    for path, code in [
        (Path("ffprobe.exe"), RuntimeErrorCode.CONFIGURATION_INVALID),
        (root / "other.exe", RuntimeErrorCode.CONFIGURATION_INVALID),
        (root / "absent/ffprobe.exe", RuntimeErrorCode.EXECUTABLE_MISSING),
    ]:
        with pytest.raises(FfmpegRuntimeError) as error:
            resolver.resolve(profile, ffprobe_override=path)
        assert error.value.code is code


def test_environment_never_uses_path(isolated_environment, tmp_path):
    isolated_environment.pop("MEDIAFORGE_FFPROBE_PATH", None)
    isolated_environment["PATH"] = str(tmp_path)
    (tmp_path / "ffprobe.exe").write_bytes(b"untrusted")
    with pytest.raises(FfmpegRuntimeError) as error:
        resolve_ffmpeg_runtime(environment=isolated_environment)
    assert error.value.code is RuntimeErrorCode.NOT_PROVISIONED
    isolated_environment["MEDIAFORGE_FFPROBE_PATH"] = "ffprobe.exe"
    with pytest.raises(FfmpegRuntimeError) as error:
        resolve_ffmpeg_runtime(environment=isolated_environment)
    assert error.value.code is RuntimeErrorCode.CONFIGURATION_INVALID


def test_valid_archive_provisions_unicode_target_and_is_idempotent(manifest, profile, monkeypatch):
    payload = archive_bytes(manifest, extra="unrelated.exe")
    # Only fixture hashes change; production manifest is never rewritten or trusted dynamically.
    fixture_manifest = replace(manifest, sha256=hashlib.sha256(payload).hexdigest())
    calls = []
    serve(monkeypatch, payload, calls)
    runner = version_runner(manifest)
    first = provisioning.provision_ffmpeg(profile, manifest=fixture_manifest, runner=runner)
    second = provisioning.provision_ffmpeg(profile, manifest=fixture_manifest, runner=runner)
    assert first == second
    assert calls == [manifest.primary_url]
    root = fixture_manifest.location(profile)
    assert provisioning.verify_archive(root / manifest.artifact, fixture_manifest) == fixture_manifest.sha256
    assert not (root / "unrelated.exe").exists()
    assert not list(root.parent.glob(".ffmpeg-staging-*"))


def test_hash_mismatch_is_hard_failure_without_mirror(manifest, profile, monkeypatch):
    calls = []
    serve(monkeypatch, b"wrong archive", calls)
    with pytest.raises(FfmpegRuntimeError) as error:
        provisioning.provision_ffmpeg(profile, manifest=manifest)
    assert error.value.code is RuntimeErrorCode.HASH_MISMATCH
    assert calls == [manifest.primary_url]
    assert not manifest.location(profile).exists()
    assert not list(manifest.location(profile).parent.glob(".ffmpeg-staging-*"))


@pytest.mark.parametrize("extra", ["../escape", "/absolute", "C:/escape", "dir\\escape", "dir/../escape", "dir./escape"])
def test_traversal_is_rejected_before_publication(manifest, profile, monkeypatch, extra):
    payload = archive_bytes(manifest, extra=extra)
    fixture_manifest = replace(manifest, sha256=hashlib.sha256(payload).hexdigest())
    serve(monkeypatch, payload)
    with pytest.raises(FfmpegRuntimeError) as error:
        provisioning.provision_ffmpeg(profile, manifest=fixture_manifest)
    assert error.value.code is RuntimeErrorCode.ARCHIVE_INVALID
    assert not fixture_manifest.location(profile).exists()
    assert not list(fixture_manifest.location(profile).parent.iterdir())


def test_partial_existing_install_fails_without_download(manifest, profile, monkeypatch):
    root = install_fake(manifest, profile)
    (root / manifest.ffprobe_path).unlink()
    calls = []
    serve(monkeypatch, b"not used", calls)
    with pytest.raises(FfmpegRuntimeError) as error:
        provisioning.provision_ffmpeg(profile, manifest=manifest)
    assert error.value.code is RuntimeErrorCode.EXECUTABLE_MISSING
    assert not calls


def test_failed_verification_does_not_publish(manifest, profile, monkeypatch):
    payload = archive_bytes(manifest)
    fixture_manifest = replace(manifest, sha256=hashlib.sha256(payload).hexdigest())
    serve(monkeypatch, payload)
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "wrong version", "")
    with pytest.raises(FfmpegRuntimeError) as error:
        provisioning.provision_ffmpeg(profile, manifest=fixture_manifest, runner=run)
    assert error.value.code is RuntimeErrorCode.VERSION_MISMATCH
    assert not fixture_manifest.location(profile).exists()


@pytest.mark.parametrize("mirror_available", [True, False])
def test_only_authorized_mirror_on_primary_unavailable(manifest, profile, monkeypatch, mirror_available):
    payload = archive_bytes(manifest)
    fixture_manifest = replace(manifest, sha256=hashlib.sha256(payload).hexdigest())
    calls = []
    def open_url(url, **kwargs):
        calls.append(url)
        if url == manifest.primary_url or not mirror_available:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return io.BytesIO(payload)
    monkeypatch.setattr(provisioning, "_open_artifact", open_url)
    if mirror_available:
        provisioning.provision_ffmpeg(profile, manifest=fixture_manifest, runner=version_runner(manifest))
    else:
        with pytest.raises(FfmpegRuntimeError) as error:
            provisioning.provision_ffmpeg(profile, manifest=fixture_manifest)
        assert error.value.code is RuntimeErrorCode.DOWNLOAD_FAILED
        assert not fixture_manifest.location(profile).exists()
    assert calls == [manifest.primary_url, manifest.mirror_url]


@pytest.mark.parametrize("invalid_zip", [True, False])
def test_invalid_or_incomplete_archive(manifest, profile, monkeypatch, invalid_zip):
    payload = b"not a zip" if invalid_zip else archive_bytes(manifest, missing=manifest.ffprobe_path)
    fixture_manifest = replace(manifest, sha256=hashlib.sha256(payload).hexdigest())
    serve(monkeypatch, payload)
    with pytest.raises(FfmpegRuntimeError) as error:
        provisioning.provision_ffmpeg(profile, manifest=fixture_manifest)
    assert error.value.code is RuntimeErrorCode.ARCHIVE_INVALID
    assert not fixture_manifest.location(profile).exists()


@pytest.mark.parametrize("member", ["ffmpeg_path", "ffprobe_path"])
@pytest.mark.parametrize("archive_present", [False, True])
def test_tampered_executable_with_unchanged_banner_rejected_before_any_launch(
    manifest, profile, monkeypatch, member, archive_present
):
    payload = archive_bytes(manifest)
    manifest = replace(manifest, sha256=hashlib.sha256(payload).hexdigest())
    root = install_fake(manifest, profile)
    if archive_present:
        (root / manifest.artifact).write_bytes(payload)
        assert provisioning.verify_archive(root / manifest.artifact, manifest) == manifest.sha256
    executable = root / getattr(manifest, member)
    executable.write_bytes(executable.read_bytes() + b"tampered")
    # The process double still reports the exact version for the modified file.
    # Neither executable may be launched, including when only FFprobe is modified.
    process_calls = []
    runner = version_runner(manifest, process_calls)
    assert manifest.banner_marker in runner([str(executable), "-version"]).stdout
    process_calls.clear()
    downloads = []
    serve(monkeypatch, payload, downloads)
    resolver = FfmpegRuntimeResolver(manifest, runner=runner)
    for operation in (
        lambda: resolver.resolve(profile),
        lambda: provisioning.provision_ffmpeg(profile, manifest=manifest, runner=runner),
    ):
        with pytest.raises(FfmpegRuntimeError) as error:
            operation()
        assert error.value.code is RuntimeErrorCode.EXECUTABLE_HASH_MISMATCH
        assert executable.name in str(error.value)
    assert process_calls == []
    assert downloads == []


def test_correct_hashes_without_archive_accepted_but_rechecked_on_every_resolve(manifest, profile):
    root = install_fake(manifest, profile)
    assert not (root / manifest.artifact).exists()
    calls = []
    resolver = FfmpegRuntimeResolver(manifest, runner=version_runner(manifest, calls))
    assert resolver.resolve(profile).ffmpeg == root / manifest.ffmpeg_path
    assert len(calls) == 2
    (root / manifest.ffprobe_path).write_bytes(b"modified after first resolution")
    with pytest.raises(FfmpegRuntimeError) as error:
        resolver.resolve(profile)
    assert error.value.code is RuntimeErrorCode.EXECUTABLE_HASH_MISMATCH
    assert len(calls) == 2


@pytest.mark.parametrize("member", ["ffmpeg_sha256", "ffprobe_sha256"])
def test_mismatching_executable_pin_rejected(manifest, profile, member):
    install_fake(manifest, profile)
    manifest = replace(manifest, **{member: "0" * 64})
    calls = []
    with pytest.raises(FfmpegRuntimeError) as error:
        FfmpegRuntimeResolver(manifest, runner=version_runner(manifest, calls)).resolve(profile)
    assert error.value.code is RuntimeErrorCode.EXECUTABLE_HASH_MISMATCH
    assert calls == []


def test_unreadable_executable_integrity_is_controlled(manifest, profile, monkeypatch):
    root = install_fake(manifest, profile)
    original_open = Path.open
    def denied(path, *args, **kwargs):
        if path == root / manifest.ffprobe_path:
            raise PermissionError("executable cannot be read")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", denied)
    calls = []
    with pytest.raises(FfmpegRuntimeError) as error:
        FfmpegRuntimeResolver(manifest, runner=version_runner(manifest, calls)).resolve(profile)
    assert error.value.code is RuntimeErrorCode.INTEGRITY_UNAVAILABLE
    assert "executable cannot be read" in str(error.value)
    assert calls == []


def test_explicit_override_retains_separate_version_policy(manifest, profile):
    root = install_fake(manifest, profile)
    # These test binaries do not match the production pins. Explicit override
    # intentionally verifies executable/version, not managed artifact provenance.
    actual_manifest = FfmpegManifest.load()
    calls = []
    resolver = FfmpegRuntimeResolver(actual_manifest, runner=version_runner(actual_manifest, calls))
    assert resolver.resolve(profile, ffprobe_override=root / manifest.ffprobe_path).ffprobe == root / manifest.ffprobe_path
    assert len(calls) == 2


@pytest.mark.parametrize("stage", ["create", "write", "close", "hash-read"])
def test_local_write_failure_does_not_retry_mirror(manifest, profile, monkeypatch, stage):
    payload = archive_bytes(manifest)
    downloads = []
    serve(monkeypatch, payload, downloads)
    original_open = Path.open
    failure = OSError("local disk full")

    @contextmanager
    def failed_write(path, *args, **kwargs):
        with original_open(path, *args, **kwargs) as stream:
            class Writer:
                def write(self, chunk):
                    if stage == "write":
                        raise failure
                    return stream.write(chunk)
            yield Writer()
            if stage == "close":
                raise failure

    def open_target(path, mode="r", *args, **kwargs):
        if path.name == manifest.artifact:
            if mode == "wb":
                if stage == "create":
                    raise PermissionError("local permission denied")
                return failed_write(path, mode, *args, **kwargs)
            if mode == "rb" and stage == "hash-read":
                raise PermissionError("local archive unreadable")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_target)
    with pytest.raises(FfmpegRuntimeError) as error:
        provisioning.provision_ffmpeg(profile, manifest=manifest)
    assert error.value.code is RuntimeErrorCode.SETUP_FAILED
    expected = {"create": "local permission denied", "hash-read": "local archive unreadable"}.get(stage, "local disk full")
    assert expected in str(error.value)
    assert isinstance(error.value.__cause__, OSError)
    assert downloads == [manifest.primary_url]
    assert not manifest.location(profile).exists()
    assert not list(manifest.location(profile).parent.glob(".ffmpeg-staging-*"))


def test_local_target_directory_failure_does_not_download(manifest, profile, monkeypatch):
    downloads = []
    serve(monkeypatch, b"unused", downloads)
    original_mkdir = Path.mkdir
    def denied(path, *args, **kwargs):
        if path == manifest.location(profile).parent:
            raise PermissionError("local target permission denied")
        return original_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", denied)
    with pytest.raises(FfmpegRuntimeError) as error:
        provisioning.provision_ffmpeg(profile, manifest=manifest)
    assert error.value.code is RuntimeErrorCode.SETUP_FAILED
    assert "local target permission denied" in str(error.value)
    assert downloads == []


@pytest.mark.parametrize("mirror_available", [True, False])
def test_source_read_failure_can_retry_mirror(manifest, profile, monkeypatch, mirror_available):
    payload = archive_bytes(manifest)
    manifest = replace(manifest, sha256=hashlib.sha256(payload).hexdigest())
    downloads = []
    class FailedSource(io.BytesIO):
        def read(self, *args):
            raise OSError("source connection interrupted")
    def open_url(url, **kwargs):
        downloads.append(url)
        if url == manifest.primary_url or not mirror_available:
            return FailedSource()
        return io.BytesIO(payload)
    monkeypatch.setattr(provisioning, "_open_artifact", open_url)
    if mirror_available:
        provisioning.provision_ffmpeg(profile, manifest=manifest, runner=version_runner(manifest))
    else:
        with pytest.raises(FfmpegRuntimeError) as error:
            provisioning.provision_ffmpeg(profile, manifest=manifest)
        assert error.value.code is RuntimeErrorCode.DOWNLOAD_FAILED
        assert "source connection interrupted" in str(error.value)
    assert downloads == [manifest.primary_url, manifest.mirror_url]


def test_download_stops_before_writing_an_oversized_archive(manifest, profile, monkeypatch):
    calls = []
    serve(monkeypatch, b"bounded synthetic bytes", calls)
    monkeypatch.setattr(provisioning, "MAX_ARCHIVE_BYTES", 8)

    with pytest.raises(FfmpegRuntimeError, match="staging size limit") as error:
        provisioning.provision_ffmpeg(profile, manifest=manifest)

    assert error.value.code is RuntimeErrorCode.DOWNLOAD_FAILED
    assert calls == [manifest.primary_url]
    assert not list(manifest.location(profile).parent.iterdir())


def test_provisioner_redirects_stay_within_each_authorized_publisher(manifest):
    mirror = provisioning._ArtifactRedirect(provisioning.ARTIFACT_HOSTS["github.com"])
    cdn = "https://release-assets.githubusercontent.com/artifact"
    request = urllib.request.Request(manifest.mirror_url)
    assert mirror.redirect_request(request, None, 302, "Found", {}, cdn).full_url == cdn

    primary = provisioning._ArtifactRedirect(provisioning.ARTIFACT_HOSTS["www.gyan.dev"])
    for redirect, url in (
        (primary, cdn),
        (mirror, "http://release-assets.githubusercontent.com/artifact"),
        (mirror, "https://unrelated.invalid/artifact"),
    ):
        with pytest.raises(FfmpegRuntimeError, match="authorized HTTPS sources"):
            redirect.redirect_request(request, None, 302, "Found", {}, url)
