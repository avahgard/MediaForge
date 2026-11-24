"""One inventory, native-use lease and explicit mutation authority for local models."""
from contextlib import contextmanager
from threading import RLock

from mediaforge.application.contracts.models import ModelError,ModelSnapshot,ModelState


class ModelService:
    def __init__(self,specs,store,*,runtime_fingerprint,smoke=None):
        self._specs={s.model_id:s for s in specs}
        if len(self._specs)!=len(specs):raise ValueError('Duplicate model identities.')
        self._store=store;self._runtime=runtime_fingerprint;self._smoke=smoke
        self._lock=RLock();self._active={};self._verifying=set();self._retained={};self._mutating=set()
        self._closing=False;self._closed=False

    def _open(self):
        if self._closing or self._closed:
            raise ModelError('lifecycle_failed','Model service shutdown is in progress or complete.',
                retained_resources=tuple(o for owners in self._retained.values() for o in owners))

    def _spec(self,key):
        if key not in self._specs:raise ModelError('unknown_model','Unknown model identity.')
        return self._specs[key]

    def _precondition(self,spec):
        if not self._store.exists(spec):return ModelState.MISSING,'No installed revision.'
        if not spec.approved:return ModelState.UNQUALIFIED,'Exact member/runtime approval is incomplete.'
        if spec.runtime_fingerprint!=self._runtime:return ModelState.INCOMPATIBLE,'Runtime fingerprint differs.'
        if self._smoke is None:return ModelState.UNQUALIFIED,'No qualified native smoke adapter.'
        return None

    def snapshot(self,key):
        with self._lock:
            spec=self._spec(key)
            if key in self._retained:return ModelSnapshot(key,spec.revision,ModelState.IN_USE,'Unresolved retained ownership.')
            self._open()
            if key in self._active:return ModelSnapshot(key,spec.revision,ModelState.IN_USE,'Revision has an active lease.')
            if key in self._verifying or key in self._mutating:return ModelSnapshot(key,spec.revision,ModelState.VERIFYING,'Verification/install in progress.')
        try:
            problem=self._precondition(spec)
            if problem:return ModelSnapshot(key,spec.revision,*problem)
            # No cached Ready flag: every readiness query reauthenticates.
            with self.acquire(key):verified=self.spec(key)
            return ModelSnapshot(key,verified.revision,ModelState.READY,f'Fresh hash/native smoke passed ({verified.qualification} qualification).')
        except ModelError as error:
            if error.code in ('storage_refused','unsafe_model_path','io_failed') and not error.retained_resources:
                # Unavailable storage is not evidence of corrupt model bytes.
                raise
            state=(ModelState.IN_USE if error.retained_resources or error.code in ('in_use','busy') else
                   ModelState.MISSING if error.code=='incomplete' else
                   ModelState.INCOMPATIBLE if error.code in ('incompatible','runtime_integrity','native_protection_unavailable') else
                   ModelState.UNQUALIFIED if error.code in ('resource_unavailable','provider_timeout') else ModelState.CORRUPT)
            return ModelSnapshot(key,spec.revision,state,str(error))

    def list(self):return tuple(self.snapshot(key) for key in self._specs)

    def spec(self,key):
        with self._lock:return self._spec(key)

    def register(self,spec):
        """Trusted composition call; registration alone cannot claim installed Ready."""
        if not spec.approved or spec.runtime_fingerprint!=self._runtime:
            raise ModelError('unqualified','Manifest/runtime approval is incomplete.')
        with self._lock:
            self.assert_mutable(spec.model_id)
            self._specs[spec.model_id]=spec

    def provision(self,key,archive):
        """Explicit authenticated acquisition; never called by inventory/startup."""
        with self._lock:self._open()
        self._store.prepare()
        with self.mutation(key):
            spec=self.spec(key)
            if not spec.approved or spec.runtime_fingerprint!=self._runtime or self._smoke is None:
                raise ModelError('unqualified','Complete model, runtime and smoke approval required for acquisition.')
            source,receipt=self._store.download(archive)
            with self._store.stage(spec,source,archive_sha256=archive.sha256,
                                   archive_size=archive.size,prefix=archive.prefix) as guard:
                self._smoke(spec,guard)
        return receipt

    @contextmanager
    def acquire(self,key,*,control=None):
        guard=None;reservation=None
        with self._lock:
            self._open()
            spec=self._spec(key)
            if key in self._retained:raise ModelError('lifecycle_failed','Model resources are retained.',retained_resources=self._retained[key])
            if key in self._active or key in self._verifying or key in self._mutating:raise ModelError('in_use','Model revision is already in use.')
            problem=self._precondition(spec)
            if problem:raise ModelError(problem[0].value,problem[1])
            self._verifying.add(key)
        try:
            if hasattr(self._store,'reserve'):reservation=self._store.reserve(spec)
            guard=self._store.verify(spec) if control is None else self._store.verify(spec,control=control)
            try:
                if control is None:self._smoke(spec,guard)
                else:self._smoke(spec,guard,control=control)
            except ModelError as error:
                if error.code in ('provider_failed','runtime_unavailable','invalid_output') and not error.retained_resources:
                    raise ModelError('incompatible','Native model/runtime smoke failed: '+str(error)) from error
                raise
            except Exception as error:raise ModelError('incompatible','Native model smoke failed.') from error
            with self._lock:
                self._active[key]=guard
        except BaseException as error:
            with self._lock:
                if getattr(error,'retained_resources',()):
                    self._retained[key]=error.retained_resources
                self._release(key,(guard,reservation))
            raise
        finally:
            with self._lock:self._verifying.discard(key)
        try:yield guard
        finally:
            with self._lock:
                self._release(key,(guard,reservation))
                self._active.pop(key,None)

    def _close(self,key,guard):
        try:guard.close()
        except BaseException as error:
            self._retained[key]=self._retained.get(key,())+(guard,)
            raise ModelError('lifecycle_failed','Model lease could not be closed; ownership retained.',retained_resources=(guard,)) from error

    def _release(self,key,owners):
        failure=None
        for owner in owners:
            if owner is not None:
                try:self._close(key,owner)
                except ModelError as error:failure=error
        if failure is not None:
            raise ModelError('lifecycle_failed','Model resources remain retained.',retained_resources=self._retained[key]) from failure

    def assert_mutable(self,key):
        """Lease observation only, NOT an atomic update reservation.

        A future installer must implement its entire mutation through this service;
        checking here and then deleting by pathname would introduce a TOCTOU race.
        """
        with self._lock:
            self._open()
            self._spec(key)
            if key in self._active or key in self._verifying or key in self._retained or key in self._mutating:
                raise ModelError('in_use','Replacement/removal of a leased model is forbidden.')

    @contextmanager
    def mutation(self,key):
        """Reservation spans verification, activation and registry changes."""
        with self._lock:
            self.assert_mutable(key)
            previous=self._spec(key)
            self._mutating.add(key)
        reservation=None
        try:
            reservation=self._store.reserve(self._spec(key))
            if hasattr(self._store,'check_mutation'):self._store.check_mutation(previous)
            yield
        except BaseException as error:
            with self._lock:
                self._specs[key]=previous
                if getattr(error,'retained_resources',()):
                    owners=list(error.retained_resources)
                    if reservation is not None and all(o is not reservation for o in owners):owners.append(reservation)
                    self._retained[key]=tuple(owners)
                    error.retained_resources=self._retained[key]
                    reservation=None # ownership transferred, not released
            raise
        finally:
            try:
                if reservation is not None:self._close(key,reservation)
            except BaseException:
                with self._lock:self._specs[key]=previous
                raise
            finally:
                with self._lock:self._mutating.discard(key)

    def retire_pending(self,key):
        """Explicit serialized retirement; reservation closes LAST, only on success."""
        with self._lock:
            self._spec(key)
            if key in self._active or key in self._verifying or key in self._mutating:
                raise ModelError('in_use','Model operation is active; retirement is not admitted.')
            owners=list(self._retained.get(key,()));results=[]
            while owners:
                owner=owners[0]
                try:
                    operation=getattr(owner,'retire',None) or owner.close
                    result=operation()
                except BaseException as failure:
                    self._retained[key]=tuple(owners)
                    raise ModelError('lifecycle_failed','Explicit model retirement remains unresolved.',retained_resources=owners) from failure
                results.append(result);owners.pop(0)
                # Release the saved record only after confirmed retirement.
                self._retained[key]=tuple(owners)
            self._retained.pop(key,None)
            return tuple(results)

    def shutdown(self):
        """One explicit cleanup attempt. Failure keeps ownership and rejects new use."""
        with self._lock:
            if self._closed:return
            if self._active or self._verifying or self._mutating:
                raise ModelError('in_use','Active model operations must settle before shutdown.')
            self._closing=True
            for key in tuple(self._retained):self.retire_pending(key)
            self._closed=True

    def install(self,spec,source,**archive):
        """Explicit call with a trusted registry spec, never model-side approval."""
        with self._lock:self._open()
        if not spec.approved or spec.runtime_fingerprint!=self._runtime or self._smoke is None:
            raise ModelError('unqualified','Complete approved manifest, runtime and native smoke required.')
        # Storage adapter supplies the transaction; no provider-specific inventory.
        self._store.prepare()
        with self.mutation(spec.model_id):
            with self._store.stage(spec,source,**archive) as guard:
                self._smoke(spec,guard)
            with self._lock:self._specs[spec.model_id]=spec

    def remove(self,key):
        """Explicit logical removal to isolated quarantine; no recursive deletion."""
        with self.mutation(key):
            return self._store.retire(self._spec(key))
