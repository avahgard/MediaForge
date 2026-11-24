"""New Edge closure only: exact resolver/NativeGate, synthetic late insertions."""
import hashlib,json,os,shutil,sys
from pathlib import Path
import pytest
from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.features.dub.contracts import DubControl
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.models.providers import WorkerRuntime,LocalProviders
from mediaforge.infrastructure.models.dub_providers import SpeechProviders
from mediaforge.infrastructure.models.process import run_worker,CpuJob
from mediaforge.application.services.processing_gate import MediaProcessingGate

pytestmark=[pytest.mark.native,pytest.mark.integration]
ROOT=Path(__file__).resolve().parents[2]
CHILD='''
import importlib.util,sys,json,ctypes
from pathlib import Path
request=json.loads(Path(sys.argv[1]).read_bytes())
spec=importlib.util.spec_from_file_location('_resolver',request['entry'])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
policy,origins=module.install_runtime(request['site'],request['manifest'],request['digest'])
sys.addaudithook(lambda event,args:policy.check_exec(args[0]) if event=='exec' else None)
if request['mode']=='declared':
 names=['aiohttp','edge_tts','certifi','tabulate','typing_extensions','aiohappyeyeballs','aiosignal','attr','frozenlist','multidict','propcache','yarl','idna',
  'aiohttp._http_parser','aiohttp._http_writer','aiohttp._websocket.mask','aiohttp._websocket.reader_c','frozenlist._frozenlist',
  'multidict._multidict','multidict._testcapi','propcache._helpers_c','yarl._quoting_c']
 import importlib
 loaded={name:importlib.import_module(name).__file__ for name in names}
 print(json.dumps({'ok':True,'declared':loaded,'imports':origins}))
elif request['mode']=='python':
 import logging
 try:import aiohttp.poison
 except ModuleNotFoundError:denied=True
 else:denied=False
 print(json.dumps({'ok':True,'denied':denied,'logging':logging.__file__,'imports':origins}))
else:
 ctypes.WinDLL(request['late'])
 print(json.dumps({'ok':False,'detail':'unauthorized native image loaded'}))
'''

@pytest.mark.parametrize('mode',['declared','python','native'])
def test_late_unlisted_edge_code_refused_and_clean_recovery(tmp_path,mode,monkeypatch):
    value=os.environ.get('MEDIAFORGE_TEST_EDGE_ROOT')
    if not value:pytest.skip('Explicit staged Edge qualification runtime required.')
    source=Path(value);paths=ProfilePaths.for_test(tmp_path/'Portable Юникод/Data/v2');paths.prepare()
    shutil.copytree(source/'runtime',paths.root/'runtime')
    spec=json.loads((ROOT/'external/manifests/dub-edge-c3-development.json').read_text('utf-8'))
    manifest=paths.manifests/'runtime.json';manifest.write_bytes((source/'manifests/runtime.json').read_bytes())
    child=paths.temporary/'probe.py';child.write_text(CHILD,encoding='utf-8')
    runtime=WorkerRuntime(Path(sys._base_executable),paths.root/'runtime/site',manifest,spec['manifest_sha256'],child)
    from mediaforge.infrastructure.models import worker
    entry=Path(worker.__file__);request=paths.temporary/'request.json'
    late=runtime.site/'late-unlisted.pyd'
    request.write_text(json.dumps(dict(mode=mode,entry=str(entry),site=str(runtime.site),manifest=str(manifest),digest=runtime.manifest_sha256,late=str(late))),encoding='utf-8')
    providers=SpeechProviders(paths,runtime,MediaProcessingGate());control=DubControl();observed=[]
    close=CpuJob.close
    def inspect_close(owner):
        close(owner);observed.append(dict(exited=owner.native.exited,process_closed=owner.process._handle.closed,metrics=owner.metrics))
    monkeypatch.setattr(CpuJob,'close',inspect_close)
    with providers.session(control),runtime.acquire(paths,control) as guard,ReadLocks() as owned:
        owned.parents(entry.parent);owned.add(entry)
        original=(runtime.site/'aiohttp/helpers.py').read_bytes()
        with pytest.raises(OSError):(runtime.site/'aiohttp/helpers.py').write_bytes(b'raise RuntimeError("REPLACED")')
        assert (runtime.site/'aiohttp/helpers.py').read_bytes()==original
        def insert_after_auth(pid):
            if mode=='python':
                (runtime.site/'logging.py').write_text('raise RuntimeError("UNLISTED_EXECUTED")',encoding='utf-8')
                (runtime.site/'aiohttp/poison.py').write_text('raise RuntimeError("UNLISTED_EXECUTED")',encoding='utf-8')
            elif mode=='native':
                declared=next(runtime.site.glob('multidict/*.pyd'));shutil.copyfile(declared,late)
                assert hashlib.sha256(late.read_bytes()).digest()==hashlib.sha256(declared.read_bytes()).digest()
        args=[str(runtime.python),'-I','-S','-B',str(child),str(request)]
        if mode=='native':
            with pytest.raises(LocalAiError) as refused:
                run_worker(args,paths=paths,environment=LocalProviders.environment(providers),control=control,
                    timeout=20,runtime_guard=guard,on_started=insert_after_auth)
            assert refused.value.code=='runtime_integrity'
        else:
            result=run_worker(args,paths=paths,environment=LocalProviders.environment(providers),control=control,
                timeout=20,runtime_guard=guard,on_started=insert_after_auth)
            if mode=='python':assert result['denied'] and Path(result['logging']).is_relative_to(Path(sys._base_executable).parent/'Lib')
            else:
                assert len(result['declared'])==22
                assert all(Path(p).is_relative_to(runtime.site) for p in result['declared'].values())
                assert len([r for r in result['metrics']['native_images'] if Path(r['path']).is_relative_to(runtime.site)])==9
    assert observed and all(row['exited'] and row['process_closed'] for row in observed)
    assert not providers.gate.busy and not providers.gate.failure and not control.cleanup_failed
    receipt=paths.manifests/'probe-receipt.json';receipt.write_text(json.dumps(dict(mode=mode,retirement=observed,
        original_listed_sha256=hashlib.sha256(original).hexdigest(),result='declared_pass' if mode=='declared' else 'refused',gate_clean=True),indent=2),encoding='utf-8')
