"""Explicit bounded acquisition of one public MIT OCW native qualification source.

Development-only, not a model or application download route. Files are untrusted
media: archive metadata/provenance is NOT code execution or model authority.
No material from this corpus is bundled with the application or used for training.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit,quote
import urllib.request

COLLECTION='MIT6.006F11'
METADATA='https://archive.org/metadata/'+COLLECTION
ORIGIN='https://ocw.mit.edu/courses/6-006-introduction-to-algorithms-fall-2011/'
TERMS='https://ocw.mit.edu/pages/privacy-and-terms-of-use/'
MAX_SOURCE=150*1024**2


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True)
    p.add_argument('--name',help='Explicit metadata-listed 300k MP4; omit for metadata only.')
    p.add_argument('--resume-receipt',type=Path,help='Explicit failed corpus receipt; preserve the original partial and copy into a NEW object.')
    args=p.parse_args()
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    paths=ProfilePaths.for_test(args.root);paths.activate_temporary()
    if args.receipt.exists() or not args.receipt.is_relative_to(paths.logs):p.error('New portable receipt required.')
    calls=[];received=0;started=time.monotonic()
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,request,fp,code,message,headers,url):
            allowed(url)
            if len(calls)>=8:raise ValueError('Corpus redirect budget exhausted.')
            calls.append(dict(url=url,kind='redirect'))
            return super().redirect_request(request,fp,code,message,headers,url)
    def allowed(url):
        value=urlsplit(url)
        if (value.scheme!='https' or value.username or value.password or value.port not in (None,443)
                or not (value.hostname=='archive.org' or value.hostname.endswith('.archive.org'))):
            raise ValueError('Corpus download escaped the declared HTTPS origin.')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),Redirect())
    def open_(url,*,method='GET',headers=None):
        allowed(url);calls.append(dict(url=url,kind='request'))
        return opener.open(urllib.request.Request(url,method=method,headers={'User-Agent':'MediaForge-native-qualification/1.0',**(headers or {})}),timeout=20)
    result=dict(ok=False,origin=ORIGIN,terms=TERMS,license='CC BY-NC-SA, MIT OCW attribution retained; local qualification only',
        acquisition_limit=MAX_SOURCE+2*1024**2)
    try:
        with open_(METADATA) as response:
            raw=response.read(2*1024**2+1);received+=len(raw)
        if len(raw)>2*1024**2:raise ValueError('Corpus metadata exceeds its bound.')
        metadata=json.loads(raw)
        rows=[{k:r.get(k) for k in ('name','size','sha1','md5','length')} for r in metadata['files']
              if r.get('name','').endswith('_300k.mp4')]
        result.update(metadata_sha256=hashlib.sha256(raw).hexdigest(),candidates=rows)
        if args.name:
            selected=next((r for r in rows if r['name']==args.name),None)
            if (not selected or Path(args.name).name!=args.name or not 0<int(selected['size'])<=MAX_SOURCE
                    or len(selected.get('sha1') or '')!=40):raise ValueError('Select one bounded metadata-listed source.')
            target=paths.root/'fixtures'/args.name
            original=paths.downloads/('corpus-'+args.name+'.partial')
            partial=original if not args.resume_receipt else paths.downloads/('corpus-'+args.name+'.resume1.partial')
            if target.exists() or partial.exists():raise ValueError('Corpus object already exists; no replacement or automatic retry.')
            size=0;sha=hashlib.sha256();sha1=hashlib.sha1()
            url=f'https://archive.org/download/{COLLECTION}/'+quote(args.name)
            with partial.open('xb') as output:
                headers={}
                if args.resume_receipt:
                    if not args.resume_receipt.is_relative_to(paths.logs):raise ValueError('Portable prior receipt required.')
                    previous=json.loads(args.resume_receipt.read_bytes())
                    if (previous.get('ok') is not False or previous.get('detail')!='Corpus acquisition exceeded declared size or deadline.'
                            or selected not in previous.get('candidates',[])):
                        raise ValueError('Explicit resume has no matching failed asset receipt.')
                    from mediaforge.infrastructure.filesystem.image_output import ReadLocks
                    with ReadLocks() as locks:
                        locks.parents(original.parent);locks.add(original)
                        if original.stat().st_nlink!=1 or not 0<original.stat().st_size<int(selected['size']):
                            raise ValueError('Unqualified prior source fragment.')
                        old_size=original.stat().st_size;old_sha=hashlib.sha256()
                        with original.open('rb') as source:
                            while block:=source.read(1024*1024):
                                output.write(block);sha.update(block);sha1.update(block);old_sha.update(block);size+=len(block)
                        result['preserved_fragment']=dict(path=str(original),bytes=old_size,sha256=old_sha.hexdigest())
                    with open_(url,method='HEAD') as response:
                        tag=response.headers.get('ETag');length=response.headers.get('Content-Length')
                    if not tag or tag.startswith('W/') or length!=selected['size']:
                        raise ValueError('Strong ETag and complete length are required for explicit Range recovery.')
                    headers={'Range':f'bytes={size}-','If-Range':tag}
                    result['range_etag']=tag
                with open_(url,headers=headers) as response:
                    if args.resume_receipt and (response.status!=206 or response.headers.get('ETag')!=result['range_etag']
                            or response.headers.get('Content-Range')!=f'bytes {size}-{int(selected["size"])-1}/{selected["size"]}'):
                        raise ValueError('Server did not establish exact fresh Range/ETag continuity.')
                    while block:=response.read(1024*1024):
                        size+=len(block);received+=len(block)
                        if size>int(selected['size']) or time.monotonic()-started>300:
                            raise ValueError('Corpus acquisition exceeded declared size or deadline.')
                        output.write(block);sha.update(block);sha1.update(block)
            if size!=int(selected['size']) or sha1.hexdigest()!=selected['sha1']:
                raise ValueError('Corpus metadata checksum/length differs; retained as untrusted partial.')
            partial.rename(target)
            result.update(source=str(target),source_bytes=size,source_sha256=sha.hexdigest(),upstream_sha1=sha1.hexdigest())
        result['ok']=True
    except Exception as error:result.update(detail=str(error))
    result.update(calls=calls,body_bytes_received=received,seconds=time.monotonic()-started,
        accounting='Instrumented urllib requests/redirects and response bodies only; previous web lookup transport totals are unknown.')
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    summary={k:result.get(k) for k in ('ok','detail','source_bytes','body_bytes_received')}
    if not args.name:summary['candidates']=result.get('candidates')
    print(json.dumps(summary,ensure_ascii=False))
    if not result['ok']:raise SystemExit(2)


if __name__=='__main__':main()
