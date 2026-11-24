"""Disabled production transport; authenticated local-TLS qualification adapter.

This module cannot contact Microsoft. Independent manifest/service approval and
explicit live-test authorization remain gates; Bootstrap never activates it.
Reuses the existing runtime, CPU Job, NativeGate and the caller's speech session.
"""
import base64,hashlib,json,time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from mediaforge.application.contracts.local_ai import LocalAiError,PcmFragment
from mediaforge.infrastructure.models.process import run_worker
from mediaforge.infrastructure.models.providers import LocalProviders
from mediaforge.infrastructure.models.edge_speech import EdgeTransient,VOICE
from mediaforge.infrastructure.filesystem.image_output import ReadLocks

MAX_COMPRESSED=2*1024**2

def validate_mp3(raw):
    """Exactly the requested MPEG-2 layer-III 24k mono 48kbit stream; no tags/junk."""
    if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_COMPRESSED:raise LocalAiError('invalid_output','Compressed speech size refused.')
    position=frames=0
    while position<len(raw):
        if position+4>len(raw):raise LocalAiError('invalid_output','Truncated MP3 frame.')
        h=int.from_bytes(raw[position:position+4],'big')
        if (h>>21!=0x7ff or (h>>19)&3!=2 or (h>>17)&3!=1 or (h>>12)&15!=6
                or (h>>10)&3!=1 or (h>>6)&3!=3 or h&3==2):
            raise LocalAiError('invalid_output','Unexpected MP3 format or extra data.')
        width=144+((h>>9)&1)
        if position+width>len(raw):raise LocalAiError('invalid_output','Incomplete MP3 data.')
        position+=width;frames+=1
        if frames*576>120*24000+1152:raise LocalAiError('invalid_output','MP3 duration exceeds synthesis bound.')
    if not frames:raise LocalAiError('invalid_output','Empty MP3 stream.')
    return frames

class EdgeNetworkQualification:
    """Trusted test composition only, not evidence of production/service approval."""
    def __init__(self,providers,runtime,audio,*,endpoint=None,ca=None,ca_sha256=None):
        self.providers=providers;self.runtime=runtime;self.audio=audio
        self.endpoint=endpoint;self.ca=Path(ca) if ca is not None else None;self.ca_sha256=ca_sha256;self.receipts=[];self.phases=[]
        try:spec=urlsplit(endpoint or '');port=spec.port
        except ValueError:raise LocalAiError('dependency_unavailable','Invalid fixed qualification origin.') from None
        if (spec.scheme!='wss' or spec.hostname!='127.0.0.1' or not port or spec.path!='/edge/v1'
                or spec.query or spec.fragment or spec.username or spec.password):
            raise LocalAiError('dependency_unavailable','Production Dmitry runtime/service admission is pending independent approval.')
        import re
        if (self.ca is None or not self.ca.is_absolute() or not self.ca.is_relative_to(providers.paths.root)
                or not isinstance(ca_sha256,str) or not re.fullmatch('[a-f0-9]{64}',ca_sha256)):
            raise LocalAiError('dependency_unavailable','An authenticated portable qualification trust store is required.')
        self.fingerprint=hashlib.sha256((runtime.manifest_sha256+hashlib.sha256(runtime.entry.read_bytes()).hexdigest()+
            ca_sha256+endpoint+'qualification-only-2mib-fixed-mp3-v1').encode()).hexdigest()

    def synthesize(self,text,options,control,deadline,*,workspace):
        if getattr(self.providers._owners,'token',None) is None:
            raise LocalAiError('busy','The shared processing owner is required before Edge admission.')
        if options.voice!=VOICE or not 0<len(text)<=500:raise LocalAiError('invalid_request','Invalid Edge unit.')
        control.check();request_path=None
        from mediaforge.infrastructure.models import worker
        entry=Path(worker.__file__)
        try:
            with self.runtime.acquire(self.providers.paths,control) as guard,ReadLocks() as locks:
                locks.parents(entry.parent);locks.add(entry)
                locks.parents(self.ca.parent);locks.add(self.ca)
                if hashlib.sha256(self.ca.read_bytes()).hexdigest()!=self.ca_sha256:
                    raise LocalAiError('incompatible','Qualification trust-store identity differs.')
                remaining=min(90,deadline-time.monotonic())
                if remaining<=0:raise LocalAiError('provider_timeout','Synthesis deadline exhausted before network admission.')
                payload=dict(qualification=True,voice=VOICE,text=text,root=str(self.providers.paths.root),site=str(self.runtime.site),
                    runtime_manifest=str(self.runtime.manifest),runtime_digest=self.runtime.manifest_sha256,
                    offline_entry=str(entry),endpoint=self.endpoint,ca=str(self.ca),ca_sha256=self.ca_sha256,remaining=remaining)
                encoded=json.dumps(payload,separators=(',',':')).encode()
                if len(encoded)>16384:raise LocalAiError('invalid_request','Edge request exceeds its bound.')
                request_path=workspace.put_bytes('edge-request-'+uuid4().hex+'.json',encoded)
                result=run_worker([str(self.runtime.python),'-I','-S','-B',str(self.runtime.entry),str(request_path)],
                    paths=self.providers.paths,environment=LocalProviders.environment(self.providers),control=control,
                    timeout=remaining,runtime_guard=guard,on_phase=self._phase)
                control.check()
                requests=result.get('physical_requests')
                if (type(requests) is not int or not 0<=requests<=1 or (requests==0 and result.get('status')!='timeout')
                        or result.get('violations')):
                    raise LocalAiError('invalid_output','Edge physical request/containment receipt refused.')
                self.receipts.append(dict(metrics=result['metrics'],runtime_verification_seconds=guard.verification_seconds,
                    physical_requests=result['physical_requests'],python_modules=result.get('python_modules'),status=result.get('status','audio'),tls=result.get('tls')))
                self.receipts[:]=self.receipts[-64:]
                if 'status' in result:raise EdgeTransient(result['status'],result.get('retry_after',-1))
                value=result.get('audio')
                if not isinstance(value,str) or len(value)>4*((MAX_COMPRESSED+2)//3):
                    raise LocalAiError('invalid_output','Edge encoded audio IPC exceeds its bound.')
                try:raw=base64.b64decode(value,validate=True)
                except ValueError:raise LocalAiError('invalid_output','Invalid Edge audio IPC.') from None
                if hashlib.sha256(raw).hexdigest()!=result.get('audio_sha256'):
                    raise LocalAiError('invalid_output','Edge audio receipt differs.')
                validate_mp3(raw);return raw
        finally:
            if request_path is not None and not control.cleanup_failed:
                try:workspace.discard(request_path)
                except BaseException as error:
                    control.retain_unreaped_child()
                    raise LocalAiError('lifecycle_failed','Edge request ownership remains unresolved.',retained_resources=(workspace,)) from error

    def _phase(self,name):
        if name not in ('request','resolver','loop','audit','imports','network'):
            raise LocalAiError('invalid_output','Unexpected Edge phase.')
        self.phases.append(name);self.phases[:]=self.phases[-32:]

    def decode(self,raw,control,*,workspace):
        validate_mp3(raw);path=workspace.put_bytes('edge-audio-'+uuid4().hex+'.mp3',raw);pcm=bytearray()
        try:
            self.audio.pipe(['-v','error','-err_detect','explode','-f','mp3','-i',str(path),'-map','0:a:0','-vn','-sn','-dn',
                '-ac','1','-ar','22050','-f','s16le','pipe:1'],control,consume=pcm.extend,maximum=120*22050*2,timeout=90,locks=(path,))
            control.check()
            return PcmFragment(bytes(pcm),22050,self.fingerprint,'dmitry')
        finally:
            if not control.cleanup_failed:workspace.discard(path)
