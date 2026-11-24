"""Content receipts captured under validation locks; protected fresh result reads."""
import hashlib,weakref
from pathlib import Path
from mediaforge.features.media.export import ExportError,OutputFileIdentity,OutputIdentity
from mediaforge.infrastructure.filesystem.image_output import ReadLocks


def file_identity(path,name,control):
    path=Path(path);before=path.stat();digest=hashlib.sha256()
    with path.open('rb') as file:
        while block:=file.read(1024*1024):control.check();digest.update(block)
    after=path.stat()
    values=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
    if values(before)!=values(after):raise ExportError('result_changed','Result changed during protected validation.')
    return OutputFileIdentity(name,*values(after),digest.hexdigest())


def capture_output(paths,names,destination,control,budget=None):
    """Caller holds validation read locks until commit; never hashes a late result."""
    lease=getattr(budget,'reserve_control',budget.reserve_peak)(max(4096,4096*len(paths))) if budget else None
    try:
        receipt=OutputIdentity(str(destination),tuple(file_identity(p,n,control) for p,n in zip(paths,names,strict=True)))
        if budget:weakref.finalize(receipt,budget.release,lease)
        return receipt
    except BaseException:
        if budget:budget.release(lease)
        raise


class LockedResult:
    def __init__(self,result,control,budget=None):
        self.result=result;self.control=control;self.locks=ReadLocks();self.budget=budget;self.lease=None

    def prepare(self):
        from mediaforge.application.services.exported_result import result_path
        result=self.result;receipt=result.identity
        if receipt is None or receipt.path!=result.path or not 0<len(receipt.files)<=10000:
            raise ExportError('result_unverified','No trustworthy completed-output identity is available.')
        if self.budget:self.lease=getattr(self.budget,'reserve_control',self.budget.reserve_peak)(4*1024**2+len(receipt.files)*4096)
        if result.kind=='sequence' and (result.numbering is None or result.numbering.count!=len(receipt.files)):
            raise ExportError('result_unverified','Completed sequence count disagrees with its bounded identity manifest.')
        raw=Path(result.path)
        if len(raw.parts)>128 or len(str(raw).encode('utf-8'))>2048:
            raise ExportError('unsupported_result_path','Result location exceeds bounded reopen path metadata. Use a shorter folder; the current Viewer is preserved.')
        path=result_path(result);self.locks.parents(path if result.kind=='sequence' else path.parent)
        expected=(tuple(result.numbering.name(i) for i in range(result.numbering.count))
                  if result.kind=='sequence' and result.numbering else (path.name,))
        if tuple(f.name for f in receipt.files)!=expected:raise ExportError('result_unverified','Completed output manifest identity disagrees with its names/order.')
        self.paths=tuple(path/f.name for f in receipt.files) if result.kind=='sequence' else (path,)
        if any(len(str(p).encode('utf-8'))>2048 for p in self.paths):
            raise ExportError('unsupported_result_path','Result member paths exceed the bounded 2 KiB reopen allowance.')
        for member in self.paths:self.control.check();self.locks.add(member)
        for member,identity in zip(self.paths,receipt.files,strict=True):
            if file_identity(member,identity.name,self.control)!=identity:
                raise ExportError('result_changed','The completed export changed. Open the file separately or export again; the current Viewer is preserved.')
        if result.kind=='sequence':
            import os
            with os.scandir(path) as entries:
                names=set(expected);count=0
                for entry in entries:
                    self.control.check();count+=1
                    if count>len(expected) or entry.name not in names:raise ExportError('result_changed','Completed sequence membership changed.')
            if count!=len(expected):raise ExportError('result_changed','Completed sequence membership changed.')
        self.control.check();return self

    def close(self):
        self.locks.close()
        if self.lease is not None:self.budget.release(self.lease);self.lease=None
