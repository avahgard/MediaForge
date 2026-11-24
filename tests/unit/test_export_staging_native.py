"""NTFS object identity and retirement at the actual Windows cleanup boundaries."""
import ctypes
import json
import os
import stat
import subprocess

import pytest

from mediaforge.features.media.export import ExportError
from mediaforge.infrastructure.filesystem.export_output import ExportOutput


pytestmark = [pytest.mark.native, pytest.mark.integration]


def identity(path):
    info = path.lstat()
    return info.st_dev, info.st_ino


def staged(tmp_path):
    source, target = tmp_path / 'source.mp4', tmp_path / 'existing.mp4'
    source.write_bytes(b'source sentinel')
    target.write_bytes(b'destination sentinel')
    output = ExportOutput(source, target)
    output.__enter__()
    with output.staging.descriptor() as stream:
        stream.write(b'owned partial')
    output.staging.finish_writing()
    return output


def release_test_handles(output):
    for name in ('deletion', 'writer', 'partial_anchor', 'directory_pin', 'directory_anchor'):
        output.staging._close(name)
    output.staging.parents.close()


def assert_sentinels(output):
    assert output.input.read_bytes() == b'source sentinel'
    assert output.destination.read_bytes() == b'destination sentinel'


@pytest.mark.parametrize('readonly', [False, True])
def test_normal_removes_both_owned_objects(tmp_path, readonly):
    output = staged(tmp_path)
    path, partial = output.directory, output.staged
    if readonly:
        os.chmod(path, stat.S_IREAD)
        assert path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    keys = [identity(path), identity(partial)]
    output._cleanup()
    assert not path.exists() and not partial.exists()
    assert_sentinels(output)
    (tmp_path / 'ownership-proof.json').write_text(json.dumps({'removed': keys, 'readonly': readonly}))


@pytest.mark.parametrize('directory,readonly', [(True, False), (True, True), (False, False)],
                         ids=['directory', 'readonly-directory', 'partial'])
@pytest.mark.parametrize('before', [True, False], ids=['before-verify', 'after-verify'])
def test_substitution_preserves_foreign_and_source_objects(tmp_path, directory, readonly, before, monkeypatch):
    output = staged(tmp_path)
    staging = output.staging
    path = output.directory if directory else output.staged
    parked, foreign = tmp_path / 'parked', tmp_path / 'foreign'
    if directory:
        foreign.mkdir()  # An empty foreign directory would pass rmdir.
        if readonly:
            os.chmod(path, stat.S_IREAD)
            os.chmod(foreign, stat.S_IREAD)
    else:
        foreign.write_bytes(b'foreign file')
    original_id, foreign_id = identity(path), identity(foreign)
    observed = []
    deletions = 0
    open_entry = staging._open
    set_disposition = staging.api.SetFileInformationByHandle

    def open_with_substitution(name, access, share, *, directory):
        if name == path and access & 0x10000:
            name.rename(parked)
            foreign.rename(name)
            observed.append(identity(name))
        return open_entry(name, access, share, directory=directory)

    def delete_with_substitution(handle, *args):
        nonlocal deletions
        deletions += 1
        if deletions == (2 if directory else 1):
            assert staging._key(handle, directory=directory) == owned_key
            assert args[0] == 21 and ctypes.cast(args[1], ctypes.POINTER(ctypes.c_uint32)).contents.value == (
                0x13 if readonly else 3)
            with pytest.raises(OSError) as blocked:
                path.rename(parked)
            observed.append(blocked.value.winerror)
            assert identity(path) == original_id
        return set_disposition(handle, *args)

    try:
        if before:
            monkeypatch.setattr(staging, '_open', open_with_substitution)
            with pytest.raises(ExportError, match='identity changed') as failure:
                output._cleanup()
            assert failure.value.retained_resources == (output,)
            assert observed == [foreign_id]
            assert identity(parked) == original_id and identity(path) == foreign_id
            if not directory:
                assert parked.read_bytes() == b'owned partial' and path.read_bytes() == b'foreign file'
        else:
            anchor = staging.directory_pin if directory else staging.partial_anchor
            owned_key = staging._key(anchor, directory=directory)
            monkeypatch.setattr(staging.api, 'SetFileInformationByHandle', delete_with_substitution)
            output._cleanup()
            assert len(observed) == 1 and observed[0] in (5, 32)
            assert not path.exists() and not parked.exists()
            assert identity(foreign) == foreign_id
            if not directory:
                assert foreign.read_bytes() == b'foreign file'
        assert_sentinels(output)
        (tmp_path / 'ownership-proof.json').write_text(json.dumps({
            'owned_id': original_id, 'foreign_id': foreign_id, 'observed': observed, 'before': before,
            'readonly': readonly}))
    finally:
        release_test_handles(output)


@pytest.mark.parametrize('readonly_partial', [False, True])
def test_readonly_handling_does_not_remove_foreign_members_or_override_files(tmp_path, readonly_partial):
    output = staged(tmp_path)
    path = output.staged if readonly_partial else output.directory
    foreign = output.directory / 'foreign'
    foreign.write_bytes(b'foreign data')
    os.chmod(path, stat.S_IREAD)
    assert path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    original = identity(path)
    try:
        with pytest.raises(ExportError) as retained:
            output._cleanup()
        error = 5 if readonly_partial else 145
        assert f'[WinError {error}]' in str(retained.value)
        assert retained.value.retained_resources == (output,)
        assert identity(path) == original and foreign.read_bytes() == b'foreign data'
        assert output.staged.exists() == readonly_partial
        assert_sentinels(output)
    finally:
        release_test_handles(output)


@pytest.mark.parametrize('kind', ['access', 'sharing', 'byte-lock', 'persistent'])
def test_cleanup_retries_only_transient_windows_errors(tmp_path, monkeypatch, kind):
    output = staged(tmp_path)
    path, staging = output.directory, output.staging
    entry_id = identity(path)
    open_entry, errors = staging._open, []
    handle = None
    attempts = 0
    acl_applied = False

    def acl(target, option, value):
        subprocess.run([r'C:\Windows\System32\icacls.exe', str(target), option, value],
                       check=True, capture_output=True, timeout=5)

    def release():
        nonlocal handle, acl_applied
        if handle is not None:
            staging._require(staging.api.CloseHandle(handle))
            handle = None
        if acl_applied:
            acl(path, '/remove:d', '*S-1-1-0')
            acl(tmp_path, '/remove:d', '*S-1-1-0')
            acl_applied = False

    def fault(name, access, share, *, directory):
        nonlocal handle, attempts, acl_applied
        if name != path or not access & 0x10000:
            return open_entry(name, access, share, directory=directory)
        attempts += 1
        if attempts == 1:
            if kind == 'access':
                acl(tmp_path, '/deny', '*S-1-1-0:(DC)')
                acl(path, '/deny', '*S-1-1-0:(DE)')
                acl_applied = True
            elif kind != 'byte-lock':
                handle = open_entry(path, 1, 3, directory=True)
        elif kind != 'persistent':
            release()
        try:
            if kind == 'byte-lock' and attempts == 1:
                raise ctypes.WinError(33)
            return open_entry(name, access, share, directory=directory)
        except OSError as error:
            errors.append(error.winerror)
            raise

    monkeypatch.setattr(staging, '_open', fault)
    try:
        if kind == 'persistent':
            with pytest.raises(ExportError) as retained:
                output._cleanup()
            assert retained.value.retained_resources == (output,)
            assert errors == [32] * 6 and identity(path) == entry_id
            assert not list(path.iterdir())
        else:
            output._cleanup()
            assert not path.exists()
            assert errors == [{'access': 5, 'sharing': 32, 'byte-lock': 33}[kind]] and attempts == 2
        assert_sentinels(output)
        (tmp_path / 'ownership-proof.json').write_text(json.dumps({
            'owned_id': entry_id, 'errors': errors, 'attempts': attempts, 'remains': path.exists()}))
    finally:
        release()
        release_test_handles(output)
