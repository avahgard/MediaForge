"""Lifetime-held portable core TCB, narrower than an arbitrary Python folder."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.filesystem.profile import local_path
from mediaforge.infrastructure.models.core_loader import records
from mediaforge.infrastructure.models.native_guard import NativeGate,identity


class CoreRuntime:
    def __init__(self,root,expected):
        self.root=local_path(Path(root));self.expected=expected;self.locks=ReadLocks();self.native_ids={}
        try:
            self.locks.parents(self.root);self.locks.add(self.root/'CoreManifest.tsv')
            members=records(self.root,expected)
            actual=set();count=0
            directories={p for file in members for p in file.parents if p!=self.root and p.is_relative_to(self.root)}
            # Walk top down under native directory leases. Validate each held
            # ancestor once, before enumerating its children.
            pending=[self.root/'Core',self.root/'external']
            while pending:
                folder=pending.pop()
                if folder not in directories and folder not in (self.root/'Core',self.root/'external'):
                    raise LocalAiError('runtime_integrity','Unlisted portable core directory.')
                self.locks.add(folder,True)
                for file in folder.iterdir():
                    count+=1
                    if count>20000:raise LocalAiError('runtime_integrity','Portable core inventory bound exceeded.')
                    status=file.lstat()
                    if status.st_file_attributes&0x400:
                        raise LocalAiError('runtime_integrity','Linked portable core member.')
                    if file in directories:
                        pending.append(file)
                    elif file in members:
                        actual.add(file)
                    else:
                        raise LocalAiError('runtime_integrity','Unlisted portable core file or directory.')
            if actual!=set(members):raise LocalAiError('runtime_integrity','Portable core inventory differs.')
            base=self.root/'Core/Python'
            for file,(size,expected_hash) in members.items():
                h=self.locks.add(file)
                key,info=identity(self.locks.api,h)
                if info.links!=1 or info.attributes&0x410 or ((info.high<<32)|info.low)!=size:
                    raise LocalAiError('runtime_integrity','Portable core file identity differs.')
                with file.open('rb') as stream:actual_hash=hashlib.file_digest(stream,'sha256').hexdigest()
                if actual_hash!=expected_hash:raise LocalAiError('runtime_integrity','Portable core hash differs.')
                if (file==base/'python.exe' or (file.parent==base and file.suffix.lower()=='.dll')
                        or (file.parent==base/'DLLs' and file.suffix.lower() in ('.dll','.pyd'))):
                    self.native_ids[key]=str(file)
            self.members=members
        except BaseException:
            self.locks.close();raise

    def native(self,runtime):
        # All stdlib/application bytes remain leased, including if the launcher
        # itself exits. NativeGate duplicates these same lifetime handles.
        ids=dict(self.native_ids);handles=tuple(self.locks.handles)
        if runtime is not None:ids.update(runtime.native_ids);handles+=tuple(runtime.handles)
        native=NativeGate(SimpleNamespace(native_ids=ids,handles=handles))
        try:
            # Accepted C2 auto-enumeration assumes a trusted host interpreter.
            # In a writable portable folder, never enroll an added base DLL.
            if set(native.allowed)-set(ids):
                raise LocalAiError('runtime_integrity','Unlisted private Python TCB image; execution refused.')
            return native
        except BaseException:native.close();raise

    def environment(self,environment):
        result=dict(environment)
        result.update(MEDIAFORGE_CORE_ROOT=str(self.root),MEDIAFORGE_CORE_MANIFEST_SHA256=self.expected)
        return result

    def close(self):self.locks.close()
