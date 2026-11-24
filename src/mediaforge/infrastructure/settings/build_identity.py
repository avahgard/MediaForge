"""Display metadata can only come from the lifetime-held package manifest."""
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class BuildIdentity:
    version: str = ''
    build: str = ''
    manifest: str = ''
    release: bool = False
    project_url: str = ''
    notices: Path | None = None


def build_identity(core):
    if core is None:
        return BuildIdentity()
    metadata = core.root / 'Core' / 'BuildInfo.json'
    default = BuildIdentity(manifest=core.expected)
    if metadata not in core.members or core.members[metadata][0] > 8192:
        return default
    try:
        info = json.loads(metadata.read_bytes())
        if info['schema'] != 1 or info['channel'] not in ('development', 'release'):
            return default
        version, build = info['version'], info['build']
        if not re.fullmatch(r'[0-9][0-9A-Za-z.+-]{0,39}', version) or not re.fullmatch(r'[A-Za-z0-9 ._-]{1,80}', build):
            return default
        project = info.get('project_url', '')
        url = urlsplit(project)
        if not (url.scheme == 'https' and url.hostname and not url.username and not url.password):
            project = ''
        notice = info.get('notices', '')
        relative = PurePosixPath(notice)
        path = core.root.joinpath(*relative.parts)
        if not notice or path not in core.members or path.suffix.lower() not in ('.txt', '.md', '.pdf'):
            path = None
        return BuildIdentity(version, build, core.expected, info['channel'] == 'release', project, path)
    except (KeyError, TypeError, ValueError, OSError):
        return default
