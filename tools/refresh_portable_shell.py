"""Update ONLY manifest-authenticated generated app files; archive old objects.

This is a build tool, never a model updater or production self-update path.
Static third-party bytes remain leased and unchanged. No recursive deletion.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import io
import json
from pathlib import Path
import subprocess

from mediaforge.infrastructure.filesystem.profile import ProfilePaths,local_path
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.models.owned import Ownership,ModelMutex
from mediaforge.infrastructure.models.core_loader import records

REPO=Path(__file__).resolve().parents[1]
CSC=Path('C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe')


def sha(raw):return hashlib.sha256(raw).hexdigest()


def held(owner,handle,maximum=8*1024**2):
    info=owner.info(handle);size=(info.high<<32)|info.low
    if size>maximum:raise ValueError('Generated build member exceeds its explicit bound.')
    owner.api.ReadFile.argtypes=[W.HANDLE,C.c_void_p,W.DWORD,C.POINTER(W.DWORD),C.c_void_p]
    owner.api.ReadFile.restype=W.BOOL
    raw=bytearray()
    while len(raw)<size:
        n=min(1024**2,size-len(raw));buffer=C.create_string_buffer(n);got=W.DWORD()
        owner.require(owner.api.ReadFile(handle,buffer,n,C.byref(got),None))
        if got.value!=n:raise ValueError('Incomplete held build member.')
        raw.extend(buffer.raw[:n])
    return bytes(raw)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--previous-receipt',type=Path,required=True)
    p.add_argument('--receipt',type=Path,required=True);p.add_argument('--generation',type=int,required=True)
    args=p.parse_args();paths=ProfilePaths.for_test(args.root);paths.activate_temporary()
    if args.receipt.exists() or not args.receipt.is_relative_to(paths.logs) or not 2<=args.generation<=56:
        p.error('New bounded portable build receipt/generation required.')
    root=paths.root.parent.parent;previous=json.loads(args.previous_receipt.read_bytes())
    before=previous['manifest_sha256'];exe_before=previous['exe_sha256']
    history=paths.data/'build-history'/f'shell-{args.generation-1:02d}'
    if history.exists():p.error('Build history is immutable; choose a new generation.')
    changed=[];result=dict(ok=False,generation=args.generation,previous_receipt=str(args.previous_receipt))
    try:
        with ModelMutex(paths,'portable-build'),Ownership() as owner,ReadLocks() as static,ReadLocks() as sources:
            owner.parents.parents(root)
            # The launcher leases this manifest through GUI lifetime. DELETE +
            # share=0 refuses an active deployment and excludes new startup.
            mh=owner.open(root/'CoreManifest.tsv',0x80010000,0,3)
            old_manifest=held(owner,mh,4*1024**2)
            if sha(old_manifest)!=before:raise ValueError('Previous generated core manifest differs.')
            eh=owner.open(root/'MediaForge.exe',0x80010000,0,3)
            old_exe=held(owner,eh)
            if sha(old_exe)!=exe_before:raise ValueError('Previous generated launcher differs.')
            old=records_from_bytes(root,old_manifest)
            desired={}
            for source in (REPO/'src/mediaforge').rglob('*'):
                if source.is_dir() or '__pycache__' in source.parts or source.suffix=='.pyc':continue
                local_path(source);sources.parents(source.parent);sources.add(source)
                desired[root/'Core/App/mediaforge'/source.relative_to(REPO/'src/mediaforge')]=source.read_bytes()
            icon = REPO/'assets/icons/MediaForge.ico'
            for source,target in ((icon,root/'Core/App/mediaforge/resources/MediaForge.ico'),
                                  (REPO/'packaging/portable_boot.py',root/'Core/portable_boot.py'),
                                  (REPO/'packaging/Launcher.cs',root/'Core/Provenance/Launcher.cs')):
                sources.parents(source.parent);sources.add(source);desired[target]=source.read_bytes()
            dirs={d for f in old for d in f.parents if d!=root and d.is_relative_to(root)}
            actual=set();count=0
            for base in (root/'Core',root/'external'):
                static.parents(base)
                for file in base.rglob('*'):
                    count+=1;local_path(file)
                    if count>20000:raise ValueError('Build inventory bound.')
                    if file.is_dir():
                        if file not in dirs:raise ValueError('Unknown generated core directory.')
                        static.add(file,True);continue
                    actual.add(file)
            if actual!=set(old):raise ValueError('Existing generated core inventory differs.')
            updates={}
            for file,(size,digest) in old.items():
                fresh=desired.get(file)
                if fresh is not None and (len(fresh),sha(fresh))!=(size,digest):
                    h=owner.open(file,0x80010000,1,3)
                    raw=held(owner,h)
                    if len(raw)!=size or sha(raw)!=digest:raise ValueError('Changed generated source before update.')
                    updates[file]=(h,fresh)
                else:
                    static.add(file)
                    if file.stat().st_nlink!=1 or file.stat().st_size!=size:
                        raise ValueError('Static core size/alias differs.')
                    with file.open('rb') as stream:actual_hash=hashlib.file_digest(stream,'sha256').hexdigest()
                    if actual_hash!=digest:raise ValueError('Static core bytes differ; no enrollment.')
            history.mkdir(parents=True)
            new=dict(old)
            for file,(h,raw) in updates.items():
                archive=history/file.relative_to(root);archive.parent.mkdir(parents=True,exist_ok=True)
                owner.parents.parents(archive.parent);owner.rename(h,archive)
                owner.publish(file,io.BytesIO(raw),len(raw),sha(raw));static.add(file)
                new[file]=(len(raw),sha(raw));changed.append(file.relative_to(root).as_posix())
            for file,raw in desired.items():
                if file in old:continue
                file.parent.mkdir(parents=True,exist_ok=True);owner.parents.parents(file.parent)
                owner.publish(file,io.BytesIO(raw),len(raw),sha(raw));static.add(file)
                new[file]=(len(raw),sha(raw));changed.append(file.relative_to(root).as_posix())
            # No unknown newly inserted file becomes part of a release manifest.
            actual={f for base in (root/'Core',root/'external') for f in base.rglob('*') if f.is_file()}
            if actual!=set(new):raise ValueError('Late unknown core file; build is not activated.')
            encoded=''.join(f'{digest}\t{size}\t{f.relative_to(root).as_posix()}\n'
                for f,(size,digest) in sorted(new.items())).encode('utf-8')
            expected=sha(encoded)
            version, = (
                file.parent.name.removeprefix('mediaforge-').removesuffix('.dist-info')
                for file in old if file.match('Core/Site/mediaforge-*.dist-info/METADATA')
            )
            generated=paths.temporary/f'Launcher-final-{args.generation:02d}.cs'
            with generated.open('x',encoding='utf-8') as stream:
                stream.write(desired[root/'Core/Provenance/Launcher.cs'].decode('utf-8').replace('@MANIFEST_SHA256@',expected)
                             .replace('@RELEASE_VERSION@',version))
            output=paths.temporary/f'MediaForge-launcher-final{args.generation}.exe'
            if output.exists():raise ValueError('Compiled build object already exists.')
            refs=[str(CSC.parent/n) for n in ('mscorlib.dll','System.dll','System.Core.dll','System.Windows.Forms.dll','System.Drawing.dll')]
            command=[str(CSC),'/nologo','/noconfig','/nostdlib+','/target:winexe','/platform:x64','/optimize+','/debug-',
                '/codepage:65001','/win32icon:'+str(icon),*['/reference:'+n for n in refs],'/out:'+output.name,generated.name]
            compiled=subprocess.run(command,cwd=paths.temporary,env=paths.child_environment({}),
                stdin=subprocess.DEVNULL,capture_output=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
            result.update(compiler_exit=compiled.returncode,compiler_diagnostic=(compiled.stdout+compiled.stderr).decode('cp866',errors='replace')[-8192:])
            if compiled.returncode:raise RuntimeError('Portable launcher compilation failed; prior objects remain owned/preserved.')
            image=output.read_bytes()
            owner.rename(mh,history/'CoreManifest.tsv');owner.rename(eh,history/'MediaForge.exe')
            owner.publish(root/'CoreManifest.tsv',io.BytesIO(encoded),len(encoded),expected)
            owner.publish(root/'MediaForge.exe',io.BytesIO(image),len(image),sha(image))
            result.update(ok=True,manifest_sha256=expected,exe_sha256=sha(image),files=len(new),
                bytes=sum(row[0] for row in new.values()),root=str(root),history=str(history),changed=changed)
    except Exception as error:result.update(detail=str(error),changed=changed)
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:result.get(k) for k in ('ok','detail','compiler_exit','manifest_sha256','files','bytes')},ensure_ascii=False))
    if not result['ok']:raise SystemExit(2)


def records_from_bytes(root,raw):
    # Parsing is data-only. The original bytes were checked against the recorded
    # prior compiled launcher digest while held, not reopened through a path.
    result={}
    for line in raw.decode('utf-8').splitlines():
        digest,size,name=line.split('\t')
        if not name.startswith(('Core/','external/')) or any(p in ('','..','.') for p in name.split('/')) or '\\' in name:
            raise ValueError('Unsafe generated manifest path.')
        file=local_path(root/name)
        if file in result or not size.isdigit() or len(digest)!=64:raise ValueError('Invalid generated manifest.')
        result[file]=(int(size),digest)
    return result


if __name__=='__main__':main()
