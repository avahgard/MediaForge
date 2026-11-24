"""Fresh result-path eligibility; completed publication is not import validation."""
import stat
from threading import RLock,Event
from contextlib import contextmanager
from pathlib import Path
from mediaforge.features.media.export import ExportControl,ExportError


class ResultControl(ExportControl):
    def __init__(self,current,cancel):
        super().__init__();self.current=current;self.external_cancel=cancel

    def check(self):
        if self.external_cancel is not None and self.external_cancel.is_set():self.cancel()
        super().check()
        if not self.current():raise ExportError('result_stale','The completed result job changed; the current Viewer is preserved.')


class ResultLease:
    """One existing gate permit and its held result locks, across worker→GUI."""
    def __init__(self,service,owner,control):
        self.service=service;self.owner=owner;self.control=control;self.native=None;self.closed=False;self.lock=RLock();self.ready=Event()

    def check(self):
        if self.closed:raise ExportError('result_stale','Result preparation was cancelled or closed.')
        self.control.check()

    @contextmanager
    def activating(self):
        # Result-job changes/cancel admission cannot cross the GUI commit.
        with self.service._lock:
            self.check()
            yield

    def close(self):
        with self.lock:
            if self.closed:return
            try:
                if self.native is not None:self.native.close()
                if self.control.cleanup_failed:raise RuntimeError('Result child/resource ownership is unresolved.')
            except BaseException:
                self.service._processing_gate.poison(self.owner,'export_result_cleanup_failed')
                self.service._result_failures.append(self)
                raise
            self.service._processing_gate.release(self.owner)
            self.closed=True
            with self.service._lock:self.service._result_preparations.discard(self)


class GuardedSource:
    def __init__(self,source,admission):self.source=source;self.admission=admission
    def check(self):self.admission.check()
    def activating(self):return self.admission.activating()
    def __exit__(self,*args):
        try:self.source.__exit__(*args)
        except BaseException as error:
            self.admission.control.retain_resources((self.source,error))
            self.admission.close();raise
        self.admission.close()


def result_path(result):
    path=Path(result.path)
    if (not path.is_absolute() or str(path).startswith(('\\\\','//'))
            or ':' in str(path)[2:] or '..' in path.parts):
        raise ValueError('Exported result requires an ordinary absolute local path.')
    for part in (path,*path.parents):
        info=part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise ValueError('Exported result paths containing reparse points are unavailable.')
    info=path.stat()
    if not (stat.S_ISDIR(info.st_mode) if result.kind=='sequence' else stat.S_ISREG(info.st_mode)):
        raise ValueError('Exported result is no longer the expected file or directory.')
    return path
