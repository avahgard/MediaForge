import sys,os,re,json
from pathlib import Path

def install_runtime(site,manifest,digest):
    """Resolve optional code by exact manifest members, never directory search.

    Parent holds every source/image object and ancestor until child retirement.
    Windows native-image admission is enforced separately by the parent debugger.
    """
    import hashlib
    import importlib.abc
    import importlib.machinery as machinery
    import importlib.util
    import importlib.metadata
    encoded=Path(manifest).read_bytes()
    if len(encoded)>8*1024*1024 or hashlib.sha256(encoded).hexdigest()!=digest:
        raise ValueError('Runtime import manifest is not authenticated')
    members={r['name']:r for r in json.loads(encoded)}
    site=Path(site).resolve()
    standard=tuple(sys.path)
    library=Path(sys.base_prefix)/'Lib'
    modules={};ranks={};namespaces=set();executed=set();origins=[]
    for name,row in members.items():
        suffix=next((s for s in machinery.EXTENSION_SUFFIXES if name.endswith(s)),None)
        if name.endswith('.py'):stem=name[:-3];kind='python'
        elif suffix:stem=name[:-len(suffix)];kind='native'
        else:continue
        parts=stem.split('/')
        package=parts[-1]=='__init__'
        if package:parts=parts[:-1]
        if not parts or not all(p.isidentifier() for p in parts):continue
        key='.'.join(parts)
        # CPython packages precede same-name modules, and native extensions
        # precede sources (PyAV legitimately ships both .py and .pyd members).
        rank=(not package,kind!='native',machinery.EXTENSION_SUFFIXES.index(suffix) if suffix else 0)
        if key in modules and rank==ranks[key]:raise ValueError('Ambiguous runtime module inventory')
        if key not in modules or rank<ranks[key]:modules[key]=(name,kind,package);ranks[key]=rank
        for i in range(1,len(parts)):namespaces.add('.'.join(parts[:i]))

    class Source(machinery.SourceFileLoader):
        def get_code(self,fullname):
            # SourceFileLoader's normal bytecode-cache preference is forbidden.
            relative=Path(self.path).relative_to(site).as_posix()
            raw=Path(self.path).read_bytes();row=members[relative]
            if len(raw)!=row['size'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:
                raise ImportError('Runtime source object failed import-time authentication')
            code=compile(raw,self.path,'exec',dont_inherit=True,optimize=sys.flags.optimize)
            executed.add(code)
            if len(origins)<256:origins.append(dict(name=fullname,path=self.path))
            return code

    class Resolver(importlib.abc.MetaPathFinder):
        def find_spec(self,fullname,path=None,target=None):
            # Standard library precedes optional packages. For submodules, never
            # delegate a runtime directory to Python's filesystem path finder.
            search=standard if path is None else tuple(p for p in path if not Path(os.path.abspath(p)).is_relative_to(site))
            spec=machinery.PathFinder.find_spec(fullname,search)
            if spec is not None:
                loader=spec.loader
                if (loader is not None and not isinstance(loader,machinery.ExtensionFileLoader)
                        and hasattr(loader,'get_code') and hasattr(loader,'exec_module')):
                    origin=Path(spec.origin or '')
                    # Only actual CPython stdlib sources/its immutable zip are
                    # the standard TCB. A runtime package cannot add tmp/cache
                    # directories to __path__ and have them authenticated here.
                    in_library=origin.is_absolute() and origin.is_relative_to(library) and 'site-packages' not in [p.casefold() for p in origin.parts]
                    zipped=type(loader).__module__=='zipimport' and str(getattr(loader,'archive','')) in standard
                    if not in_library and not zipped:raise ImportError('Filesystem source outside declared runtime/stdlib refused')
                    class Standard:
                        def create_module(self,spec):return loader.create_module(spec) if hasattr(loader,'create_module') else None
                        def exec_module(self,module):
                            code=loader.get_code(fullname)
                            if code is None:raise ImportError('Standard source code unavailable')
                            executed.add(code);exec(code,module.__dict__)
                        def __getattr__(self,name):return getattr(loader,name)
                    spec.loader=Standard()
                return spec
            row=modules.get(fullname)
            if row is not None:
                relative,kind,package=row
                filename=str(site/relative)
                loader=Source(fullname,filename) if kind=='python' else machinery.ExtensionFileLoader(fullname,filename)
                if kind=='native' and len(origins)<256:origins.append(dict(name=fullname,path=filename))
                return importlib.util.spec_from_file_location(fullname,filename,loader=loader,
                    submodule_search_locations=[str(Path(filename).parent)] if package else None)
            if fullname in namespaces:
                spec=machinery.ModuleSpec(fullname,None,is_package=True)
                spec.submodule_search_locations=[str(site/Path(*fullname.split('.')))]
                return spec
            return None

        def find_distributions(self,context=None):
            wanted=context.name if context is not None else None
            normalized=lambda x:re.sub('[-_.]+','-',x).lower()
            for name in members:
                if name.endswith('.dist-info/METADATA') and '/' not in name.removesuffix('/METADATA'):
                    distribution=importlib.metadata.PathDistribution((site/name).parent)
                    if wanted is None or normalized(distribution.metadata['Name'])==normalized(wanted):yield distribution

        def check_exec(self,code):
            # co_filename is a compiler label, often an absent build-machine
            # path in Cython. Never perform filesystem I/O from an exec audit
            # callback. Actual source identities are held by the parent and
            # authenticated by Source.get_code, not by resolving this label.
            try:inside=Path(code.co_filename).is_absolute() and Path(os.path.normpath(code.co_filename)).is_relative_to(site)
            except (ValueError,OSError):inside=False
            if inside and code not in executed:raise PermissionError('Unverified runtime code/bytecode execution refused')
            if Path(code.co_filename).suffix.lower() in ('.py','.pyc') and code not in executed:
                raise PermissionError('Unverified filesystem code execution outside the runtime refused')

    policy=Resolver()
    # Builtin/Frozen are the trusted interpreter; all filesystem imports pass
    # the resolver. Optional directories are absent from global sys.path.
    sys.meta_path[:]=[machinery.BuiltinImporter,machinery.FrozenImporter,policy]
    return policy,origins

