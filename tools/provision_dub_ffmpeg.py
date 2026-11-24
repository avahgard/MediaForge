"""Explicit verified test provisioning of the SAME managed FFmpeg pair.

Copies only hash-authenticated executables from an explicitly selected existing
test runtime. Never searches PATH or Legacy; no downloads or accepted mutations.
"""
import argparse
import hashlib
import io
from pathlib import Path
import json

from mediaforge.features.dub.contracts import DubControl
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest, FfmpegRuntimeResolver
from mediaforge.infrastructure.models.owned import Ownership


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--source-runtime', required=True, type=Path)
    args = parser.parse_args()
    paths = ProfilePaths.for_test(args.root); paths.activate_temporary()
    manifest = FfmpegManifest.load(); resolver = FfmpegRuntimeResolver(manifest)
    source = resolver.verify_directory(args.source_runtime)
    target = manifest.location(paths)
    facts = []
    with ReadLocks() as locks:
        for item, relative, expected in ((source.ffmpeg,manifest.ffmpeg_path,manifest.ffmpeg_sha256),
                                         (source.ffprobe,manifest.ffprobe_path,manifest.ffprobe_sha256)):
            locks.parents(item.parent); locks.add(item)
            with item.open('rb') as stream:
                actual = hashlib.file_digest(stream,'sha256').hexdigest()
            if actual != expected or item.stat().st_nlink != 1:
                raise ValueError('Existing runtime object differs from managed authority.')
            destination = target/relative
            if destination.exists():
                raise FileExistsError('Explicit new pair only; no replacement.')
            destination.parent.mkdir(parents=True,exist_ok=True)
            with Ownership() as owner, item.open('rb') as stream:
                owner.parents.parents(destination.parent)
                owner.publish(destination,stream,item.stat().st_size,expected)
            facts.append(dict(name=destination.name,sha256=expected,bytes=item.stat().st_size))
    verified = resolver.verify_directory(target)
    print(json.dumps(dict(verified=True,root=str(target),objects=facts)))


if __name__ == '__main__':
    main()
