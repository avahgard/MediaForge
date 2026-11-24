"""Explicit isolated Edge dependency/TLS test; never contacts Microsoft."""
import base64,hashlib,json,os,socketserver,ssl,struct,sys,threading,time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.features.dub.contracts import DubControl
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest,FfmpegRuntimeResolver
from mediaforge.infrastructure.ffmpeg.speech import SpeechAudio
from mediaforge.infrastructure.models.providers import WorkerRuntime
from mediaforge.infrastructure.models.dub_providers import SpeechProviders
from mediaforge.infrastructure.models.edge_network import EdgeNetworkQualification,validate_mp3
from mediaforge.infrastructure.models.edge_speech import EdgeSpeech

pytestmark=[pytest.mark.native,pytest.mark.integration]
ROOT=Path(__file__).resolve().parents[2]

def read_exact(stream,count):
    output=bytearray()
    while len(output)<count:
        data=stream.read(count-len(output))
        if not data:raise EOFError()
        output.extend(data)
    return bytes(output)

def read_ws(stream):
    first,second=read_exact(stream,2);n=second&127
    if n==126:n=int.from_bytes(read_exact(stream,2),'big')
    if n==127:n=int.from_bytes(read_exact(stream,8),'big')
    if n>8192:raise ValueError('oversized fixture input')
    mask=read_exact(stream,4) if second&128 else None
    raw=read_exact(stream,n)
    return bytes(v^mask[i%4] for i,v in enumerate(raw)) if mask else raw

def write_ws(stream,data,opcode=1):
    n=len(data)
    head=bytes([0x80|opcode,n]) if n<126 else bytes([0x80|opcode,126])+n.to_bytes(2,'big')
    stream.write(head+data);stream.flush()

@contextmanager
def server(ca,key,mode,audio):
    seen=[]
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                line=self.rfile.readline(8192);headers={}
                for _ in range(64):
                    row=self.rfile.readline(8192)
                    if row==b'\r\n':break
                    name,_,value=row.partition(b':');headers[name.lower()]=value.strip()
                seen.append(dict(path=line.decode().strip(),messages=[]))
                if mode in ('403','429','500','redirect','429-long'):
                    status={'403':'403 Forbidden','429':'429 Too Many Requests','429-long':'429 Too Many Requests','500':'500 Server Error','redirect':'302 Found'}[mode]
                    retry='31' if mode=='429-long' else '0'
                    self.wfile.write(('HTTP/1.1 '+status+'\r\nContent-Length: 0\r\nRetry-After: '+retry+'\r\nLocation: https://example.invalid/forbidden\r\n\r\n').encode());self.wfile.flush();return
                accept=base64.b64encode(hashlib.sha1(headers[b'sec-websocket-key']+b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
                self.wfile.write(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: upgrade\r\nSec-WebSocket-Accept: '+accept+b'\r\n\r\n');self.wfile.flush()
                for _ in range(2):seen[-1]['messages'].append(read_ws(self.rfile))
                if mode=='stall':time.sleep(5);return
                if mode=='wrong-voice':write_ws(self.wfile,b'Path:response\r\nVoice:unapproved\r\n\r\n');return
                data=audio if mode!='malformed' else b'not an MP3'
                if mode=='oversized':data=b'x'*(2*1024**2+1)
                for start in range(0,len(data),60000):
                    header=b'Path:audio\r\nContent-Type:audio/mpeg\r\n'
                    write_ws(self.wfile,len(header).to_bytes(2,'big')+header+data[start:start+60000],2)
                if mode!='partial':write_ws(self.wfile,b'Path:turn.end\r\n\r\n')
            except (OSError,EOFError):pass # expected when parent refuses/reaps
    class Server(socketserver.TCPServer):
        allow_reuse_address=False
        def get_request(self):
            raw,address=super().get_request();raw.settimeout(2)
            try:return context.wrap_socket(raw,server_side=True),address
            except BaseException:raw.close();raise
    with Server(('127.0.0.1',0),Handler) as owned:
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(ca,key)
        thread=threading.Thread(target=owned.serve_forever,kwargs=dict(poll_interval=.05));thread.start()
        try:yield 'wss://127.0.0.1:'+str(owned.server_address[1])+'/edge/v1',seen
        finally:owned.shutdown();thread.join(5);assert not thread.is_alive()

@pytest.fixture(scope='module')
def native():
    value=os.environ.get('MEDIAFORGE_TEST_EDGE_ROOT');ff=os.environ.get('MEDIAFORGE_TEST_RUNTIME_ROOT')
    if not value or not ff:pytest.skip('Explicit isolated staged Edge runtime and pinned FFmpeg required.')
    paths=ProfilePaths.for_test(Path(value));paths.prepare()
    spec=json.loads((ROOT/'external/manifests/dub-edge-c3-development.json').read_text('utf-8'))
    runtime=WorkerRuntime(Path(sys._base_executable),paths.root/'runtime/site',paths.manifests/'runtime.json',spec['manifest_sha256'],
        ROOT/'src/mediaforge/infrastructure/models/edge_worker.py')
    ffmpeg=FfmpegRuntimeResolver(FfmpegManifest.load()).verify_directory(Path(ff));audio=SpeechAudio(paths,ffmpeg)
    providers=SpeechProviders(paths,runtime,MediaProcessingGate());store=SpeechSessions(paths)
    pcm=bytearray()
    audio.pipe(['-v','error','-f','lavfi','-i','sine=frequency=440:duration=0.3:sample_rate=24000','-ac','1','-c:a','libmp3lame',
        '-b:a','48k','-write_xing','0','-id3v2_version','0','-f','mp3','pipe:1'],DubControl(),consume=pcm.extend,maximum=65536,timeout=10)
    validate_mp3(bytes(pcm));ca=paths.root/'tls/ca.pem';key=paths.root/'tls/key.pem'
    assert ca.is_file() and key.is_file()
    evidence=[]
    yield paths,runtime,audio,providers,store,ca,key,bytes(pcm),evidence
    assert not providers.gate.busy and not providers.gate.failure
    store.close(DubControl())
    receipt=os.environ.get('MEDIAFORGE_TEST_EDGE_RECEIPT')
    if receipt:
        with Path(receipt).open('x',encoding='utf-8') as f:json.dump(evidence,f,indent=2)

@pytest.fixture(autouse=True)
def retire_current_test_only(native,monkeypatch,request):
    from mediaforge.infrastructure.models.process import CpuJob
    closed=[];original=CpuJob.close
    def observe(owner):
        original(owner)
        closed.append(dict(metrics=owner.metrics,exited=owner.native.exited,job_closed=owner.job is None,process_handle_closed=owner.process._handle.closed))
    monkeypatch.setattr(CpuJob,'close',observe)
    yield
    providers=native[3];store=native[4]
    assert not providers.gate.busy and not providers.gate.failure
    store.close(DubControl())
    assert all(r['exited'] and r['job_closed'] and r['process_handle_closed'] for r in closed)
    native[-1].append(dict(test=request.node.name,retired_workers=closed))

def run_case(native,mode,*,consent=True,cancel=False,timeout=False,bad_ca=False,crash=False):
    paths,runtime,audio,providers,store,ca,key,mp3,evidence=native
    c=DubControl();started=time.monotonic();errors=[]
    with server(str(ca),str(key),mode,mp3) as (endpoint,seen):
        authority=runtime.site/'certifi/cacert.pem' if bad_ca else ca
        transport=EdgeNetworkQualification(providers,runtime,audio,endpoint=endpoint,ca=authority,ca_sha256=hashlib.sha256(authority.read_bytes()).hexdigest())
        edge=EdgeSpeech(transport,transport.decode,fingerprint=transport.fingerprint)
        req=NS(voice='dmitry',job_id='new-job',online_consent_job='new-job' if consent else None,online_text_transmission=consent)
        with providers.session(c):
            with store.open(NS(retry_session=None,retain_private_recovery=False),{'test':'TLS'},c) as workspace:
                if cancel:
                    def cancel_after_received():
                        deadline=time.monotonic()+10
                        while time.monotonic()<deadline and not (seen and len(seen[-1]['messages'])==2):time.sleep(.01)
                        c.cancel()
                    timer=threading.Thread(target=cancel_after_received,daemon=True);timer.start()
                try:
                    if timeout:transport.synthesize('Синтетическая проверка.',NS(voice='ru-RU-DmitryNeural'),c,time.monotonic()+3.5,workspace=workspace)
                    else:result=edge.synthesize('Синтетическая проверка.',req,c,workspace=workspace)
                    assert mode=='audio' and result.samples>0 and result.sample_rate==22050
                    assert len(transport.receipts)==1
                except LocalAiError as error:
                    errors.append(dict(code=error.code,detail=str(error),phases=list(transport.phases)))
                    if mode=='audio' and consent and not bad_ca:raise
                    expected='cancelled' if cancel else 'provider_timeout' if timeout else 'online_consent_required' if not consent else None
                    if expected:assert error.code==expected
                finally:
                    if cancel:timer.join(11);assert not timer.is_alive()
        assert not providers.gate.busy and not providers.gate.failure and not c.cleanup_failed
        expected_requests=0 if not consent or bad_ca else 2 if mode in ('429','500') else 1
        if not consent:assert transport.phases==[] and transport.receipts==[]
        # A parent admission timeout may expire during authentication, before a request.
        if timeout or crash:assert len(seen)<=1
        else:assert len(seen)==expected_requests,errors
        for entry in seen:
            assert entry['path']=='GET /edge/v1 HTTP/1.1'
            for message in entry['messages']:
                assert str(paths.root).encode() not in message and b'new-job' not in message
            if len(entry['messages'])==2:
                from xml.etree import ElementTree as ET
                ssml=ET.fromstring(entry['messages'][1].split(b'\r\n\r\n',1)[1])
                voices=ssml.findall('{http://www.w3.org/2001/10/synthesis}voice')
                assert len(voices)==1 and voices[0].attrib['name']=='Microsoft Server Speech Text to Speech Voice (ru-RU, DmitryNeural)'
        evidence.append(dict(case=mode,consent=consent,cancel=cancel,timeout=timeout,physical_requests=len(seen),
            elapsed=time.monotonic()-started,workers=transport.receipts,errors=errors,gate_clean=True))
    store.close(DubControl())

def test_declared_runtime_local_tls_audio(native,monkeypatch):
    for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY'):monkeypatch.setenv(name,'http://127.0.0.1:1')
    monkeypatch.setenv('NETRC','C:/unapproved/netrc')
    from mediaforge.infrastructure.models.providers import LocalProviders
    env=LocalProviders.environment(native[3])
    assert not any(name in env for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NETRC'))
    run_case(native,'audio')
def test_consent_before_dns_network(native):run_case(native,'audio',consent=False)
@pytest.mark.parametrize('mode',['403','429','500','429-long','redirect','wrong-voice','partial','malformed','oversized'])
def test_hostile_response_no_hidden_retry(native,mode):run_case(native,mode)
def test_parent_timeout_and_retirement(native):run_case(native,'stall',timeout=True)
def test_cancel_and_retirement(native):run_case(native,'stall',cancel=True)
def test_untrusted_tls_chain_refused(native):run_case(native,'audio',bad_ca=True)

def test_native_encoded_silence_is_refused(native):
    """Actual MP3 framing, TLS transfer, managed decode, then PCM-energy refusal."""
    paths,runtime,audio,providers,store,ca,key,_,evidence=native
    encoded=bytearray()
    audio.pipe(['-v','error','-f','lavfi','-i','anullsrc=r=24000:cl=mono','-t','0.3','-c:a','libmp3lame',
        '-b:a','48k','-write_xing','0','-id3v2_version','0','-f','mp3','pipe:1'],DubControl(),
        consume=encoded.extend,maximum=65536,timeout=10)
    validate_mp3(bytes(encoded));c=DubControl()
    with server(str(ca),str(key),'audio',bytes(encoded)) as (endpoint,seen):
        transport=EdgeNetworkQualification(providers,runtime,audio,endpoint=endpoint,ca=ca,
            ca_sha256=hashlib.sha256(ca.read_bytes()).hexdigest())
        decoded=[]
        def decode(*args,**kwargs):
            import array
            fragment=transport.decode(*args,**kwargs)
            decoded.append(dict(samples=fragment.samples,maximum=max(abs(v) for v in array.array('h',fragment.pcm16))))
            return fragment
        edge=EdgeSpeech(transport,decode,fingerprint=transport.fingerprint)
        request=NS(voice='dmitry',job_id='silence-control',online_consent_job='silence-control',online_text_transmission=True)
        with providers.session(c),store.open(NS(retry_session=None,retain_private_recovery=False),{'test':'silence'},c) as workspace:
            with pytest.raises(LocalAiError,match='Silent decoded') as refused:edge.synthesize('Синтетическая проверка.',request,c,workspace=workspace)
        assert refused.value.code=='invalid_output' and len(seen)==len(transport.receipts)==1
        assert decoded and decoded[0]['samples']>0 and decoded[0]['maximum']==0
        evidence.append(dict(case='native-silent-mp3',mp3_sha256=hashlib.sha256(encoded).hexdigest(),decoded=decoded,
            error=refused.value.code,workers=transport.receipts,physical_requests=len(seen)))

def test_true_stalled_tls_handshake_connect_deadline(native):
    """TCP accepts a real ClientHello, sends no TLS bytes; no post-handshake stall."""
    paths,runtime,audio,providers,store,ca,key,_,evidence=native
    seen=[]
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            row=dict(accepted=time.monotonic());seen.append(row)
            self.request.settimeout(24)
            try:
                hello=self.request.recv(8192);row['client_hello_prefix']=hello[:6].hex();row['bytes']=len(hello)
                # A timeout is a harness failure; the client must close first.
                tail=self.request.recv(8192);row['client_closed']=not tail
            except OSError as error:row.update(client_closed=False,error=type(error).__name__)
            finally:row['elapsed']=time.monotonic()-row.pop('accepted')
    class Server(socketserver.ThreadingTCPServer):
        daemon_threads=False;block_on_close=True;allow_reuse_address=False
    c=DubControl();started=time.monotonic()
    with Server(('127.0.0.1',0),Handler) as owned:
        thread=threading.Thread(target=owned.serve_forever,kwargs=dict(poll_interval=.05));thread.start()
        try:
            endpoint='wss://127.0.0.1:'+str(owned.server_address[1])+'/edge/v1'
            transport=EdgeNetworkQualification(providers,runtime,audio,endpoint=endpoint,ca=ca,
                ca_sha256=hashlib.sha256(ca.read_bytes()).hexdigest())
            edge=EdgeSpeech(transport,transport.decode,fingerprint=transport.fingerprint)
            request=NS(voice='dmitry',job_id='handshake-control',online_consent_job='handshake-control',online_text_transmission=True)
            with providers.session(c),store.open(NS(retry_session=None,retain_private_recovery=False),{'test':'handshake'},c) as workspace:
                with pytest.raises(LocalAiError) as refused:edge.synthesize('Синтетическая проверка.',request,c,workspace=workspace)
            assert refused.value.code=='online_unavailable'
        finally:owned.shutdown();thread.join(5);assert not thread.is_alive()
    assert len(seen)==len(transport.receipts)==2
    assert all(row['bytes']>5 and row['client_hello_prefix'].startswith('1603') and row['client_closed'] for row in seen)
    assert all(14<=row['elapsed']<=20 for row in seen),seen
    assert all(row['status']=='timeout' and row['physical_requests']==0 for row in transport.receipts)
    assert time.monotonic()-started<90 and not c.cleanup_failed
    evidence.append(dict(case='stalled-tls-handshake',connections=seen,http_requests=0,error=refused.value.code,
        elapsed=time.monotonic()-started,workers=transport.receipts))

def test_forced_owned_worker_death(native,monkeypatch):
    import signal
    from mediaforge.infrastructure.models import edge_network
    original=edge_network.run_worker;owned_pid=[];killed=[]
    def observed(command,**options):
        phase=options['on_phase']
        def kill_at_network(name):
            phase(name)
            if name=='network':
                os.kill(owned_pid[0],signal.SIGTERM);killed.append(owned_pid[0])
        options.update(on_started=lambda pid:owned_pid.append(pid),on_phase=kill_at_network)
        return original(command,**options)
    monkeypatch.setattr(edge_network,'run_worker',observed)
    run_case(native,'stall',crash=True)
    assert killed and killed==owned_pid

def test_offline_piper_uses_same_gate_after_edge_refusal(native,monkeypatch):
    value=os.environ.get('MEDIAFORGE_TEST_AI_ROOT')
    if not value:pytest.skip('Explicit authenticated offline model root required for same-gate recovery.')
    gate=native[3].gate;tokens=[];original_acquire=gate.acquire
    def acquire():
        token=original_acquire()
        if token is not None:tokens.append(token)
        return token
    monkeypatch.setattr(gate,'acquire',acquire)
    run_case(native,'403')
    from mediaforge.bootstrap.speech import compose_dub_local_ai
    from mediaforge.infrastructure.models.registry import development_registry
    from mediaforge.application.contracts.local_ai import SynthesisRequest
    from mediaforge.features.media.processing_control import ProcessingControl
    paths=ProfilePaths.for_test(Path(value))
    specs=development_registry(ROOT/'external/manifests/dub-c2-development.json',
        'e95373ca537ac2139eb47ce37113122d45eeb679c26fa416d39a830322f81e2a',allow_development=True)
    runtime=WorkerRuntime(Path(sys._base_executable),paths.root/'runtime/site',paths.manifests/'runtime.json',specs[0].runtime_fingerprint)
    local=compose_dub_local_ai(paths,runtime,gate,specs=specs)
    fragment=local.providers.synthesize(SynthesisRequest('Это отдельная проверка локального голоса.'),
        ProcessingControl(lambda:LocalAiError('cancelled','Test cancelled.')))
    assert fragment.samples>22050 and fragment.sample_rate==22050 and fragment.speaker=='ruslan'
    import array
    independent=array.array('h',fragment.pcm16)
    assert len(independent)==fragment.samples and max(abs(v) for v in independent)>1000
    assert len(tokens)==2 and tokens[0] is not tokens[1] # new owner after full Edge retirement
    assert not gate.busy and not gate.failure
    native[-1].append(dict(case='explicit-offline-piper-after-edge-403',same_gate=True,new_owner=True,samples=fragment.samples,
        independently_decoded_peak=max(abs(v) for v in independent),
        pcm_sha256=hashlib.sha256(fragment.pcm16).hexdigest(),workers=local.providers.receipts))
