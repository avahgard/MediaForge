"""Offline, explicit developer build of the pending-review Edge profile.

Never imported by the application. Requires all publisher-hash-pinned wheels
already downloaded, a fresh injected portable root and the project .venv.
Does not activate a provider, contact any network or alter the offline C2 runtime.
"""
import argparse,hashlib,json,shutil,struct,sys,time,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def inspect_wheel(path):
    total=0;seen=set()
    reserved={'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
    with zipfile.ZipFile(path) as archive:
        if len(archive.infolist())>3000:raise ValueError('Wheel member bound exceeded')
        for member in archive.infolist():
            # ZipInfo normalizes Windows backslashes and truncates NUL names.
            # Inspect the original archive spelling before either can hide it.
            name=member.orig_filename.rstrip('/')
            if (not name or name.startswith('/') or '\\' in name or '\0' in name
                    or any(not p or p in ('.','..') or any(c in p for c in ':*?<>|"') or p.endswith((' ','.'))
                        or p.split('.')[0].upper() in reserved for p in name.split('/'))
                    or name.casefold() in seen or member.external_attr>>16&0o170000==0o120000):
                raise ValueError('Unsafe/duplicate wheel member')
            seen.add(name.casefold());total+=member.file_size
            if member.file_size>16*1024**2 or total>128*1024**2:raise ValueError('Wheel expanded-size bound exceeded')
        if archive.testzip() is not None:raise ValueError('Wheel CRC failure')
    return total

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheels',type=Path,required=True)
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args();target=args.root
    if sys.platform!='win32' or struct.calcsize('P')!=8 or sys.version_info[:2]!=(3,12):raise ValueError('Qualified Windows x64 CPython 3.12 required')
    if not target.is_absolute() or target.exists():raise ValueError('Specify a fresh absolute portable data root')
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.infrastructure.models.owned import Ownership
    specpath=ROOT/'external/manifests/dub-edge-c3-development.json';spec=json.loads(specpath.read_text('utf-8'))
    if sha(ROOT/'external/locks/dub-edge-c3-windows-cp312.lock')!=spec['lock_sha256']:raise ValueError('Dependency lock identity mismatch')
    expanded=0
    for wheel in spec['wheels']:
        path=args.wheels/wheel['url'].rsplit('/',1)[-1]
        if path.stat().st_size!=wheel['bytes'] or sha(path)!=wheel['sha256']:raise ValueError('Publisher wheel identity mismatch')
        expanded+=inspect_wheel(path)
    if expanded>128*1024**2:raise ValueError('Aggregate expanded-size bound exceeded')
    existing=next(p for p in target.parents if p.exists())
    if shutil.disk_usage(existing).free<512*1024**2:raise ValueError('Insufficient qualification disk headroom')
    paths=ProfilePaths.for_test(target);paths.prepare();started=time.monotonic()
    site=target/'runtime/site';site.mkdir(parents=True)
    # These exact wheels have flat purelib/platlib layouts. Materialize their
    # authenticated members only: no timestamped CLI launchers, .pth execution,
    # bytecode, mutable installer metadata or ambient dependency resolver.
    # A different wheel layout is a new qualification decision, not a fallback.
    seen=set()
    with Ownership() as owned:
        owned.parents.parents(site)
        for wheel in spec['wheels']:
            path=args.wheels/wheel['url'].rsplit('/',1)[-1]
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    if member.is_dir():continue
                    name=member.filename
                    if any(p.endswith('.data') for p in name.split('/')) or name.casefold() in seen:
                        raise ValueError('Unsupported/shared wheel member layout')
                    seen.add(name.casefold());destination=site/name
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    owned.parents.parents(destination.parent)
                    with archive.open(member) as source:
                        raw=source.read(16*1024**2+1)
                    if len(raw)!=member.file_size:raise ValueError('Wheel member length mismatch')
                    import io
                    owned.publish(destination,io.BytesIO(raw),len(raw),hashlib.sha256(raw).hexdigest())
    for change in spec['adaptations']:
        path=site/change['member']
        if sha(path)!=change['before_sha256']:raise ValueError('Adaptation base mismatch')
        raw=path.read_bytes();old=change['old'].encode();new=change['new'].encode()
        if raw.count(old)!=change['occurrences']:raise ValueError('Adaptation occurrence mismatch')
        path.write_bytes(raw.replace(old,new))
        if sha(path)!=change['after_sha256']:raise ValueError('Adaptation output mismatch')
    members=[dict(name=p.relative_to(site).as_posix(),size=p.stat().st_size,sha256=sha(p)) for p in sorted(site.rglob('*')) if p.is_file()]
    raw=json.dumps(members,separators=(',',':')).encode()
    if members!=spec['members'] or hashlib.sha256(raw).hexdigest()!=spec['manifest_sha256']:
        (paths.manifests/'unqualified-observed.json').write_bytes(raw)
        raise ValueError('Installed bytes differ from reviewed candidate; no activation')
    with Ownership() as owned:
        import io
        owned.parents.parents(paths.manifests)
        owned.publish(paths.manifests/'runtime.json',io.BytesIO(raw),len(raw),spec['manifest_sha256'])
    receipt=dict(approval=spec['approval'],spec_sha256=sha(specpath),manifest_sha256=spec['manifest_sha256'],
        wheels=len(spec['wheels']),members=len(members),expanded_bytes=expanded,elapsed=time.monotonic()-started,
        python=sys.version,builder='verified wheel members; explicit recorded source adaptations; no pip/CLI generation',
        activation=False,network=False)
    (paths.manifests/'build-receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt,indent=2))

if __name__=='__main__':main()
