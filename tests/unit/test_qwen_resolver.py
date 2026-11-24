from pathlib import Path
import pytest


@pytest.mark.integration
def test_qwen_only_resolver_rejects_unverified_filesystem_code_and_filename_spoof(run_python,tmp_path):
    result=run_python(f'''
import hashlib,json,sys
from pathlib import Path
from mediaforge.infrastructure.models.qwen_resolver import install_runtime
root=Path({str(tmp_path)!r});site=root/'optional';site.mkdir()
raw=b'value=42\\n';(site/'verified_fixture.py').write_bytes(raw)
manifest=root/'manifest.json'
data=json.dumps([dict(name='verified_fixture.py',size=len(raw),sha256=hashlib.sha256(raw).hexdigest())]).encode()
manifest.write_bytes(data)
policy,origins=install_runtime(site,manifest,hashlib.sha256(data).hexdigest())
def audit(event,args):
    if event=='exec':policy.check_exec(args[0])
sys.addaudithook(audit)
import fractions
import verified_fixture
assert verified_fixture.value==42
for label in (str(root/'unverified.py'),str(Path(sys.base_prefix)/'Lib'/'fractions.py'),'relative_unverified.py'):
    code=compile('harmless_unverified_flag=True',label,'exec')
    try:exec(code,{{}})
    except PermissionError:pass
    else:raise AssertionError('Unauthenticated filesystem code was accepted: '+label)
(site/'unlisted_fixture.py').write_text('value=99')
try:__import__('unlisted_fixture')
except ImportError:pass
else:raise AssertionError('Unlisted optional source imported')
assert any(r['name']=='verified_fixture' for r in origins)
''')
    assert result.returncode==0,result.stderr
