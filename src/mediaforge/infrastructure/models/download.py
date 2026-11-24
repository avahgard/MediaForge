"""Explicit bounded HTTPS archive acquisition; frozen pins supplied by registry."""
from dataclasses import dataclass
import hashlib
import re
import shutil
from urllib.parse import urlsplit
import urllib.request
from uuid import uuid4

from mediaforge.application.contracts.models import ModelError
from mediaforge.infrastructure.models.owned import Ownership


@dataclass(frozen=True)
class ArchiveSource:
    url:str
    sha256:str
    size:int
    prefix:str=''
    allowed_redirect_hosts:tuple[str,...]=()

    def __post_init__(self):
        parsed=urlsplit(self.url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443) or parsed.fragment:
            raise ValueError('Archive source must be explicit authenticated HTTPS.')
        if not re.fullmatch('[a-f0-9]{64}',self.sha256) or type(self.size) is not int or not 0<self.size<=2*1024**3:
            raise ValueError('Expected bounded archive size and frozen SHA-256.')
        if self.prefix and (not self.prefix.endswith('/') or any(not re.fullmatch('[A-Za-z0-9_-]+',p) for p in self.prefix.rstrip('/').split('/'))):
            raise ValueError('Unsafe archive prefix.')


@dataclass(frozen=True)
class DownloadReceipt:
    source:str
    final_url:str
    size:int
    sha256:str
    relative_path:str


def acquire(paths,source):
    allowed={urlsplit(source.url).hostname,*source.allowed_redirect_hosts}
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,request,fp,code,message,headers,newurl):
            target=urlsplit(newurl)
            if target.scheme!='https' or target.hostname not in allowed or target.username or target.password or target.port not in (None,443):
                raise ModelError('source_refused','Archive redirect is outside the approved HTTPS hosts.')
            return super().redirect_request(request,fp,code,message,headers,newurl)
    if shutil.disk_usage(paths.root).free<512*1024**2+source.size:
        raise ModelError('disk_budget','Insufficient portable download space.')
    try:
        with Ownership() as owner:
            owner.parents.parents(paths.downloads)
            target=paths.downloads/('.download-'+uuid4().hex)
            request=urllib.request.Request(source.url,headers={'Accept-Encoding':'identity','User-Agent':'MediaForge explicit model acquisition'})
            with urllib.request.build_opener(Redirect()).open(request,timeout=20) as response:
                if response.status!=200 or urlsplit(response.url).hostname not in allowed:
                    raise ModelError('source_refused','Unexpected archive HTTP response.')
                length=response.headers.get('Content-Length')
                if length is not None and int(length)!=source.size:
                    raise ModelError('source_refused','Archive Content-Length differs from its reviewed size.')
                owner.publish(target,response,source.size,source.sha256)
                receipt=DownloadReceipt(source.url,response.url,source.size,source.sha256,target.relative_to(paths.root).as_posix())
            return target,receipt
    except ModelError:raise
    except Exception as error:
        raise ModelError('download_failed','Explicit model acquisition failed; no alternate source or cache fallback used.') from error
