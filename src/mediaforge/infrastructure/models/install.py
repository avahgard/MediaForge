"""Explicit hash-pinned acquisition/extraction into immutable revision directories."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import shutil
import stat
import zipfile
from uuid import uuid4

from mediaforge.application.contracts.models import ModelError, ModelMember
from mediaforge.infrastructure.filesystem.profile import local_path
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.models.owned import Ownership
from mediaforge.infrastructure.models.installation_lifecycle import Installation


def archive_members(archive, spec, prefix):
    """Inspect the entire directory before extracting any bytes; no extractall."""
    expected = {m.name:m for m in spec.members}
    found = {}
    total = 0
    rows = archive.infolist()
    if len(rows) > 1024:
        raise ModelError('archive_invalid', 'Archive has too many entries.')
    for row in rows:
        name = row.filename
        if row.flag_bits & 1 or '\\' in name or name.startswith('/') or name != name.rstrip('/') and not row.is_dir():
            raise ModelError('archive_invalid', 'Unsupported archive entry.')
        mode = row.external_attr >> 16
        if mode and stat.S_IFMT(mode) not in (0,stat.S_IFDIR,stat.S_IFREG):
            raise ModelError('archive_invalid', 'Links and special archive entries are forbidden.')
        # Validate even directory names and entries outside the selected prefix.
        try:ModelMember(name.rstrip('/'),1,'0'*64)
        except ValueError as error:raise ModelError('archive_invalid','Unsafe archive path.') from error
        if prefix and not name.startswith(prefix):
            raise ModelError('archive_invalid', 'Archive includes an unexpected package root.')
        relative = name[len(prefix):] if prefix else name
        if row.is_dir():
            if relative and not any(n.startswith(relative) for n in expected):
                raise ModelError('archive_invalid','Unexpected archive directory.')
            continue
        total += row.file_size
        if relative not in expected or relative in found or row.file_size != expected[relative].size or total > 2*1024**3:
            raise ModelError('archive_invalid','Archive inventory does not match the trusted manifest.')
        found[relative] = row
    if set(found) != set(expected):
        raise ModelError('archive_invalid','Archive members are incomplete.')
    return found


@contextmanager
def stage(store, spec, source, *, archive_sha256=None, archive_size=None, prefix=''):
    """Caller holds ModelService mutation + native reservation through commit.

    Never replace a revision in place. Failed/incomplete staging is quarantined
    under downloads, never considered installed or used as recovery authority.
    """
    paths=store.paths
    paths.prepare()
    source=local_path(Path(source))
    if shutil.disk_usage(paths.root).free < 512*1024**2 + sum(m.size for m in spec.members)*2:
        raise ModelError('disk_budget','Insufficient portable model staging space.')
    if store.location(spec).exists():
        raise ModelError('already_installed','Immutable revision already exists; remove explicitly before reinstalling.')
    directory=paths.downloads/('.install-'+uuid4().hex)
    with Installation(store,spec,directory) as transaction, ReadLocks() as inputs:
        owner=transaction.owner
        inputs.parents(source if source.is_dir() else source.parent)
        def copy_members(open_member):
            with ReadLocks() as nested:
                for member in spec.members:
                    target=directory/member.name
                    folder=directory
                    for part in Path(member.name).parts[:-1]:
                        folder=folder/part
                        local_path(folder)
                        folder.mkdir(exist_ok=True)
                        nested.add(folder,True)
                    with open_member(member.name) as stream:
                        owner.publish(target,stream,member.size,member.sha256)
        if source.is_file():
            inputs.add(source)
            if source.stat().st_nlink!=1 or source.stat().st_size!=archive_size or not archive_sha256:
                raise ModelError('archive_invalid','Expected single-link archive identity/size required.')
            h=hashlib.sha256()
            with source.open('rb') as stream:
                while block:=stream.read(1024*1024):h.update(block)
            if h.hexdigest()!=archive_sha256:
                raise ModelError('archive_invalid','Archive digest mismatch.')
            with zipfile.ZipFile(source) as archive:
                found=archive_members(archive,spec,prefix)
                copy_members(lambda name:archive.open(found[name]))
        else:
            input_guard=store.verify_at(spec,source)
            try:copy_members(lambda name:(source/name).open('rb'))
            finally:input_guard.close()
        transaction.guard=store.verify_at(spec,directory,owned_root=True)
        transaction.close_guard() # release child sharing before directory rename
        transaction.activate()
        transaction.guard=store.verify_at(spec,transaction.target,owned_root=True)
        yield transaction.guard


def retire(store,spec):
    """Remove installed eligibility without modifying/deleting any old bytes.

    Ordinary corrupt files may be quarantined; aliases are refused. Quarantines
    and abandoned stages are never adopted by inventory or recovery.
    """
    path=local_path(store.location(spec))
    with Ownership() as owner:
        owner.parents.parents(path.parent)
        owner.parents.parents(store.paths.downloads)
        handle=owner.open(path,0x10080,3,3,directory=True)
        with ReadLocks() as members:
            count=0;size=0
            for file in path.rglob('*'):
                count+=1
                if count>1024:raise ModelError('corrupt','Quarantine inventory exceeds bounds.')
                local_path(file)
                if file.is_dir():members.add(file,True)
                else:
                    locked=members.add(file);owner.info(locked)
                    size+=file.stat().st_size
                    if size>2*1024**3:raise ModelError('corrupt','Quarantine size exceeds bounds.')
        target=store.paths.downloads/('.retired-'+spec.model_id+'-'+uuid4().hex)
        owner.rename(handle,target)
        return target
