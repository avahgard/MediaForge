"""Explicit bounded build-only core acquisition, no package hooks or install."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
import urllib.request

from mediaforge.features.dub.contracts import DubControl
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.models.owned import Ownership

HOSTS={'files.pythonhosted.org','github.com','release-assets.githubusercontent.com'}
MAXIMUM_NETWORK=300814165


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,message,headers,target):
        checked(target)
        return super().redirect_request(request,fp,code,message,headers,target)


def checked(url):
    u=urlsplit(url)
    if u.scheme!='https' or u.hostname not in HOSTS or u.username or u.password or u.port not in (None,443):
        raise ValueError('Undeclared core source/redirect.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--lock',type=Path,required=True)
    p.add_argument('--receipt',type=Path,required=True);args=p.parse_args()
    paths=ProfilePaths.for_test(args.root);paths.activate_temporary();control=DubControl()
    if args.receipt.exists() or not args.receipt.is_relative_to(paths.logs):p.error('New portable receipt required.')
    encoded=args.lock.read_bytes();lock=json.loads(encoded);assets=lock['assets']
    if len(assets)!=8 or sum(a['size'] for a in assets)!=MAXIMUM_NETWORK or lock['maximum_network']!=MAXIMUM_NETWORK:
        raise ValueError('Reviewed core acquisition bound differs.')
    rows=[];network=0;opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),Redirect())
    try:
        for asset in assets:
            checked(asset['url']);target=paths.downloads/('core-'+asset['sha256'])
            if target.exists():
                with ReadLocks() as leases:
                    leases.parents(target.parent);leases.add(target)
                    with target.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
                    if actual!=asset['sha256'] or target.stat().st_nlink!=1 or target.stat().st_size!=asset['size']:
                        raise ValueError('Existing core object differs; no replacement.')
                rows.append(dict(name=asset['filename'],size=asset['size'],sha256=actual,reused=True));continue
            began=time.monotonic()
            class Stream:
                def read(self,n):
                    nonlocal network
                    control.check()
                    if time.monotonic()-began>600:raise TimeoutError('Core object exceeded ten-minute bound.')
                    value=response.read(n);network+=len(value)
                    if network>MAXIMUM_NETWORK:raise ValueError('Core network payload exceeded its exact bound.')
                    return value
            with opener.open(urllib.request.Request(asset['url'],headers={'Accept-Encoding':'identity'}),timeout=20) as response:
                checked(response.url)
                if response.status!=200 or response.headers.get('Content-Encoding','identity')!='identity':
                    raise ValueError('Unexpected core response.')
                if response.headers.get('Content-Length') and int(response.headers['Content-Length'])!=asset['size']:
                    raise ValueError('Core response length differs.')
                with Ownership() as owner:
                    owner.parents.parents(paths.downloads)
                    owner.publish(target,Stream(),asset['size'],asset['sha256'])
            rows.append(dict(name=asset['filename'],size=asset['size'],sha256=asset['sha256'],reused=False))
            print(json.dumps(dict(acquired=asset['name'],network=network)),flush=True)
        result=dict(ok=True)
    except Exception as error:result=dict(ok=False,detail=str(error))
    result.update(lock_sha256=hashlib.sha256(encoded).hexdigest(),network_payload=network,objects=rows)
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2)
    if not result['ok']:raise RuntimeError(result['detail'])


if __name__=='__main__':main()
