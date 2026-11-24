"""Session fact reuse must never replace full protected source authentication."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import os
import pytest
from mediaforge.features.dub.contracts import DubControl
from mediaforge.infrastructure.ffmpeg.dub_probe import FfprobeDubInspector
from mediaforge.infrastructure.filesystem.dub_source import ProtectedDubSource

pytestmark=pytest.mark.native


def test_qualified_source_reuse_hashes_even_when_stat_signature_is_restored(tmp_path):
    path=tmp_path/'Исходник source.mp4';path.write_bytes(b'first media bytes')
    probe=FfprobeDubInspector(None,None)
    calls=[]
    def scan(source,control):
        calls.append(source)
        with ProtectedDubSource(source,control) as protected:
            return SimpleNamespace(identity=protected.identity)
    probe._inspect=scan
    first=probe.inspect(path,DubControl())
    assert probe.inspect(path,DubControl()) is first
    assert len(calls)==1
    before=path.stat()
    path.write_bytes(b'other media bytes')
    os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
    assert path.stat().st_size==before.st_size and path.stat().st_mtime_ns==before.st_mtime_ns
    changed=probe.inspect(path,DubControl())
    assert len(calls)==2 and changed.identity.sha256!=first.identity.sha256


def test_qualified_fact_cache_is_bounded_and_does_not_adopt_failed_inspections(tmp_path):
    probe=FfprobeDubInspector(None,None)
    def scan(path,control):
        with ProtectedDubSource(path,control) as protected:
            if path.name=='bad.mp4':raise ValueError('unqualified')
            return SimpleNamespace(identity=protected.identity)
    probe._inspect=scan
    for i in range(10):
        path=tmp_path/f'{i}.mp4';path.write_bytes(b'source');probe.inspect(path,DubControl())
    assert len(probe._qualified_sources)==8
    path=tmp_path/'bad.mp4';path.write_bytes(b'bad')
    with pytest.raises(ValueError,match='unqualified'):probe.inspect(path,DubControl())
    assert not any(key[0]==str(path) for key in probe._qualified_sources)
