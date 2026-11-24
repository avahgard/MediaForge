"""Build the private manifest-held launcher and shell, never include Data/AI."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

from mediaforge.infrastructure.filesystem.profile import ProfilePaths,local_path
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime,FfmpegManifest

REPO=Path(__file__).resolve().parents[1]
CSC=Path('C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe')


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--unpack-receipt',type=Path,required=True)
    p.add_argument('--receipt',type=Path,required=True);args=p.parse_args()
    paths=ProfilePaths.for_test(args.root);paths.activate_temporary();root=paths.root.parent.parent;core=root/'Core'
    if args.receipt.exists() or (core/'App').exists() or (root/'MediaForge.exe').exists():
        p.error('New application payload and launcher required; existing build/evidence preserved.')
    previous=json.loads(args.unpack_receipt.read_bytes())
    if previous['core']!=str(core):raise ValueError('Unpack receipt points elsewhere.')
    project=tomllib.loads((REPO/'pyproject.toml').read_text(encoding='utf-8'))['project']
    version=project['version']
    if project['name']!='mediaforge' or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',version):
        raise ValueError('Expected the MediaForge project and a three-part release version.')
    expected={m['path']:m for m in previous['members']}
    with ReadLocks() as lease:
        lease.parents(core)
        actual=[]
        for file in core.rglob('*'):
            local_path(file)
            if file.is_dir():continue
            name=file.relative_to(core).as_posix();actual.append(name);row=expected.get(name)
            if row is None or file.stat().st_nlink!=1 or file.stat().st_size!=row['size'] or sha(file)!=row['sha256']:
                raise ValueError('Unpacked core differs from authenticated archive bytes.')
        if set(actual)!=set(expected):raise ValueError('Incomplete core bytes.')
        app=core/'App';app.mkdir()
        for source in (REPO/'src/mediaforge').rglob('*'):
            if source.is_dir() or '__pycache__' in source.parts or source.suffix=='.pyc':continue
            local_path(source);lease.parents(source.parent);lease.add(source)
            output=app/'mediaforge'/source.relative_to(REPO/'src/mediaforge')
            output.parent.mkdir(parents=True,exist_ok=True)
            with source.open('rb') as incoming,output.open('xb') as result:shutil.copyfileobj(incoming,result)
            if sha(output)!=sha(source):raise ValueError('Application copy differs.')
        metadata=core/f'Site/mediaforge-{version}.dist-info';metadata.mkdir()
        (metadata/'METADATA').write_text(
            f'Metadata-Version: 2.1\nName: mediaforge\nVersion: {version}\n',encoding='utf-8')
        shutil.copyfile(REPO/'packaging/portable_boot.py',core/'portable_boot.py')
        provenance=core/'Provenance';provenance.mkdir()
        for source in ('core-lock.json','Launcher.cs'):
            shutil.copyfile(REPO/'packaging'/source,provenance/source)
        runtime=resolve_ffmpeg_runtime(profile=paths);manifest=FfmpegManifest.load()
        external=root/'external';(external/'manifests').mkdir(parents=True)
        shutil.copyfile(REPO/'external/manifests/ffmpeg.json',external/'manifests/ffmpeg.json')
        for source,name,expected_hash in ((runtime.ffmpeg,manifest.ffmpeg_path,manifest.ffmpeg_sha256),
                                          (runtime.ffprobe,manifest.ffprobe_path,manifest.ffprobe_sha256)):
            lease.parents(source.parent);lease.add(source)
            if sha(source)!=expected_hash:raise ValueError('Managed FFmpeg object changed.')
            output=external/'ffmpeg'/name;output.parent.mkdir(parents=True,exist_ok=True)
            with source.open('rb') as incoming,output.open('xb') as result:shutil.copyfileobj(incoming,result)
            if sha(output)!=expected_hash:raise ValueError('Shipped FFmpeg differs.')
    rows=[]
    for parent in (core,root/'external'):
        for file in parent.rglob('*'):
            if file.is_file():rows.append((file.relative_to(root).as_posix(),file.stat().st_size,sha(file)))
    rows.sort()
    encoded=''.join(f'{digest}\t{size}\t{name}\n' for name,size,digest in rows).encode('utf-8')
    with (root/'CoreManifest.tsv').open('xb') as stream:stream.write(encoded)
    manifest_sha=hashlib.sha256(encoded).hexdigest()
    generated=paths.temporary/'Launcher-final-01.cs'
    with generated.open('x',encoding='utf-8') as stream:
        stream.write((REPO/'packaging/Launcher.cs').read_text(encoding='utf-8').replace('@MANIFEST_SHA256@',manifest_sha))
    references=[str(CSC.parent/name) for name in ('mscorlib.dll','System.dll','System.Core.dll','System.Windows.Forms.dll','System.Drawing.dll')]
    command=[str(CSC),'/nologo','/noconfig','/nostdlib+','/target:winexe','/platform:x64','/optimize+','/debug-',
        '/codepage:65001',*['/reference:'+path for path in references],'/out:'+str(root/'MediaForge.exe'),str(generated)]
    completed=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,timeout=60,
        cwd=paths.temporary,env=paths.child_environment({}),creationflags=subprocess.CREATE_NO_WINDOW)
    result=dict(ok=completed.returncode==0,version=version,compiler=str(CSC),compiler_sha256=sha(CSC),exit=completed.returncode,
        diagnostic=(completed.stdout+completed.stderr).decode('utf-8',errors='replace')[-8192:],
        manifest_sha256=manifest_sha,files=len(rows),bytes=sum(r[1] for r in rows),root=str(root),
        includes_ai_models=False,requires_system_python=False,requires_dotnet='Windows .NET Framework 4.x launcher only')
    if result['ok']:result['exe_sha256']=sha(root/'MediaForge.exe')
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(result))
    if completed.returncode:raise SystemExit(completed.returncode)


if __name__=='__main__':main()
