"""Read-only native manifest authentication with deny-write/delete lifetime leases."""
import hashlib
from contextlib import contextmanager
from pathlib import Path

from mediaforge.application.contracts.models import ModelError
from mediaforge.infrastructure.filesystem.profile import local_path
from mediaforge.infrastructure.filesystem.image_output import ReadLocks


class Guard:
    def __init__(self,path):self.path=path;self.locks=ReadLocks()
    def close(self):
        try:self.locks.close()
        except BaseException as error:
            raise ModelError('lifecycle_failed','Model read handles remain retained.',retained_resources=(self,)) from error


class PortableModelStore:
    def __init__(self,paths):self.paths=paths
    def location(self,spec):return self.paths.models/spec.model_id/spec.revision
    def exists(self,spec):
        try:return local_path(self.location(spec)).exists()
        except (OSError,ValueError) as error:raise ModelError('unsafe_model_path','Unsafe model path.') from error

    def verify(self,spec,*,control=None):
        from mediaforge.infrastructure.models.installation_lifecycle import pending_path
        try:pending_path(self,spec).lstat()
        except FileNotFoundError:pass
        except OSError as error:
            raise ModelError('storage_refused','Installation intent cannot be inspected; model trust is unavailable.') from error
        else:raise ModelError('incomplete','Unfinished installation intent exists; no orphan adoption is permitted.')
        return self.verify_at(spec,self.location(spec),control=control)

    def check_mutation(self,spec):
        # Negative-only crash state, never a source of approval or deletion authority.
        prefix=f'.install-pending-{spec.model_id}--'.casefold()
        try:
            for index,path in enumerate(self.paths.manifests.iterdir()):
                if index>=4096:raise ModelError('incomplete','Model manifest namespace exceeds its bounded admission scan.')
                if path.name.casefold().startswith(prefix):
                    raise ModelError('incomplete','An unfinished model transaction blocks mutation; explicit owned retirement is required.')
        except OSError as error:
            raise ModelError('storage_refused','Installation intent namespace cannot be inspected; mutation is refused.') from error

    def verify_at(self,spec,path,*,owned_root=False,control=None):
        guard=Guard(path)
        try:
            local_path(path)
            if not owned_root:guard.locks.parents(path)
            expected={m.name:m for m in spec.members}
            seen=set();entries=0
            def walk(folder):
                nonlocal entries
                for item in folder.iterdir():
                    if control:control.check()
                    entries+=1
                    if entries>1024:raise ModelError('corrupt','Unexpected model inventory exceeds bounds.')
                    info=item.lstat()
                    if info.st_file_attributes&0x400:raise ModelError('corrupt','Reparse model member refused.')
                    if item.is_dir():
                        prefix=item.relative_to(path).as_posix()+'/'
                        if not any(name.startswith(prefix) for name in expected):
                            raise ModelError('corrupt','Unexpected model directory.')
                        guard.locks.add(item,True);walk(item)
                    else:
                        name=item.relative_to(path).as_posix()
                        if name not in expected or info.st_nlink!=1:raise ModelError('corrupt','Extra or aliased model member.')
                        handle=guard.locks.add(item)
                        from mediaforge.infrastructure.models.owned import Info
                        import ctypes
                        opened=Info()
                        guard.locks.api.GetFileInformationByHandle.argtypes=[ctypes.c_void_p,ctypes.POINTER(Info)]
                        guard.locks.api.GetFileInformationByHandle.restype=ctypes.c_int
                        if not guard.locks.api.GetFileInformationByHandle(handle,ctypes.byref(opened)):
                            raise ModelError('storage_refused','Native model identity query failed; corruption is not established.')
                        if opened.links!=1:
                            raise ModelError('corrupt','Opened model member has an unsafe identity/link count.')
                        before=item.stat();member=expected[name]
                        if before.st_size!=member.size:raise ModelError('corrupt','Model member size mismatch.')
                        digest=hashlib.sha256()
                        with item.open('rb') as stream:
                            while chunk:=stream.read(1024*1024):
                                if control:control.check()
                                digest.update(chunk)
                        after=item.stat()
                        if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) or digest.hexdigest()!=member.sha256:
                            raise ModelError('corrupt','Model member identity/digest mismatch.')
                        seen.add(name)
            walk(path)
            if seen!=set(expected):raise ModelError('corrupt','Incomplete installed model revision.')
            return guard
        except BaseException as error:
            try:guard.close()
            except BaseException as cleanup:
                raise ModelError('lifecycle_failed','Model verification resources retained.',retained_resources=(guard,)) from cleanup
            if isinstance(error,ModelError):raise
            if getattr(error,'code',None) in ('source_busy','unsafe_path','unsupported_platform') or isinstance(error,PermissionError) or isinstance(error,OSError) and not isinstance(error,FileNotFoundError):
                raise ModelError('storage_refused','Model verification could not access storage; no corruption diagnosis is available.') from error
            raise ModelError('corrupt','Model verification failed.') from error

    def reserve(self,spec):
        from mediaforge.infrastructure.models.owned import ModelMutex
        # Explicit use/install only; read-only Missing inventory creates nothing.
        try:
            with ReadLocks() as locks:
                locks.parents(self.paths.root)
                self.paths.manifests.mkdir(exist_ok=True)
                local_path(self.paths.manifests)
            return ModelMutex(self.paths,spec.model_id)
        except ModelError:raise
        except Exception as error:
            retained=getattr(error,'retained_resources',())
            raise ModelError('lifecycle_failed' if retained else 'storage_refused',
                             'Portable model reservation is unavailable.',retained_resources=retained) from error

    def prepare(self):self.paths.prepare()

    @contextmanager
    def stage(self,spec,source,**archive):
        from mediaforge.infrastructure.models.install import stage
        import zipfile
        try:
            with stage(self,spec,source,**archive) as guard:yield guard
        except ModelError:raise
        except (OSError,zipfile.BadZipFile,ValueError,RuntimeError,NotImplementedError) as error:
            retained=getattr(error,'retained_resources',())
            raise ModelError('lifecycle_failed' if retained else 'install_failed',
                             'Model staging refused: invalid archive, permissions or incomplete storage.',retained_resources=retained) from error

    def retire(self,spec):
        from mediaforge.infrastructure.models.install import retire
        return retire(self,spec)

    def download(self,archive):
        from mediaforge.infrastructure.models.download import acquire
        return acquire(self.paths,archive)
