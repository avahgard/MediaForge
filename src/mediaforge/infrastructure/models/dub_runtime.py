"""C3-only source-bound worker selection. C2 runtime/inventory stays immutable."""
from contextlib import contextmanager
import hashlib,time
from pathlib import Path
from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.models.providers import WorkerRuntime,RuntimeLease

ENTRY=Path(__file__).with_name('dub_worker.py')
ENTRY_SHA256='3d38176d8942118da1780eeb20ac55de93c60852d50c27f0ad0f8e4cf98ce7ea'
DELEGATE_SHA256='991a3a8e2822e27b59ddf58dbfbdd071d6814ac09153fb503412590e0526bdc2'

class DubWorkerRuntime(WorkerRuntime):
    @contextmanager
    def acquire(self,paths,control=None):
        if self.entry!=ENTRY:raise LocalAiError('runtime_integrity','C3 entry is not the trusted fixed application entry.')
        started=time.monotonic()
        with ReadLocks() as owned:
            for path,expected in ((ENTRY,ENTRY_SHA256),(ENTRY.with_name('worker.py'),DELEGATE_SHA256)):
                if control:control.check()
                owned.parents(path.parent);owned.add(path)
                with path.open('rb') as stream:raw=stream.read(65537)
                if path.stat().st_nlink!=1 or len(raw)>65536 or hashlib.sha256(raw).hexdigest()!=expected:
                    raise LocalAiError('runtime_integrity','C3 worker/delegate source identity refused.')
            with super().acquire(paths,control) as guard:
                try:yield RuntimeLease(guard.native_ids,time.monotonic()-started,(*guard.handles,*owned.handles))
                except LocalAiError as error:
                    if (not error.retained_resources and error.code=='provider_failed'
                            and str(error) in ('native_platform_unavailable','runtime_integrity','speech_boundary_uncertain')):
                        raise LocalAiError(str(error),'C3 worker refused its qualified boundary.') from error
                    raise

def select_dub_runtime(runtime):
    return DubWorkerRuntime(runtime.python,runtime.site,runtime.manifest,runtime.manifest_sha256,ENTRY)
