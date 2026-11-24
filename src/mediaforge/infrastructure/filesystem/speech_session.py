"""Private C3 session checkpoints. Explicit retry only; never adopt crash orphans."""
from contextlib import contextmanager
import hashlib
import hmac
import io
import json
import secrets
import shutil
from threading import RLock
import time
from uuid import uuid4

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.models.owned import Ownership, ModelMutex
from mediaforge.infrastructure.filesystem.speech_directory import create_directory
from mediaforge.infrastructure.ffmpeg.speech import PcmWriter, blocks, digest, trim_silence

def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')

class SpeechSessions:
    """At most one recovery capability and one active job per process.

    Keys deliberately stay in memory; v1 never adopts previous-process objects.
    A root OS reservation serializes application allocations across instances.
    All existing dub-* candidates consume quota, without granting trust/deletion.
    """
    def __init__(self,paths,*,wall_clock=None,monotonic_clock=None):
        self.paths=paths;self._tickets={};self._lock=RLock();self._reservation_depth=0
        self._wall_clock=wall_clock or (lambda:time.time())
        self._monotonic_clock=monotonic_clock or (lambda:time.monotonic())
    MAX_BYTES=1024**3
    MAX_JOB_SLOTS=3
    MAX_FILES=16000
    MAX_METADATA=32*1024**2
    INFERENCE_RESERVATION=2*1024**2+65536
    @contextmanager
    def reservation(self):
        # Hold for the complete active job, not merely a detached quota scan.
        # No persistent secret/security descriptor; OS releases on process death.
        if self._reservation_depth:
            self._reservation_depth+=1
            try:yield
            finally:self._reservation_depth-=1
            return
        self.paths.prepare()
        with ModelMutex(self.paths,'dub-speech-store'):
            import ctypes as C
            from ctypes import wintypes as W
            api=C.WinDLL('kernel32',use_last_error=True)
            api.GetDiskFreeSpaceW.argtypes=[W.LPCWSTR,*([C.POINTER(W.DWORD)]*4)];api.GetDiskFreeSpaceW.restype=W.BOOL
            values=[W.DWORD() for _ in range(4)]
            if not api.GetDiskFreeSpaceW(self.paths.root.anchor,*(C.byref(v) for v in values)):
                raise LocalAiError('storage_quota_unknown','Portable allocation unit is unavailable.')
            self.allocation_unit=values[0].value*values[1].value
            if not 512<=self.allocation_unit<=65536:
                raise LocalAiError('storage_quota_unknown','Portable allocation unit is unqualified.')
            self._reservation_depth=1
            try:yield
            finally:self._reservation_depth=0
    def charge(self,size):
        unit=self.allocation_unit
        return max(unit,((size+unit-1)//unit)*unit)
    def eligible(self,ticket,now):
        return (not ticket.get('expired',False) and 0<=now-ticket['created']<86400
                and 0<=self._monotonic_clock()-ticket['created_monotonic']<86400)
    def retry_eligible(self,name):
        # UI polling must not wait behind a native worker's store reservation.
        if not self._lock.acquire(blocking=False):return False
        try:
            ticket=self._tickets.get(name)
            return bool(ticket and not ticket['active'] and self.eligible(ticket,self._wall_clock()))
        finally:self._lock.release()
    def expiry_due(self):
        if not self._lock.acquire(blocking=False):return False
        try:
            return any(not t['active'] and not self.eligible(t,self._wall_clock()) for t in self._tickets.values())
        finally:self._lock.release()
    def expire_idle(self,control):
        """Called by the existing Dub worker/gate, never adopt prior-process data.

        Expiry is irreversible in this process, including a failed exact-object
        retirement or a later clock adjustment. Keep the ticket/native owners
        on failure so the caller can quarantine them instead of releasing a gate.
        """
        with self._lock:
            expired=tuple(name for name,t in self._tickets.items()
                          if not t['active'] and not self.eligible(t,self._wall_clock()))
            if not expired:return ()
            for name in expired:self._tickets[name]['expired']=True
            try:
                with self.reservation():
                    for name in expired:
                        control.check();self._retire(name,control)
            except BaseException as error:
                control.retain_unreaped_child()
                raise LocalAiError('lifecycle_failed','Expired private session retirement remains owned.',
                    retained_resources=(self,*getattr(error,'retained_resources',()))) from error
            return expired
    def accounting(self,control):
        """Bounded no-follow discovery. Observation is never ownership authority."""
        import os
        total=metadata=files=jobs=entries=directories=0
        with Ownership() as owner:
            owner.parents.parents(self.paths.sessions)
            with os.scandir(self.paths.sessions) as roots:
                for root in roots:
                    control.check();entries+=1
                    if entries>4096:
                        raise LocalAiError('storage_quota_unknown','Too many session entries; Dub allocation refused.')
                    if not root.name.startswith('dub-'):continue
                    jobs+=1;total+=self.charge(0)
                    directories+=1
                    if jobs>self.MAX_JOB_SLOTS:raise LocalAiError('disk_budget','Prior Dub remnants exhaust portable job slots; no automatic deletion.')
                    path=self.paths.sessions/root.name
                    directory=owner.open(path,0x80,3,3,directory=True)
                    # Flat application-generated stages only. A nested/reparse/
                    # unreadable object is unknown capacity, never traversed.
                    with os.scandir(path) as members:
                        for member in members:
                            control.check();files+=1
                            if files>self.MAX_FILES:raise LocalAiError('disk_budget','Aggregate Dub object quota exhausted.')
                            handle=owner.open(path/member.name,0x80000000,1,3)
                            info=owner.info(handle)
                            size=(info.high<<32)|info.low
                            total+=self.charge(size)
                            if member.name.endswith('.json'):metadata+=size
                            owner.require(owner.api.CloseHandle(handle));owner.handles.remove(handle)
                    owner.require(owner.api.CloseHandle(directory));owner.handles.remove(directory)
            # The accepted shared C2 worker writes one bounded request in tmp/
            # inference-*. A crash can leave it before a Dub session exists.
            # Account these shared model-worker remnants too; never adopt or
            # delete them. They are units, not additional Dub session tickets.
            owner.parents.parents(self.paths.temporary)
            with os.scandir(self.paths.temporary) as roots:
                for root in roots:
                    control.check();entries+=1
                    if entries>4096:raise LocalAiError('storage_quota_unknown','Temporary discovery exceeds its bound.')
                    if not root.name.startswith('inference-'):continue
                    directories+=1;total+=self.charge(0)
                    path=self.paths.temporary/root.name
                    directory=owner.open(path,0x80,3,3,directory=True)
                    with os.scandir(path) as members:
                        for member in members:
                            control.check();files+=1
                            if files+directories>self.MAX_FILES:raise LocalAiError('disk_budget','Aggregate speech/model temporary object quota exhausted.')
                            handle=owner.open(path/member.name,0x80000000,1,3)
                            info=owner.info(handle);size=(info.high<<32)|info.low
                            total+=self.charge(size)
                            if member.name.endswith('.json'):metadata+=size
                            owner.require(owner.api.CloseHandle(handle));owner.handles.remove(handle)
                    owner.require(owner.api.CloseHandle(directory));owner.handles.remove(directory)
        if total>self.MAX_BYTES or metadata>self.MAX_METADATA or files+directories>self.MAX_FILES:
            raise LocalAiError('disk_budget','Aggregate Dub bytes/metadata quota exhausted; existing objects preserved.')
        return dict(bytes=total,metadata=metadata,files=files,jobs=jobs,directories=directories)
    @contextmanager
    def admission(self,control,*,workspace_bytes=0,retry_session=None):
        """Reserve/account before even the first ModelService smoke child."""
        with self._lock,self.reservation():
            facts=self.accounting(control)
            reusing=False
            if retry_session is not None:
                ticket=self._tickets.get(retry_session)
                if ticket is None or not self.eligible(ticket,self._wall_clock()):
                    raise LocalAiError('checkpoint_ineligible','Retry requires this process\'s live capability.')
                if ticket['active']:raise LocalAiError('busy','Retry checkpoint is already active.')
                info=ticket['directory_owner'].info(ticket['directory_handle'],directory=True)
                if (info.volume,info.id_high,info.id_low)!=ticket['directory_identity']:
                    raise LocalAiError('checkpoint_ineligible','Retry directory object differs.')
                reusing=True # no third directory; full binding is rechecked by _open
            if facts['jobs']-int(reusing)>=self.MAX_JOB_SLOTS:
                raise LocalAiError('disk_budget','Prior unfinished Dub work occupies all portable job slots; existing objects were preserved.')
            if facts['bytes']+self.INFERENCE_RESERVATION+workspace_bytes>=self.MAX_BYTES:
                raise LocalAiError('disk_budget','Portable speech capacity is unavailable before model admission.')
            if shutil.disk_usage(self.paths.root).free<workspace_bytes+self.INFERENCE_RESERVATION+max(512*1024**2,(facts['bytes']+workspace_bytes)//10):
                raise LocalAiError('disk_budget','Insufficient portable disk headroom before model admission.')
            yield
    def retire_if_present(self,name,control):
        with self._lock:
            if name in self._tickets:self.retire(name,control)
    def close(self,control):
        """Explicit shutdown retires only this process's capabilities."""
        with self._lock,self.reservation():
            if any(t['active'] for t in self._tickets.values()):
                raise LocalAiError('lifecycle_failed','Speech work is active; settle its owner before shutdown.',retained_resources=(self,))
            for name in list(self._tickets):self._retire(name,control)
    @contextmanager
    def open(self,request,binding,control):
        # Keep the Python mutex and OS reservation through all writes/settlement.
        with self._lock,self.reservation():
            with self._open(request,binding,control) as session:yield session
    @contextmanager
    def _open(self,request,binding,control):
        now=self._wall_clock();blob=encoded(binding)
        with self._lock:
            for expired,t in list(self._tickets.items()):
                if not t['active'] and not self.eligible(t,now):
                    self.retire(expired,control)
            if request.retry_session:
                ticket=self._tickets.get(request.retry_session)
                if ticket is None or ticket['binding']!=blob or not self.eligible(ticket,now):
                    raise LocalAiError('checkpoint_ineligible','Recovery identity/expiry is invalid; start a new explicit job.')
                if ticket['active']:raise LocalAiError('busy','Recovery session is in use.')
                name=request.retry_session
            else:
                if self.accounting(control)['jobs']>=self.MAX_JOB_SLOTS:raise LocalAiError('disk_budget','Prior Dub work occupies all portable job slots.')
                self.paths.prepare();name='dub-'+uuid4().hex
                ticket=dict(key=secrets.token_bytes(32),binding=blob,created=now,created_monotonic=self._monotonic_clock(),active=False,files={})
                ticket['directory_owner'],ticket['directory_handle']=create_directory(self.paths.sessions/name)
                info=ticket['directory_owner'].info(ticket['directory_handle'],directory=True)
                ticket['directory_identity']=(info.volume,info.id_high,info.id_low)
                self._tickets[name]=ticket
            ticket['active']=True
        session=Session(self.paths.sessions/name,ticket,control,store=self)
        failed=False
        try:
            session.owner.parents.parents(session.path)
            session.budget=self.accounting(control)
            session.admit(0);yield session
        except BaseException:
            failed=True;raise
        finally:
            try:session.close()
            except BaseException:
                control.retain_unreaped_child();raise
            finally:ticket['active']=False
            if failed and not request.retain_private_recovery and not control.cleanup_failed:
                try:self.retire(name,control)
                except BaseException as error:
                    control.retain_unreaped_child()
                    raise LocalAiError('lifecycle_failed','Private session cleanup remains owned.',
                        retained_resources=(self,session.path)) from error

    def retire(self,name,control):
        # open() already owns this reservation on the same manager/thread.
        # RLock ownership alone does not imply an OS reservation outside open().
        with self._lock:
            with self.reservation():return self._retire(name,control)
    def _retire(self,name,control):
        """Delete only exact, tracked objects from THIS manager, never unknown files."""
        with self._lock:
            ticket=self._tickets.get(name)
            if ticket is None or ticket['active']:raise LocalAiError('busy','Session cannot be retired.')
            path=self.paths.sessions/name
            with Ownership() as owner:
                owner.parents.parents(path.parent)
                # Retire the exact directory object, never rmdir a pathname
                # after releasing the directory's protection against replacement.
                directory_owner=ticket['directory_owner']
                # Keep an object-identity pin alive while upgrading the lifetime
                # deny-delete list handle to DELETE access. This prevents FileID
                # reuse; a replacement in the upgrade gap is refused below.
                if 'identity_pin' not in ticket:
                    pin=Ownership()
                    try:
                        pin.parents.parents(path.parent);handle=pin.open(path,0x80,7,3,directory=True)
                        info=pin.info(handle,directory=True)
                        if (info.volume,info.id_high,info.id_low)!=ticket['directory_identity']:
                            raise LocalAiError('lifecycle_failed','Speech directory identity changed.',retained_resources=(path,))
                    except BaseException:pin.close();raise
                    ticket['identity_pin']=pin
                if ticket['directory_handle'] is not None:
                    handle=ticket['directory_handle']
                    directory_owner.require(directory_owner.api.CloseHandle(handle));directory_owner.handles.remove(handle)
                    ticket['directory_handle']=None
                directory=owner.open(path,0x10080,3,3,directory=True)
                info=owner.info(directory,directory=True)
                if (info.volume,info.id_high,info.id_low)!=ticket['directory_identity']:
                    raise LocalAiError('lifecycle_failed','Directory replacement refused at retirement.',retained_resources=(path,ticket['identity_pin']))
                if {p.name for p in path.iterdir()}!=set(ticket['files']):
                    raise LocalAiError('lifecycle_failed','Unknown private session member; cleanup refused.',retained_resources=(path,))
                for filename,row in list(ticket['files'].items()):
                    file=path/filename;handle=owner.open(file,0x80010000,1,3)
                    info=owner.info(handle)
                    key=(info.volume,info.id_high,info.id_low)
                    if tuple(row['identity'])!=key:
                        raise LocalAiError('lifecycle_failed','Session object changed; cleanup refused.',retained_resources=(path,))
                    owner.pending(handle,True)
                    # A delete-pending child still occupies the namespace until
                    # its last owned handle closes. Retire children before asking
                    # Windows to delete the now-empty directory object.
                    owner.require(owner.api.CloseHandle(handle));owner.handles.remove(handle)
                    del ticket['files'][filename]
                owner.pending(directory,True)
                directory_owner.close()
            ticket['identity_pin'].close()
            del self._tickets[name]

class Session:
    def __init__(self,path,ticket,control,*,store=None):
        self.path=path;self.ticket=ticket;self.control=control;self.owner=Ownership();self.hits=0;self._leases={};self._readers=[];self.store=store;self._reserved={}
    def own_reader(self,reader):
        if len(self._readers)>=4:raise LocalAiError('resource_unavailable','Too many active speech readers.')
        self._readers.append(reader);return reader
    def close(self):
        # A paused recognition generator owns a Python file handle too. Retire
        # it before exact-object cleanup; traceback/GC lifetime is not ownership.
        for reader in reversed(self._readers):reader.close()
        self._readers.clear()
        for lease in self._leases.values():lease.close()
        self._leases.clear();self.owner.close()
    def admit(self,extra):
        total=sum(row['bytes'] for row in self.ticket['files'].values())+sum(self._reserved.values())
        if total+extra>1024**3 or len(self.ticket['files'])>16000:
            raise LocalAiError('disk_budget','Bounded private session quota exhausted.')
        if self.store is not None:
            # Reconstruct once per OS-reserved operation; charge each committed
            # object below. No quadratic full-store scan per streamed segment.
            facts=self.budget
            if (facts['bytes']+self.store.INFERENCE_RESERVATION+sum(self.store.charge(n) for n in self._reserved.values())+self.store.charge(extra)>self.store.MAX_BYTES
                    or facts['files']+facts['directories']+len(self._reserved)>=self.store.MAX_FILES):
                raise LocalAiError('disk_budget','Aggregate portable Dub allocation quota exhausted.')
        margin=max(512*1024**2,(total+extra)//10)
        if shutil.disk_usage(self.path).free<extra+margin:
            raise LocalAiError('disk_budget','Insufficient portable disk headroom.')
    def name(self,stem):
        # Only application-generated stage names, never paths from checkpoint JSON.
        reserved={'con','prn','aux','nul',*(f'com{i}' for i in range(1,10)),*(f'lpt{i}' for i in range(1,10))}
        if (not stem or stem.endswith('.') or stem.split('.')[0] in reserved or len(stem)>100
                or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-.' for c in stem)):
            raise LocalAiError('checkpoint_ineligible','Unsafe stage name.')
        return self.path/stem
    def admit_records(self,count,metadata_bytes):
        """Conservative future-stage admission, not a quota increase/reservation."""
        if type(count) is not int or type(metadata_bytes) is not int or count<0 or metadata_bytes<0:
            raise LocalAiError('disk_budget','Invalid stage-record forecast.')
        self.admit(metadata_bytes)
        facts=self.budget if self.store is not None else dict(files=len(self.ticket['files']),directories=1,
            metadata=sum(r['bytes'] for n,r in self.ticket['files'].items() if n.endswith('.json')))
        if facts['files']+facts.get('directories',0)+count>16000 or facts['metadata']+metadata_bytes>32*1024**2:
            raise LocalAiError('disk_budget','Tutorial journal forecast exceeds existing file/metadata quotas.')
    def track(self,path):
        if path.name in self.ticket['files']:
            if path.name not in self._leases:self.protect(path)
            return
        lease=ReadLocks();self._leases[path.name]=lease
        lease.parents(path.parent);lease.add(path)
        info=lease.api
        from mediaforge.infrastructure.models.native_guard import identity
        key,opened=identity(info,lease.handles[-1])
        if opened.links!=1:raise LocalAiError('checkpoint_ineligible','Aliased stage object refused.')
        # Record exact ownership BEFORE cancellable hashing. Cancellation here
        # must not turn a newly owned stage into an untracked cleanup orphan.
        row=dict(bytes=path.stat().st_size,sha256=None,identity=key)
        self.ticket['files'][path.name]=row
        if self.store is not None:
            self.budget['bytes']+=self.store.charge(row['bytes'])
            self.budget['files']+=1
            if path.name.endswith('.json'):self.budget['metadata']+=row['bytes']
        row['sha256']=digest(path,self.control)
    def put_bytes(self,name,raw):
        if name.endswith('.json') and (self.budget['metadata'] if self.store is not None else sum(row['bytes'] for key,row in self.ticket['files'].items() if key.endswith('.json')))+len(raw)>32*1024**2:
            raise LocalAiError('disk_budget','Speech metadata exceeds 32 MiB.')
        self.control.check();self.admit(len(raw));path=self.name(name)
        self.owner.publish(path,io.BytesIO(raw),len(raw),hashlib.sha256(raw).hexdigest());self.track(path)
        return path
    def put(self,name,value):
        payload=encoded(value)
        if len(payload)>60000:raise LocalAiError('disk_budget','Checkpoint row exceeds 64 KiB.')
        seal=hmac.new(self.ticket['key'],self.ticket['binding']+name.encode()+payload,hashlib.sha256).hexdigest()
        return self.put_bytes(name,encoded(dict(payload=value,seal=seal)))
    def get(self,name):
        if name not in self.ticket['files']:return None
        path=self.name(name);row=self.ticket['files'][name]
        if name not in self._leases:self.protect(path)
        if path.stat().st_nlink!=1 or path.stat().st_size>65536 or digest(path,self.control)!=row['sha256']:
            raise LocalAiError('checkpoint_ineligible','Checkpoint object changed; retry refused.')
        value=json.loads(path.read_bytes());payload=encoded(value['payload'])
        seal=hmac.new(self.ticket['key'],self.ticket['binding']+name.encode()+payload,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(seal,value['seal']):raise LocalAiError('checkpoint_ineligible','Checkpoint authentication failed.')
        self.hits+=1;return value['payload']
    def protect(self,path):
        row=self.ticket['files'].get(path.name)
        if row is None:raise LocalAiError('checkpoint_ineligible','Unowned stage object.')
        lease=ReadLocks();self._leases[path.name]=lease
        lease.parents(path.parent);lease.add(path)
        from mediaforge.infrastructure.models.native_guard import identity
        key,opened=identity(lease.api,lease.handles[-1])
        if opened.links!=1 or tuple(row['identity'])!=key or path.stat().st_size!=row['bytes'] or digest(path,self.control)!=row['sha256']:
            raise LocalAiError('checkpoint_ineligible','Stage identity/content differs.')
    def owns_name(self,name):
        """Ticket membership only; callers still need sha/native discard.

        Distinguishes intentionally retired caches from missing owned objects.
        This never adopts an object merely because its pathname exists.
        """
        self.name(name)
        return name in self.ticket['files']
    def discard(self,path):
        row=self.ticket['files'][path.name]
        lease=self._leases.pop(path.name,None)
        if lease is not None:lease.close()
        from mediaforge.infrastructure.models.native_guard import identity
        with Ownership() as owner:
            owner.parents.parents(path.parent);handle=owner.open(path,0x80010000,1,3)
            key,_=identity(owner.api,handle)
            if tuple(row['identity'])!=key:
                raise LocalAiError('lifecycle_failed','Stage replacement refused at retirement.',retained_resources=(path,))
            owner.pending(handle,True)
        del self.ticket['files'][path.name]
        if self.store is not None:
            self.budget['bytes']-=self.store.charge(row['bytes'])
            self.budget['files']-=1
            if path.name.endswith('.json'):self.budget['metadata']-=row['bytes']
    @contextmanager
    def pcm(self,name,samples):
        path=self.name(name);self.admit(samples*2)
        if name in self._reserved or name in self.ticket['files']:raise LocalAiError('storage_refused','PCM stage already exists.')
        self._reserved[name]=samples*2
        try:
            with PcmWriter(path,self.control,samples) as writer:yield writer
            self.track(path)
        finally:self._reserved.pop(name,None)
    def read_blocks(self,path):return blocks(path,self.control)
    def sha(self,path):
        if path.name not in self._leases:self.protect(path)
        return digest(path,self.control)
    def trim(self,raw):return trim_silence(raw)
