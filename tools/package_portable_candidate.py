"""Manifest-exact INTERNAL QA ZIP and clean unpack; no models or user Data.

This creates no public release or license-clearance claim. Every member is
held, hashed, streamed and independently rechecked after clean extraction.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import time
import zipfile


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--build-receipt',type=Path,required=True)
    p.add_argument('--receipt',type=Path,required=True)
    p.add_argument('--package-generation',type=int,default=1)
    args=p.parse_args()
    if not 1<=args.package_generation<=20:p.error('Package generation must be within 1..20; no replacement.')
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths,local_path
    from mediaforge.infrastructure.models.core_runtime import CoreRuntime
    from mediaforge.infrastructure.filesystem.image_output import ReadLocks
    paths=ProfilePaths.for_test(args.root);paths.activate_temporary();app=paths.root.parent.parent
    if args.receipt.exists() or not args.receipt.is_relative_to(paths.logs):p.error('New managed receipt required.')
    build=json.loads(args.build_receipt.read_bytes())
    if build.get('ok') is not True or build.get('root')!=str(app):raise ValueError('Exact successful build receipt required.')
    destination=paths.data/f'portable-package-qa-{args.package_generation:02d}'
    if destination.exists():p.error('New owned QA output required; no replacement.')
    name=('MediaForge-internal-QA.zip' if args.package_generation==1 else
          f"MediaForge-Core{build['generation']:02d}-QA{args.package_generation:02d}.zip")
    destination.mkdir();artifact=destination/name;unpacked=destination/'unpacked/MediaForge'
    result=dict(ok=False,public_release_cleared=False,includes_ai_models=False,includes_user_data=False,
        build_receipt=str(args.build_receipt),core_generation=build.get('generation'),package_generation=args.package_generation,
        controller_sha256=sha(Path(__file__)))
    started=time.monotonic()
    try:
        with closing(CoreRuntime(app,build['manifest_sha256'])) as guard,ReadLocks() as held:
            held.parents(app);held.add(app/'MediaForge.exe')
            if sha(app/'MediaForge.exe')!=build['exe_sha256']:raise ValueError('Compiled executable differs.')
            files={file.relative_to(app).as_posix():file for file in guard.members}
            files.update({'MediaForge.exe':app/'MediaForge.exe','CoreManifest.tsv':app/'CoreManifest.tsv'})
            total=sum(f.stat().st_size for f in files.values())
            if total>2*1024**3 or len(files)>16002:raise ValueError('Core package exceeds exact limits.')
            used=sum(f.stat().st_size for f in paths.root.rglob('*') if f.is_file())
            if used+2*total+16*1024**2>20*1024**3:raise ValueError('QA copy/ZIP would exceed 20 GiB portable allocation bound.')
            if shutil.disk_usage(paths.root).free<2*total+2*1024**3:raise ValueError('Package headroom unavailable.')
            expected={name:(f.stat().st_size,sha(f)) for name,f in files.items()}
            with zipfile.ZipFile(artifact,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
                for name,file in sorted(files.items()):
                    info=zipfile.ZipInfo('MediaForge/'+name,(2026,10,28,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                    info.external_attr=0o100644<<16
                    with file.open('rb') as source,archive.open(info,'w') as target:
                        shutil.copyfileobj(source,target,1024**2)
                    if archive.fp.tell()>2*1024**3:raise ValueError('Archive exceeds bounded size.')
            zip_sha=sha(artifact)
        with ReadLocks() as held:
            held.parents(artifact.parent);held.add(artifact)
            if sha(artifact)!=zip_sha:raise ValueError('QA archive changed.')
            with zipfile.ZipFile(artifact) as archive:
                if {r.filename for r in archive.infolist()}!={'MediaForge/'+n for n in expected} or len(archive.infolist())!=len(expected):
                    raise ValueError('Archive inventory differs.')
                for row in archive.infolist():
                    name=row.filename.removeprefix('MediaForge/');size,digest=expected[name]
                    target=local_path(unpacked/name)
                    if not target.is_relative_to(unpacked) or row.file_size!=size:raise ValueError('Archive member differs.')
                    target.parent.mkdir(parents=True,exist_ok=True)
                    count=0;check=hashlib.sha256()
                    with archive.open(row) as source,target.open('xb') as output:
                        while raw:=source.read(1024**2):
                            count+=len(raw)
                            if count>size:raise ValueError('Archive expanded beyond exact member size.')
                            check.update(raw);output.write(raw)
                    if count!=size or check.hexdigest()!=digest:raise ValueError('Extracted core member differs.')
        with closing(CoreRuntime(unpacked,build['manifest_sha256'])):
            if sha(unpacked/'MediaForge.exe')!=build['exe_sha256']:raise ValueError('Unpacked executable differs.')
            if (unpacked/'Data').exists():raise ValueError('QA package included application data.')
        result.update(ok=True,zip=str(artifact),zip_sha256=zip_sha,zip_bytes=artifact.stat().st_size,
            unpacked=str(unpacked),unpacked_bytes=total,members=len(expected),
            manifest_sha256=build['manifest_sha256'],exe_sha256=build['exe_sha256'],
            independently_rehashed=True,launch_test_pending=True)
    except Exception as error:result['detail']=str(error)
    result['seconds']=time.monotonic()-started
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))
    if not result['ok']:raise SystemExit(2)


if __name__=='__main__':main()
