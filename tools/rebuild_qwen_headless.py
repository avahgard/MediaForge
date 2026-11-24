"""Reproducible source-only registry correction, no installation.

Use only against the explicitly pinned registry and exact original upstream wheel.
Historical task overlays are never edited. Produces a deterministic small wheel
as provenance; the installer still consumes the pinned upstream+overlay recipe.
"""
import argparse
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]/'src/mediaforge/resources/dub'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def dump(value):
    return (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-wheel', type=Path, required=True)
    parser.add_argument('--wheel-output', type=Path, required=True)
    parser.add_argument('--expected-profile', required=True)
    parser.add_argument('--from-version', required=True)
    parser.add_argument('--to-version', required=True)
    parser.add_argument('--runtime-revision', required=True)
    parser.add_argument('--modification', required=True)
    args = parser.parse_args()
    original = (ROOT/'qwen-profile.json').read_bytes()
    if sha(original) != args.expected_profile:
        parser.error('Exact prior registry required; do not repeat on another candidate.')
    if (not all(re.fullmatch(r'0\.1\.1\+mf\.headless12\.\d+', v) for v in (args.from_version,args.to_version))
            or args.from_version == args.to_version or not re.fullmatch(r'final\d+',args.runtime_revision)
            or not 1 <= len(args.modification) <= 500):
        parser.error('Explicit bounded source version/revision and modification required.')
    OLD = 'qwen_tts-'+args.from_version+'.dist-info'
    NEW = 'qwen_tts-'+args.to_version+'.dist-info'
    profile = json.loads(original)
    rows = json.loads((ROOT/'qwen-runtime.json').read_bytes())
    manifest = {r['name']: r for r in rows}
    asset = next(a for a in profile['assets'] if a['name'] == 'qwen-tts')
    if asset['version'] != args.from_version or asset['rename_to'] != OLD:
        parser.error('Source version differs from current pinned registry.')
    if sha(args.source_wheel.read_bytes()) != asset['sha256'] or args.wheel_output.exists():
        parser.error('Exact upstream wheel and new provenance output required.')
    old_meta = ROOT/'patches/qwen-tts'/OLD
    new_meta = ROOT/'patches/qwen-tts'/NEW
    new_meta.mkdir()
    for name in ('METADATA', 'MF_MODIFICATIONS.txt', 'RECORD'):
        raw = (old_meta/name).read_bytes().replace(args.from_version.encode(),args.to_version.encode())
        if name == 'MF_MODIFICATIONS.txt':
            raw += ('\nMediaForge '+args.to_version+': '+args.modification+'\n').encode()
        (new_meta/name).write_bytes(raw)
    asset['version'] = args.to_version
    asset['rename_to'] = NEW
    asset['omit'] = [n.replace(OLD, NEW) for n in asset['omit']]
    overlays = {}
    for name, row in asset['overlays'].items():
        name = name.replace(OLD, NEW)
        relative = row['path'].replace(OLD, NEW)
        raw = (ROOT/relative).read_bytes()
        overlays[name] = dict(path=relative, sha256=sha(raw), size=len(raw))
    asset['overlays'] = overlays
    members = {}
    with zipfile.ZipFile(args.source_wheel) as wheel:
        for name in wheel.namelist():
            if name.endswith('/'):
                continue
            mapped = name.replace(asset['rename_from']+'/', NEW+'/')
            if mapped in asset['omit']:
                continue
            members[mapped] = wheel.read(name)
    for name, row in overlays.items():
        members[name] = (ROOT/row['path']).read_bytes()
    record = NEW+'/RECORD'
    text = io.StringIO(newline=''); writer = csv.writer(text, lineterminator='\n')
    for name, raw in sorted(members.items()):
        if name != record:
            writer.writerow((name, 'sha256='+base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip('='), len(raw)))
    writer.writerow((record, '', ''))
    members[record] = text.getvalue().encode()
    (new_meta/'RECORD').write_bytes(members[record])
    overlays[record].update(size=len(members[record]), sha256=sha(members[record]))
    for name in list(manifest):
        if name.startswith(OLD+'/'):
            del manifest[name]
    for name, raw in members.items():
        manifest[name] = dict(name=name, size=len(raw), sha256=sha(raw))
    args.wheel_output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.wheel_output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as wheel:
        for name, raw in sorted(members.items()):
            entry = zipfile.ZipInfo(name, (2000, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED; entry.external_attr = 0o100644 << 16
            wheel.writestr(entry, raw, compresslevel=9)
    provenance = next(p for p in profile['source_provenance'] if p['name'] == 'qwen-tts')
    provenance.setdefault('prior_derived',[]).append(dict(version=args.from_version,sha256=provenance['derived']))
    provenance['derived'] = sha(args.wheel_output.read_bytes())
    provenance['recipe'] = 'tools/rebuild_qwen_headless.py; upstream wheel plus authenticated source overlays; deterministic ZIP'
    runtime = dump(sorted(manifest.values(), key=lambda r: r['name']))
    (ROOT/'qwen-runtime.json').write_bytes(runtime)
    profile.update(profile='qwen12-customvoice-cu128-'+args.runtime_revision, runtime_revision=args.runtime_revision,
                   manifest_name='qwen-cu128-'+args.runtime_revision+'.json', runtime_manifest_sha256=sha(runtime),
                   runtime_bytes=sum(r['size'] for r in manifest.values()))
    encoded = dump(profile)
    (ROOT/'qwen-profile.json').write_bytes(encoded)
    print(json.dumps(dict(profile_sha256=sha(encoded), manifest_sha256=sha(runtime),
                          wheel_sha256=provenance['derived'], runtime_bytes=profile['runtime_bytes'])))


if __name__ == '__main__':
    main()
