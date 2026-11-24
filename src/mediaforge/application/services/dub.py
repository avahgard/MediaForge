"""Complete explicit Dub command: inspect, synthesize, validate and publish.

Qt observes snapshots. One shared gate owner spans CPU, CUDA and export. A
checkpoint is a same-process capability, never a restart/resume instruction.
"""
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock, Thread
from uuid import uuid4
import time

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.application.contracts.export import output_filename
from mediaforge.application.contracts.speech import SpeechRequest
from mediaforge.features.dub.contracts import DubControl, OutputMode, AudioFormat
from mediaforge.features.dub.planning import output_plan, default_output_mode


@dataclass(frozen=True)
class DubState:
    generation: int = 0
    state: str = 'empty'
    stage: str = ''
    completed: int = 0
    total: int = 0
    code: str = ''
    message: str = ''
    destination: str = ''
    warnings: tuple = ()


class DubService:
    def __init__(self, inspector, speech, exporter, gate, voices, *, destination_planner=None):
        self.inspector,self.speech,self.exporter,self.gate=inspector,speech,exporter,gate
        self.voices=tuple(voices)
        self.destination_planner=destination_planner
        self._lock=RLock();self._thread=None;self._control=None;self._closed=False
        self._state=DubState();self._source=None;self._retry=None;self._retry_key=None
        self.retained=[];self.last_receipt=None
        self._started_at=None;self._elapsed=0.;self.timings={};self._phase_at=None;self._phase=''

    def elapsed(self):
        with self._lock:
            return time.monotonic()-self._started_at if self.busy and self._started_at is not None else self._elapsed

    def can_cancel(self):
        with self._lock:
            return bool(self.busy and self._control and not self._control.published and self._state.state!='cancelling')

    @property
    def busy(self):
        with self._lock:return self._thread is not None and self._thread.is_alive()

    def snapshot(self):
        with self._lock:return self._state

    def source(self):
        with self._lock:return self._source

    def clear_source(self, message=''):
        """Invalidate derived facts only; the workspace owns original selection."""
        with self._lock:
            if self.busy or self._closed:return False
            self._source=None;self._retry=None;self._retry_key=None;self.last_receipt=None
            self._state=DubState(self._state.generation+1, message=message)
            return True

    def options_changed(self):
        """Invalidate the displayed result/retry when idle selections change.

        Published files and owned checkpoint objects are untouched. Existing
        session ownership is retired by the next explicit Start or shutdown.
        """
        with self._lock:
            if self._closed or self.busy:return False
            self._retry=None;self._retry_key=None
            if self._source is not None and self._state.state in ('completed','failed','cancelled'):
                self._state=replace(self._state,state='ready',stage='',completed=0,total=0,
                    code='',message='Selections changed. Start a new Dub.',destination='',warnings=())
            return True

    def _start(self, stage, invoke, *, maintenance=False):
        with self._lock:
            if self._closed or self.busy or self.gate.failure:return False
            token=self.gate.acquire()
            if token is None:return False
            self._state=(replace(self._state,state='running',stage=stage) if maintenance
                         else DubState(self._state.generation+1,'running',stage))
            control=DubControl();self._control=control
            self._started_at=time.monotonic();self._phase_at=self._started_at;self._phase=stage;self.timings={}
            def work():
                try:
                    outcome=invoke(control,token)
                except Exception as error:
                    code=getattr(error,'code','dub_failed');code=str(getattr(code,'value',code))
                    message=str(error)
                    fit=(self.speech.last_receipt or {}).get('fit_candidate')
                    if code=='timing_unfit' and fit:
                        message+=(f" Segment {fit['segment']} at {fit['start_sample']/48000:.3f} s: "
                            f"generated speech {fit['neutral_samples']/48000:.3f} s, "
                            f"source slot {(fit['slot_end_sample']-fit['start_sample'])/48000:.3f} s. "
                            'Choose another built-in voice or a more slowly spoken source. No words or source pauses were removed.')
                    if code=='phrase_timing_unfit' and fit:
                        message+=' Complete Russian narration cannot fit the selected bounded tempo policy and qualified timing capacity. No speech was clipped or published.'
                    held=getattr(error,'retained_resources',())
                    if held or control.cleanup_failed:
                        self.retained.extend(held)
                        if not self.gate.failure:self.gate.poison(token,'Dub resources remain owned; restart is required.')
                    outcome=dict(state='cancelled' if code=='cancelled' else 'failed',code=code,message=message)
                finally:
                    if not self.gate.failure:self.gate.release(token)
                # No Completed state can be observed until every child/owner has
                # retired and the shared gate has been safely released.
                with self._lock:self._state=replace(self._state,**outcome)
                with self._lock:
                    now=time.monotonic();self._elapsed=now-self._started_at
                    self.timings[self._phase]=self.timings.get(self._phase,0.)+now-self._phase_at
            self._thread=Thread(target=work,name='MediaForge Dub',daemon=False)
            try:self._thread.start()
            except BaseException:
                self._thread=None;self.gate.release(token);raise
            return True

    def inspect(self,path):
        path=Path(path)
        def run(control,token):
            self.speech.sessions.close(DubControl())
            with self._lock:self._source=None;self._retry=None;self._retry_key=None
            source=self.inspector.inspect(path,control);control.check()
            with self._lock:self._source=source
            return dict(state='ready',stage='',message='Choose voice, output and destination folder.')
        return self._start('inspect',run)

    def plan(self,mode=None,format_='mp3',track=None):
        source=self.source()
        if source is None:raise LocalAiError('source_missing','Choose a source first.')
        return output_plan(source,OutputMode(mode) if mode else default_output_mode(source),
                           audio_index=track,audio_format=AudioFormat(format_))

    @staticmethod
    def output_name(voice, plan, filename=None):
        basename = filename or (plan.source.path.stem + '_RU_' + voice)
        return output_filename(basename, plan.route.suffix, require_matching_suffix=True)

    def destination(self,voice,folder,plan,filename=None):
        if self.destination_planner is not None:
            name=self.output_name(voice,plan,filename)
            return self.destination_planner(plan.source,plan.route,Path(folder),filename=name)
        if filename:raise LocalAiError('destination_unavailable','A protected filename planner is required.')
        return Path(folder)/self.output_name(voice,plan)

    def start(self,voice,folder,*,mode=None,format_='mp3',track=None,acknowledge=False,retry=False,filename=None):
        # Capture every choice before dispatch. No QML mutable state is read by
        # the worker, and Retry must match the exact frozen identity/options.
        if voice not in self.voices:raise LocalAiError('invalid_request','Choose a qualified built-in voice.')
        source=self.source();plan=self.plan(mode,format_,track)
        folder=Path(folder)
        if not folder.is_absolute() or not folder.is_dir():
            raise LocalAiError('destination_missing','Choose an existing writable local output folder.')
        destination=self.destination(voice,folder,plan,filename)
        key=(source.identity,plan,voice,str(destination),bool(acknowledge))
        if retry and (self._retry is None or self._retry_key!=key):
            raise LocalAiError('checkpoint_ineligible','Retry choices changed or private recovery expired. Use Start Dub.')
        if destination.exists():raise LocalAiError('destination_exists','Output already exists. Choose another folder; existing files are never overwritten.')
        if plan.requires_span_acknowledgment and not acknowledge:
            raise LocalAiError('span_acknowledgment','Confirm the explicitly displayed audio outside the video span.')
        request=SpeechRequest(source,plan,'final-'+uuid4().hex,voice=voice,
            acknowledge_clipped_span=bool(acknowledge),retain_private_recovery=True,
            retry_session=self._retry if retry else None)
        def run(control,token):
            if not retry:
                self.speech.sessions.close(DubControl())
                with self._lock:self._retry=None;self._retry_key=None
            try:
                prepared=self.speech.prepare(request,control,self._progress,gate_owner=token)
                result=self.exporter.execute(source,plan,prepared,destination,control,self._progress)
                self.speech.sessions.retire_if_present(prepared.session_id,DubControl())
                if self.gate.failure or control.cleanup_failed:
                    raise LocalAiError('lifecycle_failed','Output is published, but resource retirement failed. Do not report Completed.')
                with self._lock:
                    self._retry=None;self._retry_key=None
                    self.last_receipt=dict(speech=self.speech.last_receipt,export=self.exporter.last_receipt)
                return dict(state='completed',stage='',destination=str(result.output),warnings=prepared.warnings,
                            message='Validated Russian dub saved. Open the destination folder.')
            except BaseException:
                receipt=self.speech.last_receipt or {}
                with self._lock:
                    self._retry=receipt.get('session_id');self._retry_key=key
                raise
        return self._start('extract',run)

    def _progress(self,stage,done,total):
        with self._lock:
            now=time.monotonic()
            if self._phase_at is not None:
                self.timings[self._phase]=self.timings.get(self._phase,0.)+now-self._phase_at
            self._phase_at=now;self._phase=stage
            self._state=replace(self._state,stage=stage,completed=done,total=total)

    def can_retry(self):
        with self._lock:
            return bool(self._retry and not self.busy and not self.gate.failure
                        and self._state.state in ('failed','cancelled')
                        and self.speech.sessions.retry_eligible(self._retry))

    def expire_idle_sessions(self):
        """Existing UI tick dispatches bounded maintenance through our one worker.

        No timer thread/queue or independent cache owner. Another processing job
        takes priority; Retry eligibility still expires without acquiring a gate.
        Shutdown stops polling first, joins this worker and closes owned sessions.
        """
        with self._lock:
            if self._closed or self.busy or self.gate.busy or not self.speech.sessions.expiry_due():return False
            previous=self._state
            def run(control,token):
                retired=self.speech.sessions.expire_idle(control)
                with self._lock:
                    expired_retry=self._retry in retired
                    if expired_retry:self._retry=None;self._retry_key=None
                return dict(state=previous.state,stage=previous.stage,
                    message=(previous.message+' Private Retry expired; start a new Dub.').strip()
                            if expired_retry else previous.message)
            return self._start('retire_private',run,maintenance=True)

    def cancel(self):
        with self._lock:
            if self.busy and self._control.cancel():self._state=replace(self._state,state='cancelling')

    def shutdown(self):
        with self._lock:
            self._closed=True;self.cancel();thread=self._thread
        if thread is not None:
            thread.join(25)
            if thread.is_alive():
                self.gate.quarantine(self,'Dub worker did not retire at shutdown.')
                raise LocalAiError('lifecycle_failed','Dub worker still owns resources.',retained_resources=(self,))
        if self.gate.failure:
            raise LocalAiError('lifecycle_failed',self.gate.failure,retained_resources=(self,*self.retained))
        token=self.gate.acquire()
        if token is None:raise LocalAiError('busy','Retire other processing before closing private Dub sessions.')
        try:self.speech.sessions.close(DubControl())
        except BaseException:
            self.gate.poison(token,'Private Dub session retirement failed.');raise
        finally:
            if not self.gate.failure:self.gate.release(token)
