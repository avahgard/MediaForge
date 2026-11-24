"""Exact-object install retirement. Pending records only deny trust after a crash."""
import ctypes as C
from ctypes import wintypes as W
import os

from mediaforge.application.contracts.models import ModelError
from mediaforge.infrastructure.models.owned import Ownership


def pending_path(store,spec):
    return store.paths.manifests/f'.install-pending-{spec.model_id}--{spec.revision}'


class Installation:
    """Own the original directory, parents and intent until confirmed retirement.

    No pathname deletion, ACL mutation, automatic retry or orphan adoption. A
    quarantined failed directory remains under downloads for explicit disposition.
    """
    def __init__(self,store,spec,directory):
        self.store=store;self.spec=spec;self.directory=directory;self.target=store.location(spec)
        self.owner=Ownership();self.handle=None;self.identity=None;self.marker=None;self.marker_identity=None
        self.marker_pending=False;self.guard=None;self.committed=False;self.quarantined=False;self.finished=False
        self.original_error=None;self.rollback_error=None

    @staticmethod
    def key(info):return info.volume,info.id_high,info.id_low

    def __enter__(self):
        try:
            self.owner.parents.parents(self.store.paths.downloads)
            self.owner.parents.parents(self.store.paths.models)
            self.owner.parents.parents(self.store.paths.manifests)
            self.marker=self.owner.open(pending_path(self.store,self.spec),0xC0010000,1,1)
            self.marker_identity=self.key(self.owner.info(self.marker))
            self.owner.require(self.owner.api.FlushFileBuffers(self.marker))
            self.directory.mkdir()
            self.handle=self.owner.open(self.directory,0x10080,3,3,directory=True)
            self.identity=self.key(self.owner.info(self.handle,directory=True))
            return self
        except BaseException as original:
            try:self.retire()
            except BaseException as failure:self.hold(original,failure)
            raise

    def where(self):
        if self.key(self.owner.info(self.handle,directory=True))!=self.identity:
            raise ModelError('lifecycle_failed','Install directory object identity differs.')
        api=self.owner.api.GetFinalPathNameByHandleW
        api.argtypes=[W.HANDLE,W.LPWSTR,W.DWORD,W.DWORD];api.restype=W.DWORD
        buffer=C.create_unicode_buffer(32768);size=api(self.handle,buffer,len(buffer),0)
        self.owner.require(size and size<len(buffer))
        name=buffer.value
        if name.startswith('\\\\?\\'):name=name[4:]
        return os.path.normcase(os.path.normpath(name))

    def activate(self):
        if self.where()!=os.path.normcase(str(self.directory)):
            raise ModelError('lifecycle_failed','Install directory escaped its original staging location.')
        self.target.parent.mkdir(exist_ok=True);self.owner.parents.add(self.target.parent,True)
        self.owner.rename(self.handle,self.target)

    def close_guard(self):
        if self.guard is not None:
            self.guard.close() # keep record until successful close
            self.guard=None

    def clear_marker(self):
        if self.marker is None:return
        info=self.owner.info(self.marker,links=0 if self.marker_pending else 1)
        if self.key(info)!=self.marker_identity:
            raise ModelError('lifecycle_failed','Install intent object identity differs.')
        if not self.marker_pending:
            self.owner.pending(self.marker,True);self.marker_pending=True
        self.owner.require(self.owner.api.CloseHandle(self.marker))
        self.owner.handles.remove(self.marker);self.marker=None

    def retire(self):
        """One explicit attempt against the held original object; never delete data."""
        if self.finished:return self.target if self.committed else self.directory
        self.close_guard()
        if not self.committed and not self.quarantined and self.handle is not None:
            current=self.where()
            if current==os.path.normcase(str(self.target)):
                self.owner.rename(self.handle,self.directory)
            elif current!=os.path.normcase(str(self.directory)):
                raise ModelError('lifecycle_failed','Install directory is at an unexpected native location.')
            if self.where()!=os.path.normcase(str(self.directory)):
                raise ModelError('lifecycle_failed','Install quarantine identity/location was not confirmed.')
            self.quarantined=True
        self.clear_marker()
        self.owner.close()
        self.finished=True
        return self.target if self.committed else self.directory

    def hold(self,original,secondary=None):
        self.original_error=original;self.rollback_error=secondary
        # Contained owners must retire THROUGH this controller, never before it:
        # closing their handles first would destroy exact-object authority.
        resources=tuple(r for r in getattr(original,'retained_resources',())
                        if r is not self and r is not self.owner and r is not self.guard)+(self,)
        message='Model installation resources retained; original: '+str(original)
        if secondary is not None:message+='; retirement: '+str(secondary)
        error=ModelError('lifecycle_failed',message,retained_resources=resources)
        error.installation_error=original;error.retirement_error=secondary
        raise error from secondary or original

    def __exit__(self,kind,original,tb):
        if original is not None:
            if getattr(original,'retained_resources',()):self.hold(original)
            try:self.retire()
            except BaseException as failure:self.hold(original,failure)
        else:
            try:
                self.close_guard()
                self.committed=True # bytes/config and native smoke finished, before commit intent removal
                self.retire()
            except BaseException as failure:self.hold(failure)
