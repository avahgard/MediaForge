"""Job-scoped Dmitry contract. No implicit client install, network or fallback.

    A production transport is deliberately absent until its exact client/runtime
    closure and Windows ownership have been reviewed. Injectable transports are
    for contract qualification, never treated as live Microsoft authorization.
"""
from dataclasses import dataclass
import array
import re
import time

from mediaforge.application.contracts.local_ai import LocalAiError, PcmFragment

VOICE='ru-RU-DmitryNeural'

@dataclass(frozen=True)
class EdgeOptions:
    voice:str=VOICE
    connect_timeout:int=15
    absolute_timeout:int=90
    attempts:int=2
    max_response_bytes:int=4*1024**2

class EdgeTransient(LocalAiError):
    def __init__(self,status,retry_after=0):
        self.status=status;self.retry_after=retry_after
        super().__init__('online_transient','Classified online synthesis response.')

class EdgeDeadline:
    """The same parent deadline covers acquisition, retries AND audio validation."""
    def __init__(self,control,deadline):self.control=control;self.deadline=deadline
    def __getattr__(self,name):return getattr(self.control,name)
    def check(self):
        try:self.control.check()
        except Exception as error:
            if getattr(error,'code',None)=='cancelled':raise LocalAiError('cancelled','Dmitry synthesis cancelled.') from None
            raise
        if time.monotonic()>=self.deadline:raise LocalAiError('provider_timeout','Dmitry synthesis-unit deadline exceeded.')
    def starting(self):
        from contextlib import contextmanager
        @contextmanager
        def start():
            self.check()
            try:
                with self.control.starting():
                    # The captured spawn gate already checked cancellation while
                    # holding its non-reentrant mutex. Do not reacquire via check().
                    if time.monotonic()>=self.deadline:raise LocalAiError('provider_timeout','Dmitry spawn deadline exceeded.')
                    yield
            except Exception as error:
                if getattr(error,'code',None)=='cancelled':raise LocalAiError('cancelled','Dmitry synthesis cancelled.') from None
                raise
        return start()

class EdgeSpeech:
    def __init__(self,transport,decode,*,fingerprint):
        if not fingerprint:raise ValueError('An independently reviewed transport fingerprint is required.')
        self.transport=transport;self.decode=decode;self.fingerprint=fingerprint
    def synthesize(self,text,request,control,*,workspace=None):
        if (request.voice!='dmitry' or not isinstance(request.job_id,str) or not re.fullmatch('[A-Za-z0-9-]{1,80}',request.job_id)
                or request.online_consent_job!=request.job_id or request.online_text_transmission is not True):
            raise LocalAiError('online_consent_required','Explicit Dmitry text-transmission consent is required for this job.')
        if not isinstance(text,str) or not text.strip() or len(text)>500 or any(ord(c)<32 and c not in '\t\r\n' for c in text):
            raise LocalAiError('invalid_request','Invalid bounded Dmitry synthesis unit.')
        try:text.encode('utf-8')
        except UnicodeError:raise LocalAiError('invalid_request','Invalid synthesis text encoding.') from None
        control.check();options=EdgeOptions();deadline=time.monotonic()+options.absolute_timeout
        control=EdgeDeadline(control,deadline)
        for attempt in range(options.attempts):
            try:
                # Transport gets Russian synthesis text only, never the source,
                # English transcript, local path, cache key or parent environment.
                extra={} if workspace is None else dict(workspace=workspace)
                raw=self.transport.synthesize(text,options,control,deadline,**extra)
                control.check()
                if time.monotonic()>deadline:raise LocalAiError('provider_timeout','Dmitry request exceeded its absolute deadline.')
                if not isinstance(raw,bytes) or not 0<len(raw)<=options.max_response_bytes:
                    raise LocalAiError('invalid_output','Untrusted online audio exceeded its bound.')
                fragment=self.decode(raw,control,**extra)
                control.check()
                if (not isinstance(fragment,PcmFragment) or not isinstance(fragment.pcm16,bytes)
                        or len(fragment.pcm16)%2 or fragment.speaker!='dmitry'
                        or fragment.model_revision!=self.fingerprint or fragment.sample_rate!=22050
                        or not 0<fragment.samples<=120*22050):
                    raise LocalAiError('invalid_output','Decoded Dmitry audio is invalid.')
                if max(abs(n) for n in array.array('h',fragment.pcm16))<16:
                    raise LocalAiError('invalid_output','Silent decoded Dmitry audio refused.')
                return fragment
            except EdgeTransient as error:
                if error.status not in (429,500,502,503,504,'timeout') or attempt+1==options.attempts:
                    raise LocalAiError('online_unavailable','Dmitry service is unavailable; no provider fallback.') from None
                if not 0<=error.retry_after<=30:raise LocalAiError('online_unavailable','Retry-After exceeds the bounded policy.') from None
                until=time.monotonic()+error.retry_after
                while time.monotonic()<until:
                    control.check()
                    if time.monotonic()>deadline:raise LocalAiError('provider_timeout','Dmitry retry deadline exceeded.')
                    time.sleep(.05)
        raise LocalAiError('online_unavailable','Dmitry request failed.')
