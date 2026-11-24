"""Curate the final EN/RU-only optional CPU graph from exact C2 wheel bytes.

Exclude only the unused Piper distribution and its CLI-only pathvalidate edge.
Preserve C2 implementation, original registries and installed prior revisions.
Every retained member is mapped through the reviewed archive plan and re-pinned.
"""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from mediaforge.infrastructure.models.optional_resources import load_cpu_profile,wheel_plan


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--downloads',required=True,type=Path)
    parser.add_argument('--receipt',required=True,type=Path)
    args=parser.parse_args()
    profile=load_cpu_profile();root=profile.root
    if profile.runtime.revision!='final1' or args.receipt.exists():parser.error('One exact final1 curation only.')
    before=(root/'cpu-profile.json').read_bytes()
    expected={r.name:r for r in profile.runtime.members}
    retained=set();assets=[];removed=[]
    for asset in profile.data['assets']:
        if asset['name'] in ('piper-tts','pathvalidate'):
            removed.append({k:asset[k] for k in ('name','version','sha256','size')});continue
        path=args.downloads/('asset-'+asset['sha256'])
        with path.open('rb') as stream:
            if path.stat().st_size!=asset['size'] or hashlib.file_digest(stream,'sha256').hexdigest()!=asset['sha256']:
                raise ValueError('Retained upstream wheel failed authentication.')
        with zipfile.ZipFile(path) as wheel:
            plan=wheel_plan(wheel,asset,expected)
            if retained.intersection(plan):raise ValueError('Cross-wheel member collision.')
            retained.update(plan)
        assets.append(asset)
    rows=[dict(name=r.name,size=r.size,sha256=r.sha256) for r in profile.runtime.members if r.name in retained]
    if len(rows)!=len(retained):raise ValueError('Incomplete retained C2 member set.')
    encoded=(json.dumps(rows,ensure_ascii=False,indent=2)+'\n').encode()
    (root/'cpu-runtime.json').write_bytes(encoded)
    data=profile.data
    data.update(assets=assets,runtime_revision='final2',manifest_name='cpu-runtime-final2.json',
                runtime_manifest_sha256=hashlib.sha256(encoded).hexdigest(),
                runtime_bytes=sum(r['size'] for r in rows),
                network_bytes=data['network_bytes']-sum(a['size'] for a in removed))
    data['final_delivery_curation']=dict(prior_profile_sha256=hashlib.sha256(before).hexdigest(),
        removed=removed,reason='No Piper inference selected; pathvalidate is used only by removed Piper CLI. Whisper/CT2/Argos worker imports retain exact upstream bytes. No C2 default/code/pin change.')
    raw=(json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode()
    (root/'cpu-profile.json').write_bytes(raw)
    result=dict(profile_sha256=hashlib.sha256(raw).hexdigest(),manifest_sha256=data['runtime_manifest_sha256'],
                files=len(rows),runtime_bytes=data['runtime_bytes'],network_bytes=data['network_bytes'],removed=removed)
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2)
    print(json.dumps(result))


if __name__=='__main__':main()
