from dataclasses import replace
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest, manifest_path, resolve_ffmpeg_runtime


def test_executable_relative_lazy_cwd_independent_root(tmp_path,monkeypatch):
    executable=tmp_path/'Portable Кириллица'/'MediaForge.exe'
    executable.parent.mkdir();executable.write_bytes(b'fixture')
    monkeypatch.chdir(tmp_path.parent)
    paths=ProfilePaths.from_executable(executable)
    assert paths.root==executable.parent/'Data/v2' and not paths.root.exists()
    paths.prepare()
    assert paths.temporary.is_dir()
    assert not list(paths.root.rglob('.write-check-*'))
    env=paths.child_environment({'TEMP':'outside','TMP':'outside','KEEP':'yes'})
    assert env['TEMP']==str(paths.temporary)==env['TMP']==env['TMPDIR']
    assert env['KEEP']=='yes'


def test_packaged_ignores_environment_injection_and_uses_shipped_manifest(tmp_path,monkeypatch):
    manifest=FfmpegManifest.load()
    executable=tmp_path/'MediaForge.exe';executable.write_bytes(b'fixture')
    monkeypatch.setattr(sys,'frozen',True,raising=False);monkeypatch.setattr(sys,'executable',str(executable))
    assert ProfilePaths.from_environment({'MEDIAFORGE_DATA_ROOT':str(tmp_path/'wrong')}).root==tmp_path/'Data/v2'
    assert manifest_path()==tmp_path/'external/manifests/ffmpeg.json'
    with pytest.raises(Exception):resolve_ffmpeg_runtime(environment={'MEDIAFORGE_FFPROBE_PATH':str(tmp_path/'ffprobe.exe')})


@pytest.mark.parametrize('root',['relative','D:/bad/../root','D:/bad./root','//server/share/data'])
def test_ambiguous_or_nonlocal_root_refuses(root):
    with pytest.raises(ValueError):ProfilePaths.for_test(Path(root))


def test_write_failure_no_fallback(tmp_path,monkeypatch):
    paths=ProfilePaths.for_test(tmp_path/'data');real=Path.open
    def deny(path,*args,**kwargs):
        if path.name.startswith('.write-check-'):raise PermissionError('read-only root')
        return real(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',deny)
    with pytest.raises(ValueError,match='Extract MediaForge'):paths.prepare()
    assert set(tmp_path.iterdir())=={paths.root}


def test_mapped_remote_drive_refuses(tmp_path,monkeypatch):
    import mediaforge.infrastructure.filesystem.profile as module
    def drive_type(anchor):return 4
    monkeypatch.setattr(module.ctypes,'WinDLL',lambda *a,**k:SimpleNamespace(GetDriveTypeW=drive_type))
    with pytest.raises(ValueError,match='local'):ProfilePaths.for_test(tmp_path/'remote')


def test_isolated_roots_and_folder_move_rebind_relative_data(tmp_path):
    exe=tmp_path/'original'/'MediaForge.exe';exe.parent.mkdir();exe.write_bytes(b'fixture')
    a=ProfilePaths.from_executable(exe);a.prepare()
    (a.models/'sentinel').write_bytes(b'weights');(a.config/'settings.json').write_bytes(b'{}')
    b=ProfilePaths.for_test(tmp_path/'isolated');b.prepare()
    assert not (b.models/'sentinel').exists()
    moved=tmp_path/'moved'
    assert exe.parent.resolve().is_relative_to(tmp_path.resolve())
    assert moved.resolve().is_relative_to(tmp_path.resolve())
    shutil.move(exe.parent,moved)
    c=ProfilePaths.from_executable(moved/exe.name)
    assert (c.models/'sentinel').read_bytes()==b'weights'
    assert (c.config/'settings.json').read_bytes()==b'{}'


@pytest.mark.native
def test_real_windows_unwritable_root_refuses(tmp_path):
    # Real required-directory collision: deterministic even under elevated ACLs.
    paths=ProfilePaths.for_test(tmp_path/'readonly');paths.root.mkdir()
    # A file occupying required config is a real filesystem refusal independent of ACL admin privileges.
    paths.config.write_bytes(b'owned sentinel')
    with pytest.raises(ValueError,match='Extract MediaForge'):paths.prepare()
    assert paths.config.read_bytes()==b'owned sentinel'
