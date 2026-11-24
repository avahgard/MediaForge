"""Final-only evidence extension; accepted C2/C3 entry bytes stay immutable."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import time

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.models.dub_runtime import DubWorkerRuntime,ENTRY_SHA256 as C3_SHA256,DELEGATE_SHA256
from mediaforge.infrastructure.models.providers import WorkerRuntime,RuntimeLease

ENTRY=Path(__file__).with_name('final_dub_worker.py')
ENTRY_SHA256 = '459c085219558edda20463a90d5a4a3e5e2250e759793a04c8269ae7d31b5d2d'


class FinalDubWorkerRuntime(DubWorkerRuntime):
    @contextmanager
    def acquire(self,paths,control=None):
        if self.entry!=ENTRY:raise LocalAiError('runtime_integrity','Final Dub entry differs from its fixed source.')
        started=time.monotonic()
        with ReadLocks() as owned:
            for path,expected in ((ENTRY,ENTRY_SHA256),(ENTRY.with_name('dub_worker.py'),C3_SHA256),
                                  (ENTRY.with_name('worker.py'),DELEGATE_SHA256)):
                if control:control.check()
                owned.parents(path.parent);owned.add(path)
                with path.open('rb') as stream:raw=stream.read(65537)
                if path.stat().st_nlink!=1 or len(raw)>65536 or hashlib.sha256(raw).hexdigest()!=expected:
                    raise LocalAiError('runtime_integrity','Final/accepted delegate source failed authentication.')
            # The original verifier/worker/NativeGate policy is unchanged.
            with WorkerRuntime.acquire(self,paths,control) as guard:
                yield RuntimeLease(guard.native_ids,time.monotonic()-started,(*guard.handles,*owned.handles))
