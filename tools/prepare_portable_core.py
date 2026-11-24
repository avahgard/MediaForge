"""Unpack authenticated inert core resources into a NEW build-owned directory."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import tarfile
import zipfile

from mediaforge.infrastructure.filesystem.profile import ProfilePaths,local_path
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.models.optional_resources import safe_member


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--lock',type=Path,required=True)
    p.add_argument('--target',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True)
    p.add_argument('--resume-authenticated-partial',action='store_true')
    args=p.parse_args();paths=ProfilePaths.for_test(args.root);paths.activate_temporary()
    target=local_path(args.target)
    if (target.exists() and not args.resume_authenticated_partial) or not target.is_relative_to(paths.root.parent.parent) or args.receipt.exists():
        p.error('New build-owned directory inside the explicit portable application required.')
    lock=json.loads(args.lock.read_bytes());total=0;members=[];omitted=[];seen={};duplicates=[]
    target.mkdir(exist_ok=args.resume_authenticated_partial)
    def output(relative,stream,size):
        nonlocal total
        safe_member(relative);key=relative.casefold()
        if len(seen)>=16000 or not 0<=size<=256*1024**2:raise ValueError('Core member limit: '+relative)
        digest=hashlib.sha256();count=0
        if key in seen:
            while raw:=stream.read(1024*1024):
                count+=len(raw)
                if count>size:raise ValueError('Duplicate member size bound.')
                digest.update(raw)
            if (size,digest.hexdigest())!=(seen[key]['size'],seen[key]['sha256']) or count!=size:
                raise ValueError('Nonidentical wheel collision: '+relative)
            duplicates.append(dict(path=relative,sha256=digest.hexdigest(),reason='Identical declared PySide split-wheel namespace.'))
            return
        total+=size
        if total>2*1024**3:raise ValueError('Core byte bound.')
        file=target/relative;file.parent.mkdir(parents=True,exist_ok=True)
        if file.exists():
            if not args.resume_authenticated_partial:raise ValueError('Unexpected existing core file.')
            with ReadLocks() as leases:
                leases.parents(file.parent);leases.add(file)
                with file.open('rb') as staged:actual=hashlib.file_digest(staged,'sha256').hexdigest()
                if file.stat().st_nlink!=1 or file.stat().st_size!=size:raise ValueError('Partial core object size/alias differs.')
                while raw:=stream.read(1024*1024):
                    count+=len(raw)
                    if count>size:raise ValueError('Core member exceeded size bound.')
                    digest.update(raw)
                if count!=size or digest.hexdigest()!=actual:raise ValueError('Partial core bytes differ from authenticated upstream.')
            row=dict(path=relative,size=count,sha256=actual);seen[key]=row;members.append(row);return
        with file.open('xb') as result:
            while raw:=stream.read(1024*1024):
                count+=len(raw)
                if count>size:raise ValueError('Core member expanded beyond its authenticated header.')
                result.write(raw);digest.update(raw)
        if count!=size:raise ValueError('Incomplete core member.')
        row=dict(path=relative,size=count,sha256=digest.hexdigest());seen[key]=row;members.append(row)
    for asset in lock['assets']:
        source=paths.downloads/('core-'+asset['sha256'])
        with ReadLocks() as leases:
            leases.parents(source.parent);leases.add(source)
            with source.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
            if source.stat().st_nlink!=1 or source.stat().st_size!=asset['size'] or digest!=asset['sha256']:
                raise ValueError('Core acquisition object differs.')
            if asset['name']=='CPython':
                with tarfile.open(source,'r:gz') as archive:
                    for row in archive:
                        name=safe_member(row.name.rstrip('/'))
                        if row.isdir():continue
                        if not row.isfile() or not name.startswith('python/'):raise ValueError('Unsupported CPython archive object.')
                        name=name[len('python/'):]
                        if name.startswith('Lib/site-packages/'):
                            omitted.append(dict(asset=asset['filename'],path=name,reason='No bundled pip/site hooks; explicit declared core wheels only.'));continue
                        with archive.extractfile(row) as stream:output('Python/'+name,stream,row.size)
            else:
                with zipfile.ZipFile(source) as archive:
                    for row in archive.infolist():
                        name=safe_member(row.filename.rstrip('/'))
                        if row.is_dir():continue
                        mode=row.external_attr>>16
                        if row.flag_bits&1 or (mode and stat.S_IFMT(mode) not in (0,stat.S_IFREG)) or name.endswith('.pth'):
                            raise ValueError('Wheel hook/link/encrypted member refused.')
                        with archive.open(row) as stream:output('Site/'+name,stream,row.file_size)
    actual=set()
    for path in target.rglob('*'):
        local_path(path)
        if path.is_file():actual.add(path.relative_to(target).as_posix())
    if actual!={m['path'] for m in members}:raise ValueError('Unknown partial core objects; no activation.')
    receipt=dict(schema=1,core=str(target),bytes=total,files=len(members),members=members,omissions=omitted,duplicates=duplicates,
        provenance=lock,source_lock_sha256=hashlib.sha256(args.lock.read_bytes()).hexdigest(),hooks_executed=False)
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(receipt,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(ok=True,bytes=total,files=len(members),omitted=len(omitted))))


if __name__=='__main__':main()
