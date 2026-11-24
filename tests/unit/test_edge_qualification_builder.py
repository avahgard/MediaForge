"""Small archive defenses of the offline development-only builder."""
import importlib.util
from pathlib import Path
import zipfile
import pytest

spec=importlib.util.spec_from_file_location('edge_builder',Path(__file__).resolve().parents[2]/'tools/provision_edge_qualification.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

@pytest.mark.parametrize('name',['../escape','/absolute','directory\\escape','a//b','a:stream','file.','CON.txt','a/NUL','a?.py'])
def test_invalid_wheel_paths_refuse_before_materialization(tmp_path,name):
    wheel=tmp_path/'bad.whl'
    with zipfile.ZipFile(wheel,'w') as out:
        row=zipfile.ZipInfo('safe');row.filename=name;row.orig_filename=name
        out.writestr(row,b'harmless')
    with pytest.raises(ValueError):builder.inspect_wheel(wheel)

def test_symlink_duplicate_and_size_refusal(tmp_path):
    for mode in ('link','duplicate','size'):
        wheel=tmp_path/(mode+'.whl')
        with zipfile.ZipFile(wheel,'w') as out:
            if mode=='link':
                row=zipfile.ZipInfo('link');row.create_system=3;row.external_attr=0o120777<<16;out.writestr(row,b'outside')
            elif mode=='duplicate':out.writestr('a.py',b'a');out.writestr('A.py',b'b')
            else:out.writestr('too-big',bytes(16*1024**2+1),compress_type=zipfile.ZIP_DEFLATED)
        with pytest.raises(ValueError):builder.inspect_wheel(wheel)

def test_regular_flat_wheel_inspects_without_executing(tmp_path):
    wheel=tmp_path/'safe.whl'
    with zipfile.ZipFile(wheel,'w') as out:out.writestr('package/__init__.py',b'raise RuntimeError("never execute during build")')
    assert builder.inspect_wheel(wheel)==len(b'raise RuntimeError("never execute during build")')
