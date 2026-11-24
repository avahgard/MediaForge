"""Separate Edge network entry, currently local TLS qualification only.

Production service admission is deliberately absent until independent dependency
review and controlled live-test permission. Never run the upstream retrying client.
The existing C2 import resolver and parent NativeGate still own optional code.
"""
import base64,hashlib,importlib.util,json,os,socket,ssl,sys,time
from pathlib import Path

OFFLINE_ENTRY_SHA256='991a3a8e2822e27b59ddf58dbfbdd071d6814ac09153fb503412590e0526bdc2'
MAX_AUDIO=2*1024**2  # bounded base64 remains below the unchanged 5 MiB IPC cap
VOICE='ru-RU-DmitryNeural'
def phase(name):sys.stderr.write('MF_PHASE '+name+'\n');sys.stderr.flush()

def headers(raw):
    if len(raw)>8192:raise ValueError('header bound')
    result={}
    for line in raw.split(b'\r\n'):
        if not line:continue
        key,value=line.split(b':',1)
        if key in result:raise ValueError('duplicate header')
        result[key]=value
    return result

async def network(request):
    phase('imports')
    import asyncio,aiohttp
    from xml.sax.saxutils import escape
    from edge_tts.communicate import mkssml,ssml_headers_plus_data,date_to_string,connect_id
    from edge_tts.data_classes import TTSConfig
    phase('network')
    started=time.monotonic();audio=bytearray();completed=False;attempts=0;frames=0;received=0;tls=None
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cafile=request['ca'])
    if context.verify_mode!=ssl.CERT_REQUIRED or not context.check_hostname:raise ValueError('TLS policy')
    trace=aiohttp.TraceConfig()
    async def sent(*args):
        nonlocal attempts
        attempts+=1
        if attempts>1:raise ValueError('physical request budget')
    async def redirected(*args):raise ValueError('redirect refused')
    trace.on_request_headers_sent.append(sent);trace.on_request_redirect.append(redirected)
    # No DNS resolver threads, proxy discovery, cookies, persistent-connection
    # retries, compression expansion, redirects or preflight voice-list request.
    connector=aiohttp.TCPConnector(limit=1,limit_per_host=1,force_close=True,
        family=socket.AF_INET,happy_eyeballs_delay=None)
    timeout=aiohttp.ClientTimeout(total=request['remaining'],connect=15,sock_connect=15,sock_read=request['remaining'])
    try:
        async with aiohttp.ClientSession(connector=connector,trust_env=False,timeout=timeout,
                cookie_jar=aiohttp.DummyCookieJar(),auto_decompress=False,trace_configs=[trace],read_bufsize=16384,
                max_line_size=8192,max_field_size=8192,max_headers=64) as session:
            async with session.ws_connect(request['endpoint'],ssl=context,proxy=None,compress=0,
                    autoping=False,max_msg_size=65536,headers={'User-Agent':'MediaForge-local-TLS-qualification'}) as ws:
                peer=ws.get_extra_info('ssl_object')
                if peer is None:raise ValueError('missing TLS connection')
                tls=dict(version=peer.version(),cipher=peer.cipher()[0],peer_sha256=hashlib.sha256(peer.getpeercert(binary_form=True)).hexdigest(),
                    certificate_required=True,hostname_checked=True)
                await ws.send_str('Path:speech.config\r\nContent-Type:application/json\r\n\r\n'+
                    json.dumps({'context':{'synthesis':{'audio':{'metadataoptions':{'sentenceBoundaryEnabled':'false','wordBoundaryEnabled':'false'},
                    'outputFormat':'audio-24khz-48kbitrate-mono-mp3'}}}}))
                config=TTSConfig(VOICE,'+0%','+0%','+0Hz','SentenceBoundary')
                await ws.send_str(ssml_headers_plus_data(connect_id(),date_to_string(),mkssml(config,escape(request['text']))))
                async for item in ws:
                    frames+=1
                    if frames>4096:raise ValueError('frame bound')
                    if isinstance(item.data,(str,bytes)):
                        received+=len(item.data.encode('utf-8') if isinstance(item.data,str) else item.data)
                    if received>4*1024**2:raise ValueError('aggregate response bound')
                    if item.type==aiohttp.WSMsgType.TEXT:
                        raw=item.data.encode('utf-8')
                        if len(raw)>8192:raise ValueError('metadata bound')
                        head,sep,body=raw.partition(b'\r\n\r\n')
                        if not sep:raise ValueError('metadata framing')
                        fields=headers(head)
                        if fields.get(b'Voice',VOICE.encode())!=VOICE.encode():raise ValueError('voice mismatch')
                        path=fields.get(b'Path')
                        if path==b'turn.end':completed=True;break
                        if path not in (b'turn.start',b'response',b'audio.metadata'):raise ValueError('metadata path')
                        if body and path==b'audio.metadata':
                            data=json.loads(body)
                            if isinstance(data,dict) and data.get('Voice',VOICE)!=VOICE:raise ValueError('voice mismatch')
                    elif item.type==aiohttp.WSMsgType.BINARY:
                        data=item.data
                        if len(data)<2:raise ValueError('binary framing')
                        n=int.from_bytes(data[:2],'big')
                        if n>8192 or n+2>len(data):raise ValueError('binary header bound')
                        fields=headers(data[2:2+n]);block=data[2+n:]
                        if fields.get(b'Path')!=b'audio' or fields.get(b'Content-Type')!=b'audio/mpeg' or not block:
                            raise ValueError('audio framing')
                        if fields.get(b'Voice',VOICE.encode())!=VOICE.encode():raise ValueError('voice mismatch')
                        if len(audio)+len(block)>MAX_AUDIO:raise ValueError('compressed audio bound')
                        audio.extend(block)
                    else:raise ValueError('unexpected websocket frame')
        if not completed or not audio:raise ValueError('incomplete synthesis')
        return dict(ok=True,audio=base64.b64encode(audio).decode('ascii'),physical_requests=attempts,
            audio_sha256=hashlib.sha256(audio).hexdigest(),network_seconds=time.monotonic()-started,tls=tls)
    except aiohttp.WSServerHandshakeError as error:
        retry=error.headers.get('Retry-After','0') if error.headers else '0'
        return dict(ok=True,status=error.status,retry_after=int(retry) if retry.isdecimal() else -1,physical_requests=attempts)
    except (asyncio.TimeoutError,aiohttp.ServerTimeoutError):
        return dict(ok=True,status='timeout',retry_after=0,physical_requests=attempts)

def main():
    phase('request')
    with Path(sys.argv[1]).open('rb') as f:raw=f.read(16385)
    if len(raw)>16384:raise ValueError('request bound')
    request=json.loads(raw);root=Path(request['root']).resolve()
    if (request.get('qualification') is not True or request.get('voice')!=VOICE or not isinstance(request.get('text'),str)
            or not 0<len(request['text'])<=500 or '\0' in request['text'] or not 0<request['remaining']<=90):
        raise ValueError('request admission')
    from urllib.parse import urlsplit
    endpoint=urlsplit(request['endpoint'])
    if (endpoint.scheme!='wss' or endpoint.hostname!='127.0.0.1' or endpoint.path!='/edge/v1'
            or endpoint.query or endpoint.fragment or endpoint.username or endpoint.password or not endpoint.port):
        raise ValueError('production origin not approved; qualification endpoint only')
    entry=Path(request['offline_entry'])
    if hashlib.sha256(entry.read_bytes()).hexdigest()!=OFFLINE_ENTRY_SHA256:raise ValueError('resolver identity')
    spec=importlib.util.spec_from_file_location('_mf_c2_import_contract',entry)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    policy,origins=module.install_runtime(request['site'],request['runtime_manifest'],request['runtime_digest'])
    phase('resolver')
    ca=Path(request['ca']).resolve()
    if not ca.is_relative_to(root) or hashlib.sha256(ca.read_bytes()).hexdigest()!=request['ca_sha256']:
        raise ValueError('trust store identity')
    # Trusted interpreter loop infrastructure may make its local wakeup socket.
    # Create that before auditing; parent already verified this job's consent.
    import asyncio
    phase('loop')
    loop=asyncio.new_event_loop();asyncio.set_event_loop(loop)
    phase('audit')
    violations=[]
    def audit(event,args):
        if event=='exec':policy.check_exec(args[0])
        if event in ('subprocess.Popen','os.system','os.spawn','os.exec','os.startfile'):raise PermissionError('nested child refused')
        if event in ('os.mkdir','os.remove','os.rmdir','os.rename','os.link','os.symlink','os.chmod','os.utime','os.truncate'):
            raise PermissionError('worker filesystem mutation refused')
        if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
            if args[2]&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):raise PermissionError('worker writes refused')
            target=Path(os.fsdecode(args[0])).resolve()
            if not (target.is_relative_to(root) or target.is_relative_to(Path(sys.base_prefix).resolve()) or target==entry):
                raise PermissionError('outside portable/trusted read refused')
        if event=='socket.getaddrinfo' and (args[0]!='127.0.0.1' or args[1]!=endpoint.port):
            violations.append('dns');raise PermissionError('DNS origin refused')
        if event=='socket.connect' and args[1]!=('127.0.0.1',endpoint.port):
            violations.append('connect');raise PermissionError('connection origin refused')
    sys.addaudithook(audit)
    try:
        result=loop.run_until_complete(network(request))
        result.update(python_modules=origins,violations=violations)
    except Exception as error:
        # Provider errors may echo SSML/token-bearing URLs: never forward them.
        result=dict(ok=False,detail='Edge qualification failed: '+type(error).__name__)
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens());loop.close()
    sys.stdout.write(json.dumps(result,separators=(',',':')));sys.stdout.flush()
    return 0 if result['ok'] else 2

if __name__=='__main__':raise SystemExit(main())
