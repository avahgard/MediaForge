"""C3 job-wide admission using accepted C2 workers, inventory and result parsers."""
from contextlib import contextmanager, ExitStack
import hashlib
import sys

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.infrastructure.models.providers import LocalProviders
import base64

class SpeechProviders(LocalProviders):
    reuse_runtime = False
    @property
    def session_owner(self):
        token=getattr(self._owners,'token',None)
        if token is None or not self.gate.owned_by(token):
            raise LocalAiError('busy','An owning speech session is required.')
        return token

    def activity(self,pcm16,offset,control,**options):
        from mediaforge.features.dub.alignment import VAD_SHA256
        if not isinstance(pcm16,bytes) or len(pcm16)%2 or not 3072<=len(pcm16)<=960000 or type(offset) is not int or offset<0:
            raise LocalAiError('speech_boundary_uncertain','Invalid bounded activity window.')
        row,_=self._selected('whisper-base','c3_activity',dict(pcm16=base64.b64encode(pcm16).decode()),control,**options)
        observations=row.get('observations')
        if row.get('asset_sha256')!=VAD_SHA256 or not isinstance(observations,list) or len(observations)!=2:
            raise LocalAiError('speech_boundary_uncertain','VAD identity/schema differs.')
        result=[]
        for grid,item in zip((0,256),observations):
            if item.get('grid')!=grid or len(item.get('scores',()))!=(len(pcm16)//2-grid)//512:
                raise LocalAiError('speech_boundary_uncertain','VAD grid/window facts differ.')
            result.append(dict(offset=offset+grid,scores=item['scores']))
        return result
    @contextmanager
    def selected_models(self,keys,control):
        if getattr(self._owners,'token',None) is None or getattr(self._owners,'guards',None) is not None:
            raise LocalAiError('busy','Selected model leases require exactly one owning speech job.')
        self._owners.guards={}
        try:
            with ExitStack() as leases:
                for key in keys:
                    with self.observe():guard=leases.enter_context(self.models.acquire(key,control=control))
                    self._owners.guards[key]=(self.models.spec(key),guard)
                yield {key:pair[0] for key,pair in self._owners.guards.items()}
        finally:del self._owners.guards
    @contextmanager
    def observe(self):
        marker=self.receipts[-1] if self.receipts else None
        try:yield
        finally:
            start=next((i+1 for i,row in enumerate(self.receipts) if row is marker),0)
            for row in self.receipts[start:]:
                metrics=row['metrics'];stats=self.statistics
                stats['successful_workers']+=1
                for name in ('commit_peak','rss_peak','threads_peak'):
                    stats[name]=max(stats[name],metrics.get(name,0))
                stats['cpu_seconds']+=metrics.get('cpu_seconds',0)
                stats['runtime_verification_seconds']+=row.get('runtime_verification_seconds',0)
                stats['load_seconds']+=row.get('load_seconds',0)
                stats['inference_seconds']+=row.get('inference_seconds',0)
    def binding(self):
        from mediaforge.infrastructure.models.dub_runtime import DELEGATE_SHA256
        return dict(runtime_manifest=self.runtime.manifest_sha256,
            worker_sha256=hashlib.sha256(self.runtime.entry.read_bytes()).hexdigest(),delegate_sha256=DELEGATE_SHA256,python=sys.version)
    @classmethod
    def from_local(cls,local):
        old=local.providers
        result=cls(old.paths,old.runtime,old.gate,timeout=old.timeout)
        result.models=local.models
        # ModelService's accepted smoke callback still targets the original C2
        # adapter. Share its thread-local owner and bounded metric list, so smoke
        # and real inference participate in this SAME gate session.
        result._owners=old._owners;result.receipts=old.receipts
        return result

    @contextmanager
    def session(self,control,*,owner=None):
        if getattr(self._owners,'token',None) is not None:
            raise LocalAiError('busy','Nested speech jobs are not supported.')
        borrowed=owner is not None
        if borrowed and not self.gate.owned_by(owner):
            raise LocalAiError('busy','The current processing token must be explicitly lent to speech.')
        token=owner if borrowed else self.gate.acquire()
        if token is None:raise LocalAiError('busy','Another Media or Dub job owns processing.')
        self._owners.token=token
        self.statistics=dict(successful_workers=0,commit_peak=0,rss_peak=0,threads_peak=0,
            cpu_seconds=0.,runtime_verification_seconds=0.,load_seconds=0.,inference_seconds=0.)
        try:
            control.check()
            if self.reuse_runtime:
                # Authenticate once and KEEP every deny-write/delete handle for
                # the operation. Each child retains its own CpuJob/NativeGate.
                # No stat/hash cache and no lease survives this owner thread.
                with self.runtime.acquire(self.paths,control) as runtime_guard:
                    self.statistics['runtime_verification_seconds']+=runtime_guard.verification_seconds
                    self._owners.runtime_guard=(self.runtime,runtime_guard)
                    try:yield
                    finally:del self._owners.runtime_guard
            else:yield
        except BaseException as error:
            if getattr(error,'retained_resources',()) or control.cleanup_failed:
                if not self.gate.failure:self.gate.poison(token,'Dub resources remain owned.')
            raise
        finally:
            del self._owners.token
            if not borrowed and not self.gate.failure:self.gate.release(token)

    def _selected(self,key,action,payload,control,**options):
        if getattr(self._owners,'token',None) is None:
            return super()._selected(key,action,payload,control,**options)
        guards=getattr(self._owners,'guards',None)
        if guards is not None:
            if key not in guards:raise LocalAiError('model_missing','This model was not selected for the frozen job.')
            spec,guard=guards[key]
            if self.models.spec(key)!=spec:raise LocalAiError('incompatible','Frozen model revision changed.')
            with self.observe():return self._run(spec,guard,action,payload,control,**options),spec
        # _run sees the existing thread-local owner. Its original NativeGate,
        # runtime verification, model leases and unresolved cleanup stay intact.
        with self.observe():
            with self.models.acquire(key,control=control) as guard:
                spec=self.models.spec(key)
                return self._run(spec,guard,action,payload,control,**options),spec
