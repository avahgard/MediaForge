from pathlib import Path
import pytest


@pytest.mark.integration
@pytest.mark.parametrize('case',['good','unlisted','changed','bytecode'])
def test_private_stdlib_imports_are_exact_core_source_objects(run_python,case):
    result=run_python(f'''
import hashlib,importlib,json,py_compile,sys
from pathlib import Path
from mediaforge.infrastructure.models.core_loader import install
root=Path.cwd()/'private-core';library=root/'Core/Python/Lib';library.mkdir(parents=True)
source=library/'core_sample.py';source.write_text('value=42\\n',encoding='utf-8')
row=hashlib.sha256(source.read_bytes()).hexdigest()+'\\t'+str(source.stat().st_size)+'\\tCore/Python/Lib/core_sample.py\\n'
manifest=root/'CoreManifest.tsv';manifest.write_bytes(row.encode())
case={case!r}
if case=='bytecode':
    source.write_text('value=99\\n');py_compile.compile(str(source),doraise=True)
    source.write_text('value=42\\n')
if case=='changed':source.write_text('value=17\\n')
if case=='unlisted':(library/'unlisted_sample.py').write_text('value=7\\n')
sys.base_prefix=str(root/'Core/Python');sys.path.insert(0,str(library))
install(root,hashlib.sha256(manifest.read_bytes()).hexdigest())
name='unlisted_sample' if case=='unlisted' else 'core_sample'
try:module=importlib.import_module(name)
except ImportError:
    assert case in ('unlisted','changed')
else:
    assert case in ('good','bytecode') and module.value==42
''')
    assert result.returncode==0,result.stdout+result.stderr
