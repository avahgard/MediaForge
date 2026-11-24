"""Short local voice previews: bounded memory, explicit generation and playback.

No files, model manager, media import or Completed Dub result. One resource owner
loads/retire its inference engine before publishing an in-memory preview value.
"""
from collections import OrderedDict
from dataclasses import dataclass, replace
import hashlib
import json
from threading import RLock, Thread
import time

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.application.services.optional_models import ModelsControl

TEXT='Здравствуйте! Это пример русской речи.'
MAX_SECONDS=8
MAX_ENTRIES=3
MAX_BYTES=2*1024**2


@dataclass(frozen=True)
class VoicePreviewState:
    generation:int=0
    state:str='idle'
    voice:str=''
    message:str=''
    code:str=''
    cached:bool=False


class VoicePreviewService:
    def __init__(self,engine_factory,binding,voices,gate):
        self.factory,self.binding,self.voices,self.gate=engine_factory,binding,tuple(voices),gate
        self._lock=RLock();self._state=VoicePreviewState();self._thread=None;self._control=None
        self._cache=OrderedDict();self._fragment=None;self._closed=False
        self.retained=[];self.last_statistics=None
        self._started_at=None;self._elapsed=0.

    def elapsed(self):
        with self._lock:
            return time.monotonic()-self._started_at if self.busy and self._started_at is not None else self._elapsed

    @property
    def busy(self):
        with self._lock:return self._thread is not None and self._thread.is_alive()

    def snapshot(self):
        with self._lock:return self._state

    def fragment(self):
        with self._lock:return self._fragment if self._state.state=='ready' and not self.busy else None

    def _key(self,voice):
        raw=json.dumps(dict(provider=self.binding(),voice=voice,text=TEXT,seconds=MAX_SECONDS),
                       sort_keys=True,separators=(',',':')).encode()
        return hashlib.sha256(raw).hexdigest()

    def _expire(self):
        now=time.monotonic()
        for key,(stamp,_) in list(self._cache.items()):
            if not 0<=now-stamp<86400:del self._cache[key]

    def start(self,voice):
        with self._lock:
            if self._closed or self.busy or self.gate.busy or self.gate.failure:return False
            if voice not in self.voices:raise LocalAiError('invalid_request','Select a qualified built-in voice.')
            key=self._key(voice);self._expire()
            generation=self._state.generation+1
            self._elapsed=0.
            self._fragment=None
            if key in self._cache:
                self._fragment=self._cache[key][1];self._cache.move_to_end(key)
                self._state=VoicePreviewState(generation,'ready',voice,'Local sample ready. Press Play.',cached=True)
                return True
            control=ModelsControl(lambda:LocalAiError('cancelled','Voice preview cancelled.'))
            self._control=control
            self._started_at=time.monotonic()
            self._state=VoicePreviewState(generation,'running',voice,
                'Verifying local resources and generating a short sample. First load can take about a minute.')
            def work():
                token=None;fragment=None;statistics=None
                try:
                    control.check();token=self.gate.acquire()
                    if token is None:raise LocalAiError('busy','Another Media/Dub/Models operation owns processing.')
                    with self.factory(control,token) as opened:
                        engine=opened[0] if isinstance(opened,tuple) else opened
                        fragment=engine.synthesize(TEXT,voice,seconds=MAX_SECONDS)
                        statistics=getattr(engine,'statistics',None)
                        if (fragment.speaker!=voice or fragment.sample_rate!=24000
                                or not isinstance(fragment.pcm16,bytes) or len(fragment.pcm16)%2
                                or not 0<len(fragment.pcm16)<=MAX_SECONDS*24000*2 or not any(fragment.pcm16)):
                            raise LocalAiError('invalid_output','Voice preview does not meet its bounded PCM contract.')
                    if self.gate.failure:raise LocalAiError('lifecycle_failed','Preview engine retirement failed.')
                    control.settle()
                    outcome=dict(state='ready',message='Local sample ready. Press Play.',code='')
                except Exception as error:
                    retained=getattr(error,'retained_resources',())
                    if retained or control.cleanup_failed:
                        self.retained.extend(retained)
                        if token is not None and not self.gate.failure:self.gate.poison(token,'Preview resources remain owned.')
                    code=str(getattr(error,'code','preview_failed'))
                    outcome=dict(state='cancelled' if code=='cancelled' else 'failed',code=code,message=str(error))
                finally:
                    if token is not None and not self.gate.failure:self.gate.release(token)
                with self._lock:
                    self._elapsed=time.monotonic()-self._started_at
                    self.last_statistics=statistics
                    if outcome['state']=='ready':
                        self._cache[key]=(time.monotonic(),fragment);self._cache.move_to_end(key)
                        while len(self._cache)>MAX_ENTRIES or sum(len(f.pcm16) for _,f in self._cache.values())>MAX_BYTES:
                            self._cache.popitem(last=False)
                        self._fragment=fragment
                    self._state=replace(self._state,**outcome)
            self._thread=Thread(target=work,name='MediaForge voice preview',daemon=False)
            self._thread.start();return True

    def cancel(self):
        with self._lock:
            if self.busy and self._control.cancel():self._state=replace(self._state,state='cancelling')

    def invalidate(self):
        with self._lock:
            if self.busy:
                self.cancel();return False
            self._cache.clear();self._fragment=None
            self._state=VoicePreviewState(self._state.generation+1)
            return True

    def shutdown(self):
        with self._lock:
            self._closed=True;self.cancel();thread=self._thread
        if thread is not None:
            thread.join(25)
            if thread.is_alive():
                self.gate.quarantine(self,'Voice preview worker did not retire.')
                raise LocalAiError('lifecycle_failed','Preview worker still owns resources.',retained_resources=(self,))
        self.invalidate()
        if self.gate.failure:
            raise LocalAiError('lifecycle_failed',self.gate.failure,retained_resources=(self,*self.retained))
