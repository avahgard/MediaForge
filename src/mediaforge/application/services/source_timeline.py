"""One cancellable source scan; completed immutable timelines are session-local."""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from mediaforge.application.contracts.source_timeline import (
    SourceSignature, TimelineRequest, TimelineResult, TimelineBuildControl,
)


class SourceTimelineService:
    def __init__(self,indexer):
        self._indexer=indexer
        self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='source-timeline')
        self._lock=Lock()
        self._current=None
        self._current_request=None
        self._pending=None
        self._closed=False
        self._generation=0
        self._cache={}
        self._active_request=None
        self._cleanup_error=''

    def activate(self,item_id,path,completion):
        signature=SourceSignature.capture(path)
        with self._lock:
            if self._closed: return
            self._generation+=1
            request=TimelineRequest(item_id,signature,self._generation)
            self._active_request=request
            if self._cleanup_error:
                failure=TimelineResult(request,None,self._cleanup_error)
            else:
                failure=None
            cached=self._cache.get(item_id)
            if cached is not None and cached.request.source!=signature:
                del self._cache[item_id]
                cached=None
            if cached is None:
                cached=next((entry for entry in self._cache.values()
                             if entry.request.source==signature),None)
            if cached is None and self._current is not None and self._current_request.source!=signature:
                self._current.request_cancel()
            self._pending=(request,completion) if failure is None and cached is None else None
            if cached is not None:
                self._cache[item_id]=TimelineResult(request,cached.timeline)
        if failure is not None:
            completion(failure)
            return
        if cached is not None:
            completion(TimelineResult(request,cached.timeline))
        else:
            self._admit()

    def _admit(self):
        with self._lock:
            if self._closed or self._current is not None or self._pending is None: return
            request,completion=self._pending
            self._pending=None
            control=TimelineBuildControl()
            self._current=control
            self._current_request=request
            future=self._executor.submit(self._indexer.build,request,control)
        def finished(job):
            try: result=job.result()
            except Exception as error: result=TimelineResult(request,None,str(error))
            with self._lock:
                if control.owns_process:
                    self._cleanup_error=result.error or 'Frame scan child cleanup failed'
                    if self._pending is not None:
                        final_request,final_completion=self._pending
                        self._pending=None
                        result=TimelineResult(final_request,None,self._cleanup_error)
                    else:
                        final_request,final_completion=request,completion
                        result=TimelineResult(request,None,self._cleanup_error)
                else:
                    self._current=None
                    self._current_request=None
                    final_request, final_completion = request, completion
                if (not control.cancelled.is_set() and self._pending is not None
                        and self._pending[0].source==request.source and result.timeline is not None):
                    final_request,final_completion=self._pending
                    self._pending=None
                    result=TimelineResult(final_request,result.timeline)
                accepted=(not self._closed and final_request.generation==self._generation
                          and (bool(self._cleanup_error) or not control.cancelled.is_set()))
                if (not self._closed and not control.cancelled.is_set() and not self._cleanup_error
                        and result.timeline is not None):
                    self._cache[final_request.item_id]=result
            if accepted: final_completion(result)
            self._admit()
        future.add_done_callback(finished)

    def cancel(self, *, keep_scan=False):
        with self._lock:
            self._generation+=1
            self._active_request=None
            self._pending=None
            current=self._current
        if current is not None and not keep_scan: current.request_cancel()

    def forget(self,item_id):
        with self._lock:
            self._cache.pop(item_id,None)
            same_source_successor=(self._pending is not None and self._current_request is not None
                                   and self._pending[0].source==self._current_request.source
                                   and self._pending[0].item_id!=item_id)
            if self._current_request is not None and self._current_request.item_id == item_id and not same_source_successor:
                self._generation += 1
                self._current.request_cancel()
            if self._pending is not None and self._pending[0].item_id == item_id:
                self._pending = None

    def source_matches(self,item_id,path):
        try:
            signature=SourceSignature.capture(path)
        except (OSError,ValueError):
            return False
        with self._lock:
            active=self._active_request
            return ((active is not None and active.source==signature)
                    or any(retained.request.source==signature for retained in self._cache.values()))

    def shutdown(self):
        with self._lock: self._closed=True
        self.cancel()
        self._executor.shutdown(wait=True,cancel_futures=True)
        with self._lock: current=self._current
        if current is not None and current.owns_process:
            current.cancel()
            raise RuntimeError('Source timeline cleanup failed; child ownership retained')
