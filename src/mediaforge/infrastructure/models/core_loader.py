"""Standalone exact private-stdlib resolver; authenticated by final entries.

No optional-directory trust, bytecode preference, shell helper or new native
exception. Builtin/Frozen remain interpreter TCB; file imports need core rows.
"""
import hashlib
import importlib.machinery as machinery
from pathlib import Path
import sys


def records(root,expected):
    root=Path(root)
    raw=(root/'CoreManifest.tsv').read_bytes()
    if len(raw)>4*1024**2 or hashlib.sha256(raw).hexdigest()!=expected:
        raise ImportError('Private core manifest is not authenticated')
    members={};total=0
    for line in raw.decode('utf-8').splitlines():
        digest,size,name=line.split('\t')
        if (len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest)
                or not size.isdigit() or not name.startswith(('Core/','external/'))
                or '\\' in name or any(p in ('','..','.') or ':' in p or p.endswith((' ','.')) for p in name.split('/'))):
            raise ImportError('Invalid private core member')
        path=root/name
        if path in members or len(members)>=16000:raise ImportError('Duplicate/excessive private core member')
        size=int(size);total+=size
        if size>256*1024**2 or total>2*1024**3:raise ImportError('Private core size bound')
        members[path]=(size,digest)
    return members


def install(root,expected):
    root=Path(root)
    if root/'Core'/'Python'!=Path(sys.base_prefix):raise ImportError('Private interpreter identity differs')
    members=records(root,expected)
    library=root/'Core'/'Python'
    original=machinery.PathFinder.find_spec

    class Source(machinery.SourceFileLoader):
        def get_code(self,fullname):
            path=Path(self.path);row=members.get(path)
            if row is None or not path.is_relative_to(library):raise ImportError('Unlisted private stdlib source')
            with path.open('rb') as stream:raw=stream.read(4*1024**2+1)
            if len(raw)!=row[0] or hashlib.sha256(raw).hexdigest()!=row[1]:
                raise ImportError('Private stdlib source hash differs')
            return compile(raw,self.path,'exec',dont_inherit=True,optimize=sys.flags.optimize)

    def find(cls,fullname,path=None,target=None):
        spec=original(fullname,path,target)
        if spec is None:return None
        loader=spec.loader
        if loader is None:
            for folder in spec.submodule_search_locations or ():
                prefix=Path(folder)
                if not prefix.is_relative_to(library) or not any(p.is_relative_to(prefix) for p in members):
                    raise ImportError('Unlisted private stdlib namespace')
            return spec
        origin=Path(spec.origin or '')
        if isinstance(loader,machinery.SourceFileLoader):
            if origin not in members or not origin.is_relative_to(library):raise ImportError('Unlisted filesystem source')
            spec.loader=Source(fullname,str(origin));return spec
        if isinstance(loader,machinery.ExtensionFileLoader):
            if origin not in members or not origin.is_relative_to(library):raise ImportError('Unlisted private stdlib native module')
            return spec # unchanged parent NativeGate owns the exact image object
        raise ImportError('Unqualified private stdlib loader; no bytecode/zip fallback')
    machinery.PathFinder.find_spec=classmethod(find)
