"""Resolve declared core wheel provenance only; never install or execute wheels."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

PINS={'PySide6':'6.11.2','PySide6_Essentials':'6.11.2','PySide6_Addons':'6.11.2',
      'shiboken6':'6.11.2','numpy':'2.4.3','imagecodecs':'2026.11.11','tifffile':'2026.9.20'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists():p.error('New explicit core lock required.')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));assets=[];sources=[]
    for name,version in PINS.items():
        url=f'https://pypi.org/pypi/{name}/{version}/json'
        with opener.open(url,timeout=20) as response:raw=response.read(2*1024**2+1)
        if len(raw)>2*1024**2:raise ValueError('Metadata size bound.')
        data=json.loads(raw);rows=[]
        for row in data['urls']:
            f=row['filename']
            if row.get('yanked') or not f.endswith('.whl'):continue
            if f.endswith('none-any.whl') or (f.endswith('win_amd64.whl') and ('cp312-' in f or 'cp310-abi3-' in f)):
                rows.append(row)
        if len(rows)!=1:raise ValueError(f'Exact Windows CPython 3.12 asset is ambiguous for {name}: {[r["filename"] for r in rows]}')
        a=rows[0]
        assets.append(dict(name=name,version=version,filename=a['filename'],url=a['url'],size=a['size'],sha256=a['digests']['sha256']))
        sources.append(dict(url=url,sha256=hashlib.sha256(raw).hexdigest(),requires=data['info'].get('requires_dist'),license=data['info'].get('license')))
    assets.append(dict(name='CPython',version='3.12.16+20261026',
        filename='cpython-3.12.16+20261026-x86_64-pc-windows-msvc-install_only_stripped.tar.gz',
        url='https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16%2B20261026-x86_64-pc-windows-msvc-install_only_stripped.tar.gz',
        size=22014416,sha256='1f2ab53880fc67ea323398c6388e05af28addc99a8e389209f08a51fdff4b149'))
    value=dict(schema=1,platform='Windows x64 cp312',assets=assets,metadata=sources,
        python_provenance='https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/20261026',
        maximum_network=sum(a['size'] for a in assets),maximum_unpacked=2*1024**3,
        omitted_extras='No optional extras, demo tools, model weights or inference runtime in core.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(assets=len(assets),network=value['maximum_network'])))


if __name__=='__main__':main()
