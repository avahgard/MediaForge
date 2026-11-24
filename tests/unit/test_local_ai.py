"""Input bounds, portable runtime, download and process ownership faults."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import io
import json
import sys
import urllib.request

import pytest

from mediaforge.application.contracts.local_ai import *
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.features.media.processing_control import ProcessingControl
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.models.providers import WorkerRuntime,LocalProviders
from mediaforge.infrastructure.models.download import ArchiveSource,acquire
from mediaforge.infrastructure.models.process import run_worker


@pytest.mark.parametrize('factory',[lambda:RecognitionRequest(b'X'),lambda:RecognitionRequest(b'\0'*960002),lambda:RecognitionRequest(b'',-1),lambda:TranslationRequest('x'*4001),lambda:SynthesisRequest(''),lambda:SynthesisRequest('x'*501)])
def test_inputs_are_bounded(factory):
    with pytest.raises(ValueError):factory()


@pytest.mark.parametrize('url',['http://publisher/model','file:///model','https://user:password@publisher/model','https://publisher:8443/model'])
def test_unsafe_download_sources_refuse(url):
    with pytest.raises(ValueError):ArchiveSource(url,'a'*64,1)


@pytest.mark.native
@pytest.mark.parametrize('bad',['size','digest','interrupted','good'])
def test_download_is_owned_and_authenticated(tmp_path,monkeypatch,bad):
    paths=ProfilePaths.for_test(tmp_path/'Data/v2');paths.prepare()
    class Response(io.BytesIO):
        status=200;url='https://publisher.example/model'
        headers={'Content-Length':'3' if bad!='size' else '4'}
        def read(self,count=-1):
            if bad=='interrupted':raise OSError('Disconnected')
            return super().read(count)
    response=Response(b'abc')
    monkeypatch.setattr(urllib.request,'build_opener',lambda *a:SimpleNamespace(open=lambda *a,**k:response))
    source=ArchiveSource(response.url,hashlib.sha256(b'abc').hexdigest() if bad!='digest' else '0'*64,3)
    if bad=='good':
        path,receipt=acquire(paths,source)
        assert path.read_bytes()==b'abc' and receipt.sha256==source.sha256
    else:
        with pytest.raises(ModelError):acquire(paths,source)
        assert not list(paths.downloads.iterdir())


@pytest.mark.native
def test_runtime_is_authenticated_and_cancellable(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'Data/v2');paths.prepare()
    site=paths.root/'runtime/site';site.mkdir(parents=True);(site/'member.py').write_bytes(b'abc')
    manifest=paths.manifests/'runtime.json'
    rows=[dict(name='member.py',size=3,sha256=hashlib.sha256(b'abc').hexdigest())]
    manifest.write_text(json.dumps(rows))
    runtime=WorkerRuntime(Path(sys._base_executable),site,manifest,hashlib.sha256(manifest.read_bytes()).hexdigest())
    with runtime.acquire(paths):
        with pytest.raises(PermissionError):(site/'member.py').write_bytes(b'xyz')
    (site/'member.py').write_bytes(b'xyz')
    with pytest.raises(LocalAiError):
        with runtime.acquire(paths):pass
    c=ProcessingControl(lambda:LocalAiError('cancelled','cancel'));c.cancel()
    with pytest.raises(LocalAiError,match='cancel'):
        with runtime.acquire(paths,c):pass


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('fault',['crash','timeout','resource','cancel'])
def test_worker_failure_reaps_and_refuses(tmp_path,fault):
    paths=ProfilePaths.for_test(tmp_path/'Data/v2');paths.prepare()
    c=ProcessingControl(lambda:LocalAiError('cancelled','cancel'))
    code='import os;os._exit(23)' if fault=='crash' else 'import time;time.sleep(30)'
    options=dict(timeout=.2 if fault=='timeout' else 5,limit=1 if fault=='resource' else 2*1024**3)
    if fault=='cancel':options['on_started']=lambda pid:c.cancel()
    with pytest.raises(LocalAiError) as error:
        run_worker([sys._base_executable,'-I','-S','-B','-c',code],paths=paths,environment=paths.child_environment(),control=c,**options)
    assert error.value.code=={'crash':'provider_failed','timeout':'provider_timeout','resource':'resource_unavailable','cancel':'cancelled'}[fault]
    assert not c.cleanup_failed


def test_provider_environment_does_not_mutate_parent(tmp_path,monkeypatch):
    monkeypatch.setenv('HF_HOME','outside');monkeypatch.setenv('SYNTHETIC_API_SECRET','never-persist-this')
    paths=ProfilePaths.for_test(tmp_path/'Data/v2')
    providers=LocalProviders(paths,None,MediaProcessingGate());env=providers.environment()
    assert Path(env['HF_HOME']).is_relative_to(paths.root) and env['HF_HUB_OFFLINE']=='1'
    import os
    assert os.environ['HF_HOME']=='outside'
    assert 'SYNTHETIC_API_SECRET' not in env


@pytest.mark.native
def test_request_never_serializes_parent_environment(tmp_path,monkeypatch):
    from contextlib import contextmanager
    import mediaforge.infrastructure.models.providers as module
    monkeypatch.setenv('SYNTHETIC_API_SECRET','never-persist-this')
    paths=ProfilePaths.for_test(tmp_path/'Data/v2')
    class Runtime:
        site=paths.root/'runtime/site';python=Path(sys._base_executable);entry=tmp_path/'entry.py'
        @contextmanager
        def acquire(self,*args):yield
    providers=LocalProviders(paths,Runtime(),MediaProcessingGate())
    def transport(command,**options):
        raw=Path(command[-1]).read_bytes();data=json.loads(raw)
        assert b'never-persist-this' not in raw and 'environment' not in data
        assert 'SYNTHETIC_API_SECRET' not in options['environment']
        return dict(metrics={},load_seconds=0,inference_seconds=0,writes=[],violations=[])
    monkeypatch.setattr(module,'run_worker',transport)
    providers._run(SimpleNamespace(model_id='synthetic',revision='v1'),SimpleNamespace(path=paths.models),'translate',{'text':'test'},ProcessingControl(lambda:LocalAiError('cancelled','cancel')))


def test_shared_gate_spans_model_verification_and_final_lease_release(tmp_path,monkeypatch):
    from contextlib import contextmanager
    gate=MediaProcessingGate();providers=LocalProviders(ProfilePaths.for_test(tmp_path/'Data/v2'),None,gate)
    observed=[]
    class Models:
        @contextmanager
        def acquire(self,*a,**kw):
            observed.append(gate.acquire() is None)
            yield object()
            observed.append(gate.acquire() is None)
        def spec(self,key):return SimpleNamespace(revision='v1')
    providers.models=Models()
    def run(*a,**kw):
        assert gate.busy and providers._owners.token is not None
        return {'spans':[]}
    monkeypatch.setattr(providers,'_run',run)
    providers.translate(TranslationRequest(''),ProcessingControl(lambda:LocalAiError('cancelled','cancel')))
    assert observed==[True,True] and not gate.busy


def test_clean_composition_without_models_is_lazy(tmp_path):
    from mediaforge.bootstrap.local_ai import compose_local_ai
    from mediaforge.application.contracts.models import ModelState
    paths=ProfilePaths.for_test(tmp_path/'uncreated/Data/v2')
    runtime=WorkerRuntime(Path(sys._base_executable),paths.root/'runtime/site',paths.manifests/'missing.json','0'*64)
    services=compose_local_ai(paths,runtime,MediaProcessingGate())
    assert all(row.state==ModelState.MISSING for row in services.models.list())
    assert not paths.root.exists()
