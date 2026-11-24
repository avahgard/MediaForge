"""Explicit optional Qwen resources; inert installation is never inference Ready.

No pip, install hooks, model imports, shell helpers or driver enrollment. The
trusted application registry supplies both upstream and transformed byte pins.
The accepted C2 Installation controller owns activation/rollback unchanged.
"""
from contextlib import contextmanager, closing
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import shutil
import stat
import time
from urllib.parse import urlsplit
import urllib.request
from uuid import uuid4
import zipfile

from mediaforge.application.contracts.model_acquisition import DownloadAuthority
from mediaforge.application.contracts.models import ModelError, ModelMember
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.filesystem.profile import local_path
from mediaforge.infrastructure.models.installation_lifecycle import Installation, pending_path
from mediaforge.infrastructure.models.owned import Ownership, ModelMutex
from mediaforge.infrastructure.models.store import PortableModelStore, Guard


RESOURCE_ROOT = Path(__file__).parents[2] / 'resources' / 'dub'
PROFILE_SHA256 = 'f3a2b6820f6ec14603806ac8d0a3097fe7904c9447c05c53903a8e8ddb4d650f'
CPU_PROFILE_SHA256 = 'b7b73296453f41db5afa1498d20f8c1bd58ffe9b443bd3f02547a1c1e6513872'
MAX_RUNTIME_BYTES = 6 * 1024**3
MAX_OBJECTS = 32768 + 8192
MAX_ACQUISITION = 6 * 1024**3
MAX_DISK = 20 * 1024**3


def digest(path, control):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            control.check()
            result.update(block)
    return result.hexdigest()


def file_matches(path, size, expected, control):
    local_path(path)
    with ReadLocks() as locks:
        locks.parents(path.parent)
        locks.add(path)
        info = path.stat()
        if info.st_nlink != 1:
            raise ModelError('unsafe_alias', 'A managed file has an unsafe link count.')
        return info.st_size == size and digest(path, control) == expected


def safe_member(name):
    if not isinstance(name, str) or not name or len(name) > 1024:
        raise ModelError('archive_invalid', 'Invalid optional member name.')
    parts = name.split('/')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                *(f'LPT{i}' for i in range(1, 10))}
    if any(not p or p in ('.', '..') or p.endswith((' ', '.'))
           or p.split('.')[0].upper() in reserved
           or any(ord(c) < 32 or c in '\\:<>"|?*' for c in p) for p in parts):
        raise ModelError('archive_invalid', 'Unsafe optional member path.')
    return name


@dataclass(frozen=True)
class RuntimeMember:
    name: str
    size: int
    sha256: str

    def __post_init__(self):
        import re
        safe_member(self.name)
        if (type(self.size) is not int or not 0 <= self.size <= 2 * 1024**3
                or not re.fullmatch('[0-9a-f]{64}', self.sha256)):
            raise ValueError('Invalid dedicated runtime member pin.')


@dataclass(frozen=True)
class ResourceSpec:
    model_id: str
    revision: str
    members: tuple


@dataclass(frozen=True)
class ResourceProfile:
    data: dict
    runtime: ResourceSpec
    models: tuple
    root: Path


def load_profile():
    return _load_trusted_profile('qwen-profile.json', PROFILE_SHA256)


def load_cpu_profile():
    profile = _load_trusted_profile('cpu-profile.json', CPU_PROFILE_SHA256)
    if (len(profile.runtime.members) > 10000 or profile.data['runtime_bytes'] > 2 * 1024**3
            or {s.model_id for s in profile.models} != {'whisper-base', 'argos-en-ru'}):
        raise ModelError('unqualified', 'CPU profile exceeds the accepted provider boundary.')
    return profile


def _load_trusted_profile(filename, trusted_digest):
    """Only the application pin is authority, never an installed-side manifest."""
    raw = (RESOURCE_ROOT / filename).read_bytes()
    if len(raw) > 512 * 1024 or hashlib.sha256(raw).hexdigest() != trusted_digest:
        raise ModelError('unqualified', 'Application Qwen profile failed authentication.')
    data = json.loads(raw)
    manifest = (RESOURCE_ROOT / data.get('manifest_resource', 'qwen-runtime.json')).read_bytes()
    if len(manifest) > 8 * 1024**2 or hashlib.sha256(manifest).hexdigest() != data['runtime_manifest_sha256']:
        raise ModelError('unqualified', 'Application runtime member pins differ.')
    rows = json.loads(manifest)
    if not 0 < len(rows) <= 32768 or sum(r['size'] for r in rows) > MAX_RUNTIME_BYTES:
        raise ModelError('unqualified', 'Runtime profile exceeds its dedicated bounds.')
    # A runtime is a separate resource profile, not a widened C2 ModelSpec.
    members = tuple(RuntimeMember(r['name'], r['size'], r['sha256']) for r in rows)
    if len({r.name.casefold() for r in members}) != len(members):
        raise ModelError('unqualified', 'Ambiguous runtime member identity.')
    runtime = ResourceSpec(data.get('runtime_id', 'qwen-runtime-cu128'), data.get('runtime_revision', 'final1'), members)
    models = tuple(ResourceSpec(m['model_id'], m['revision'],
                                tuple(ModelMember(r['name'], r['size'], r['sha256']) for r in m['members']))
                   for m in data['models'])
    if data['network_bytes'] > MAX_ACQUISITION:
        raise ModelError('disk_budget', 'Optional profile exceeds acquisition ceiling.')
    return ResourceProfile(data, runtime, models, RESOURCE_ROOT)


class OptionalResourceStore(PortableModelStore):
    """Qwen-only inventory limits; no modifications to the accepted C2 store."""
    def verify_at(self, spec, path, *, owned_root=False, control=None):
        guard = Guard(path)
        try:
            local_path(path)
            if not owned_root:
                guard.locks.parents(path)
            expected = {m.name: m for m in spec.members}
            directories = {p.as_posix() for n in expected for p in Path(n).parents if p != Path('.')}
            seen, seen_dirs = set(), set()
            count = 0
            for item in path.rglob('*'):
                if control:
                    control.check()
                count += 1
                if count > MAX_OBJECTS:
                    raise ModelError('corrupt', 'Optional inventory object limit exceeded.')
                info = item.lstat()
                if info.st_file_attributes & 0x400:
                    raise ModelError('corrupt', 'Aliased optional member.')
                name = item.relative_to(path).as_posix()
                if item.is_dir():
                    if name not in directories:
                        raise ModelError('corrupt', 'Unexpected optional directory.')
                    guard.locks.add(item, True)
                    seen_dirs.add(name)
                else:
                    member = expected.get(name)
                    if member is None or info.st_nlink != 1 or info.st_size != member.size:
                        raise ModelError('corrupt', 'Extra, aliased or resized optional member.')
                    handle = guard.locks.add(item)
                    from mediaforge.infrastructure.models.native_guard import identity
                    _, opened = identity(guard.locks.api, handle)
                    if opened.links != 1 or opened.attributes & 0x400:
                        raise ModelError('corrupt', 'Opened optional object differs.')
                    check = control or _Uncancelled()
                    if digest(item, check) != member.sha256:
                        raise ModelError('corrupt', 'Optional resource hash mismatch.')
                    seen.add(name)
            if seen != set(expected) or seen_dirs != directories:
                raise ModelError('corrupt', 'Incomplete optional resource inventory.')
            return guard
        except BaseException:
            guard.close()
            raise


class _Uncancelled:
    def check(self):
        pass


class _CheckedStream:
    def __init__(self, stream, control, advance):
        self.stream, self.control, self.advance = stream, control, advance

    def read(self, size):
        self.control.check()
        result = self.stream.read(min(size, 1024 * 1024))
        self.control.check()
        if result:
            self.advance(len(result))
        return result


PUBLISHER_ENDPOINTS = {
    'PyPI': (frozenset({'files.pythonhosted.org'}), frozenset()),
    'PyTorch': (frozenset({'download-r2.pytorch.org', 'download.pytorch.org'}), frozenset()),
    'Hugging Face': (frozenset({'huggingface.co'}), frozenset({
        'cdn-lfs.huggingface.co', 'cdn-lfs-us-1.huggingface.co',
        'cas-bridge.xethub.hf.co', 'us.aws.cdn.hf.co',
    })),
    'Argos Translate': (frozenset({'data.argosopentech.com', 'argos-net.com'}), frozenset()),
}


def https_host(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme == 'https' and parsed.username is None
                and parsed.password is None and parsed.port in (None, 443)):
            return parsed.hostname
    except ValueError:
        pass
    return None


def download_authority(asset):
    url, size, expected = asset['url'], asset['size'], asset['sha256']
    host = https_host(url)
    for publisher, (origins, cdns) in PUBLISHER_ENDPOINTS.items():
        if host in origins and 0 < size <= 3 * 1024**3:
            return DownloadAuthority(url, size, expected, publisher, origins | cdns)
    raise ModelError('source_refused', 'Source is outside the frozen optional registry.')


def download(paths, asset, control, progress, *, allow_network=True, authority=None):
    """One explicit verified object; no hidden retry/proxy/global cache fallback."""
    registered = download_authority(asset)
    if authority is None:
        authority = registered  # The standalone registry installer also stays within the asset's publisher.
    elif authority != registered:
        raise ModelError('consent_changed', 'The authorized download changed. Review a fresh plan.')
    url, size, expected = authority.url, authority.size, authority.sha256

    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, target):
            try:
                control.check()
                if https_host(target) not in authority.hosts:
                    raise ModelError('source_refused', 'Redirect is outside this asset’s authorized publisher endpoints.')
                return super().redirect_request(request, fp, code, message, headers, target)
            except BaseException:
                # This runs before urllib asks its opener to follow the redirect.
                # On refusal, urllib does not retire the original response itself.
                if fp is not None:
                    fp.close()
                raise

    progress(0)  # identify the pending object before DNS/redirect/body activity
    target = paths.downloads / ('asset-' + expected)
    # An existing object is not trusted by its filename. Its complete bytes are
    # reauthenticated under a lease before explicit reuse.
    if target.exists():
        with ReadLocks() as locks:
            locks.parents(target.parent)
            locks.add(target)
            if target.stat().st_nlink != 1 or target.stat().st_size != size or digest(target, control) != expected:
                raise ModelError('corrupt', 'Existing download differs; explicit recovery required.')
        progress(size)
        return target
    if not allow_network:
        raise ModelError('consent_changed', 'A verified cached object is missing. Review a fresh plan.')
    if shutil.disk_usage(paths.root).free < size + 1024**3:
        raise ModelError('disk_budget', 'Insufficient portable download headroom.')
    request = urllib.request.Request(url, headers={'Accept-Encoding': 'identity',
                                                  'User-Agent': 'MediaForge explicit optional resources'})
    started = time.monotonic()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), Redirect())
    with Ownership() as owner:
        owner.parents.parents(paths.downloads)
        control.check()
        with opener.open(request, timeout=20) as response:
            if (response.status != 200 or https_host(response.url) not in authority.hosts
                    or response.headers.get('Content-Encoding', 'identity') != 'identity'):
                raise ModelError('source_refused', 'Unexpected optional response.')
            length = response.headers.get('Content-Length')
            if length is not None and int(length) != size:
                raise ModelError('source_refused', 'Optional response length differs.')
            def advance(count):
                if time.monotonic() - started > 3600:
                    raise ModelError('download_timeout', 'Optional object exceeded one-hour download bound.')
                progress(count)
            owner.publish(target, _CheckedStream(response, control, advance), size, expected)
    return target


def wheel_plan(archive, asset, expected):
    """Validate every ZIP name and omission before extraction; hooks stay inert."""
    result, names = {}, set()
    rows = archive.infolist()
    if len(rows) > MAX_OBJECTS:
        raise ModelError('archive_invalid', 'Optional wheel member limit.')
    omitted = set(asset.get('omit', ()))
    overlays = asset.get('overlays', {})
    for row in rows:
        name = safe_member(row.filename.rstrip('/'))
        folded = name.casefold()
        if folded in names or row.flag_bits & 1:
            raise ModelError('archive_invalid', 'Duplicate or encrypted optional member.')
        names.add(folded)
        mode = row.external_attr >> 16
        if mode and stat.S_IFMT(mode) not in (0, stat.S_IFDIR, stat.S_IFREG):
            raise ModelError('archive_invalid', 'Special optional archive member refused.')
        if row.is_dir():
            continue
        name = asset.get('member_map', {}).get(name, name)
        safe_member(name)
        source = asset.get('rename_from', '') + '/'
        if source != '/' and name.startswith(source):
            name = asset['rename_to'] + '/' + name[len(source):]
        if name in omitted:
            continue
        if name not in expected:
            raise ModelError('archive_invalid', 'Undeclared optional wheel member.')
        if name in overlays:
            continue
        if name in result or row.file_size != expected[name].size:
            raise ModelError('archive_invalid', 'Optional wheel member size/collision.')
        result[name] = row
    return result


class OptionalResources:
    """One final-delivery optional inventory facade; no startup acquisition."""
    def __init__(self, paths, gate, *, profile=None):
        self.paths, self.gate = paths, gate
        self.profile = profile or load_profile()
        self.store = OptionalResourceStore(paths)
        self.retained = []
        self.last_checks = {}

    @property
    def specs(self):
        return (self.profile.runtime, *self.profile.models)

    def sizes(self):
        installed = self.profile.data['runtime_bytes'] + sum(sum(m.size for m in s.members) for s in self.profile.models)
        network = self.profile.data['network_bytes']
        peak = network + installed + 2 * 1024**3
        if peak > MAX_DISK:
            raise ModelError('disk_budget', 'Optional installation exceeds approved disk ceiling.')
        return dict(network=network, installed=installed, peak=peak, ready=False)

    def inventory(self):
        # No hash/smoke/network work in a Qt-facing inventory getter.
        rows = []
        for spec in self.specs:
            try:
                present = self.store.exists(spec)
                companion = self.companion(spec)
                if present and companion:
                    local_path(companion)
                if pending_path(self.store, spec).exists():
                    state = 'incomplete'
                elif not present:
                    state = 'corrupt' if self.last_checks.get(spec.model_id) == 'cache_damaged' else 'missing'
                elif companion and not companion.is_file():
                    state = 'corrupt'
                else:
                    state = self.last_checks.get(spec.model_id, 'installed_unqualified')
            except (OSError, ValueError, ModelError):
                present, state = False, 'unavailable'
            rows.append(dict(id=spec.model_id, revision=spec.revision,
                             bytes=sum(m.size for m in spec.members), present=present, state=state))
        return tuple(rows)

    def companion(self, spec):
        if spec is self.profile.runtime:
            return self.paths.manifests / self.profile.data.get('manifest_name', 'qwen-cu128-final1.json')
        return None

    def companion_pin(self):
        data = self.profile.data
        resource = self.profile.root / data.get('manifest_resource', 'qwen-runtime.json')
        raw = resource.read_bytes()
        expected = data['runtime_manifest_sha256']
        if len(raw) > 8 * 1024**2 or hashlib.sha256(raw).hexdigest() != expected:
            raise ModelError('unqualified', 'Bundled runtime manifest failed authentication.')
        return raw, expected

    def verify_companion(self, spec, control):
        target = self.companion(spec)
        if not target:
            return
        raw, expected = self.companion_pin()
        if not target.is_file():
            raise ModelError('corrupt', 'The runtime verification manifest is missing.')
        if not file_matches(target, len(raw), expected, control):
            raise ModelError('corrupt', 'Installed runtime manifest differs.')

    def publish_companion(self, spec, control):
        target = self.companion(spec)
        if not target:
            return
        if not target.exists():
            raw, expected = self.companion_pin()
            control.check()
            with Ownership() as owner:
                owner.parents.parents(target.parent)
                owner.publish(target, io.BytesIO(raw), len(raw), expected)
        self.verify_companion(spec, control)

    def assets_for(self, spec):
        if spec is self.profile.runtime:
            return self.profile.data['assets']
        model = next(m for m in self.profile.data['models'] if m['model_id'] == spec.model_id)
        return [model['archive']] if 'archive' in model else model['members']

    def verify(self, control, progress=lambda *args:None, *, gate_owner=None, complete_inventory=False):
        """Explicit offline byte check, never a cached inference authority."""
        with self.operation(gate_owner):
            for spec in self.specs:
                control.check();progress('verify',spec.model_id,0,0)
                try:
                    with self.store.reserve(spec):
                        if complete_inventory and not self.store.exists(spec):
                            self.store.check_mutation(spec)
                            self.last_checks.pop(spec.model_id, None)
                            continue
                        with closing(self.store.verify(spec,control=control)):
                            self.verify_companion(spec, control)
                except Exception as error:
                    code=str(getattr(error,'code','unavailable'))
                    if code!='cancelled':self.last_checks[spec.model_id]=code if code in ('corrupt','incomplete') else 'unavailable'
                    if not complete_inventory or code == 'cancelled' or getattr(error, 'retained_resources', ()):
                        raise
                    continue
                self.last_checks[spec.model_id]='verified_unqualified'
        return self.inventory()

    def remove(self, key, control, *, discard_damaged=False, reservation_held=False, require_damage=False,
               authorized_intent=None):
        """Explicit removal of exact authenticated objects, never recursive paths.

        A corrupt/extra/aliased inventory needs separate recovery disposition;
        this operation will not delete unknown bytes to make a status green.
        """
        spec = next((s for s in self.specs if s.model_id == key), None)
        if spec is None:
            raise ModelError('unknown_model', 'Unknown optional component.')
        from contextlib import ExitStack
        with ExitStack() as stack:
            if not reservation_held:
                stack.enter_context(self.operation())
                stack.enter_context(ModelMutex(self.paths, 'optional-acquisition'))
                stack.enter_context(self.store.reserve(spec))
            if not discard_damaged or authorized_intent is False:
                self.store.check_mutation(spec)
            intent = None
            if authorized_intent:
                marker = pending_path(self.store, spec)
                owner = stack.enter_context(Ownership())
                owner.parents.parents(marker.parent)
                handle = owner.open(marker, 0x80010000, 1, 3)
                info = owner.info(handle)
                if info.high or info.low:
                    raise ModelError('consent_changed', 'The interruption marker changed.')
                intent = (owner, handle)

            def settle_intent():
                if intent is not None:
                    owner, handle = intent
                    owner.pending(handle, True)
                elif discard_damaged and authorized_intent is None:
                    self._discard_intent(spec)

            root = local_path(self.store.location(spec))
            if not root.exists():
                control.check()
                settle_intent()
                return self.inventory()
            if not discard_damaged:
                with closing(self.store.verify(spec, control=control)):
                    pass
            # Reacquire EVERY exact object with DELETE/read access before any
            # deletion. ReadFile hashes these held objects; no pathname reopen
            # or lost deny-write interval is used as deletion authority.
            owner = Ownership()
            folders, files, seen = [], [], set()
            retirement_started = False
            try:
                owner.parents.parents(root.parent)
                root_handle = owner.open(root, 0x10080, 3, 3, directory=True)
                expected = {m.name: m for m in spec.members}
                import ctypes as C
                from ctypes import wintypes as W
                owner.api.ReadFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
                owner.api.ReadFile.restype = W.BOOL
                buffer, received = C.create_string_buffer(1024*1024), W.DWORD()
                matches = True
                for index, path in enumerate(root.rglob('*')):
                    control.check()
                    if index >= MAX_OBJECTS:
                        raise ModelError('corrupt', 'Removal inventory limit exceeded.')
                    name = path.relative_to(root).as_posix()
                    if path.lstat().st_file_attributes & 0x400:
                        raise ModelError('corrupt', 'Removal encountered an alias.')
                    if path.is_dir():
                        if not any(n.startswith(name + '/') for n in expected):
                            raise ModelError('corrupt', 'Unknown removal directory.')
                        folders.append((len(path.parts), owner.open(path, 0x10080, 3, 3, directory=True)))
                        continue
                    member = expected.get(name)
                    if member is None:
                        raise ModelError('corrupt', 'Unknown removal object.')
                    handle = owner.open(path, 0x80010000, 1, 3)
                    info = owner.info(handle)
                    observed = (info.high << 32 | info.low)
                    if observed > 2*1024**3:
                        raise ModelError('corrupt', 'Damaged member exceeds bounded removal; manual disposition required.')
                    if not discard_damaged and observed != member.size:
                        raise ModelError('corrupt', 'Removal object size changed.')
                    hashed, size = hashlib.sha256(), 0
                    while True:
                        control.check()
                        owner.require(owner.api.ReadFile(handle, buffer, len(buffer), C.byref(received), None))
                        if not received.value:
                            break
                        size += received.value
                        if size > observed:
                            raise ModelError('corrupt', 'Removal object grew.')
                        hashed.update(buffer.raw[:received.value])
                    if size != observed or not discard_damaged and hashed.hexdigest() != member.sha256:
                        raise ModelError('corrupt', 'Removal object bytes changed; recovery required.')
                    matches &= size == member.size and hashed.hexdigest() == member.sha256
                    seen.add(name)
                    files.append(handle)
                if not discard_damaged and seen != set(expected):
                    raise ModelError('corrupt', 'Removal inventory changed.')
                if require_damage and matches and seen == set(expected):
                    raise ModelError('consent_changed', 'The component payload is healthy. No files were replaced.')
                control.check()
                # Once exact deletion begins, finish retirement without a
                # cancellation interruption that could discard ownership.
                for handle in [*files, *(h for _, h in sorted(folders, reverse=True)), root_handle]:
                    retirement_started = True
                    owner.pending(handle, True)
                    owner.require(owner.api.CloseHandle(handle))
                    owner.handles.remove(handle)
            except BaseException as error:
                # Before deletion this just releases unmodified exact objects.
                # After a retirement failure, retain the remainder and poison
                # the shared gate; never claim complete removal.
                if retirement_started:
                    raise ModelError('lifecycle_failed', 'Optional removal partially retired; ownership retained.',
                                     retained_resources=(owner,)) from error
                owner.close()
                raise
            owner.close()
            settle_intent()
        return self.inventory()

    def _discard_intent(self, spec):
        """Explicit reset of a registry-named NEGATIVE marker, never adoption.

        Do not parse marker contents or follow a staging path from it. Damaged
        component bytes were removed (or absent); a new install still performs
        all byte checks. Old download quarantines remain counted and preserved.
        """
        marker=pending_path(self.store,spec)
        try:marker.lstat()
        except FileNotFoundError:return
        with Ownership() as owner:
            owner.parents.parents(marker.parent)
            handle=owner.open(marker,0x80010000,1,3)
            info=owner.info(handle)
            if info.high or info.low:
                raise ModelError('corrupt','Unknown nonempty installation intent; reset refused.')
            owner.pending(handle,True)

    @contextmanager
    def operation(self, owner=None):
        borrowed=owner is not None
        if borrowed and not self.gate.owned_by(owner):raise ModelError('in_use','Invalid optional processing owner.')
        token = owner if borrowed else self.gate.acquire()
        if token is None:
            raise ModelError('in_use', 'Another Media/Dub/Models operation owns processing.')
        try:
            yield
        except BaseException as error:
            if getattr(error, 'retained_resources', ()):
                self.retained.extend(error.retained_resources)
                self.gate.poison(token, 'Optional resource ownership remains retained.')
            raise
        finally:
            if not borrowed and not self.gate.failure:
                self.gate.release(token)

    def install(self, control, progress=lambda *args: None, *, selected=None, gate_owner=None):
        """Explicit user action installs verified inert bytes; never claims Ready."""
        self.paths.prepare()
        sizes = self.sizes()
        chosen = set(selected) if selected is not None else {s.model_id for s in self.specs}
        if not chosen or chosen - {s.model_id for s in self.specs}:
            raise ModelError('unknown_model', 'Choose declared optional components.')
        with self.operation(gate_owner), ModelMutex(self.paths, 'optional-acquisition'):
            used = 0
            for index, item in enumerate(self.paths.root.rglob('*')):
                control.check()
                if index >= 100000 or item.lstat().st_file_attributes & 0x400:
                    raise ModelError('disk_budget', 'Portable inventory is aliased or exceeds accounting bounds.')
                if item.is_file():
                    used += item.stat().st_size
            additional = 2 * 1024**3
            for spec in self.specs:
                if spec.model_id not in chosen or self.store.exists(spec):
                    continue
                additional += sum(m.size for m in spec.members)
                model = next((m for m in self.profile.data['models'] if m['model_id'] == spec.model_id), None)
                assets = (self.profile.data['assets'] if spec is self.profile.runtime else
                          [model['archive']] if 'archive' in model else model['members'])
                additional += sum(a['size'] for a in assets
                                  if not (self.paths.downloads / ('asset-' + a['sha256'])).exists())
            if used + additional > MAX_DISK:
                raise ModelError('disk_budget', 'Retained portable objects exceed the optional installation ceiling.')
            if shutil.disk_usage(self.paths.root).free < additional + max(1024**3, additional // 10):
                raise ModelError('disk_budget', 'Insufficient space for optional download and staging.')
            for spec in self.specs:
                if spec.model_id not in chosen:
                    continue
                control.check()
                with self.store.reserve(spec):
                    self.store.check_mutation(spec)
                    if self.store.exists(spec):
                        with closing(self.store.verify(spec, control=control)):
                            pass
                        self.last_checks[spec.model_id]='verified_unqualified'
                        continue
                    self.install_payload(spec, control, progress)
                    if spec is not self.profile.runtime:
                        self.last_checks[spec.model_id] = 'verified_unqualified'
            if self.profile.runtime.model_id in chosen:
                self.publish_companion(self.profile.runtime, control)
                self.last_checks[self.profile.runtime.model_id] = 'verified_unqualified'
        return self.inventory()

    def install_payload(self, spec, control, progress, *, offline_assets=(), used_network=None, downloads=None):
        directory = self.paths.downloads / ('.optional-' + uuid4().hex)
        with Installation(self.store, spec, directory) as transaction:
            self._fill(spec, directory, transaction.owner, control, progress, offline_assets, used_network, downloads)
            progress('verify', spec.model_id, 0, 0)
            transaction.guard = self.store.verify_at(spec, directory, owned_root=True, control=control)
            transaction.close_guard()
            control.check()
            transaction.activate()
            transaction.guard = self.store.verify_at(spec, transaction.target, owned_root=True, control=control)

    def _fill(self, spec, directory, owner, control, progress, offline_assets, used_network, downloads):
        expected = {m.name: m for m in spec.members}
        written = set()

        def fetch(asset, name):
            key = asset['sha256']
            offline = key in offline_assets or used_network is not None and key in used_network
            report = lambda n: progress('verify' if offline else 'download', name, n, asset['size'])
            if used_network is not None:
                used_network.add(key)
            if offline:
                return download(self.paths, asset, control, report, allow_network=False)
            if downloads is not None:
                authority = downloads.get(key)
                if authority is None:
                    raise ModelError('consent_changed', 'This asset was not authorized for download. Review a fresh plan.')
                return download(self.paths, asset, control, report, authority=authority)
            return download(self.paths, asset, control, report)

        def publish(name, stream):
            control.check()
            if name in written:
                raise ModelError('archive_invalid', 'Cross-wheel optional member collision.')
            target = directory / safe_member(name)
            with ReadLocks() as nested:
                folder = directory
                for part in Path(name).parts[:-1]:
                    folder = folder / part
                    folder.mkdir(exist_ok=True)
                    nested.add(folder, True)
                member = expected[name]
                progress('install', name, 0, member.size)
                owner.publish(target, _CheckedStream(stream, control,
                              lambda n: progress('install', name, n, member.size)), member.size, member.sha256,
                              inside_owned_directory=True)
            written.add(name)
        if spec is self.profile.runtime:
            for asset in self.profile.data['assets']:
                source = fetch(asset, asset['filename'])
                with ReadLocks() as locked:
                    locked.parents(source.parent)
                    locked.add(source)
                    # Hash again under this extraction lease; a prior download
                    # receipt cannot authenticate a subsequently replaced file.
                    if source.stat().st_nlink != 1 or source.stat().st_size != asset['size'] or digest(source, control) != asset['sha256']:
                        raise ModelError('corrupt', 'Wheel changed before extraction.')
                    with zipfile.ZipFile(source) as wheel:
                        plan = wheel_plan(wheel, asset, expected)
                        for name, row in plan.items():
                            with wheel.open(row) as stream:
                                publish(name, stream)
                    for name, overlay in asset.get('overlays', {}).items():
                        path = self.profile.root / overlay['path']
                        if not path.resolve().is_relative_to(self.profile.root.resolve()):
                            raise ModelError('unqualified', 'Application patch escaped resource root.')
                        with ReadLocks() as patch:
                            patch.parents(path.parent)
                            patch.add(path)
                            if path.stat().st_nlink != 1 or path.stat().st_size != overlay['size'] or digest(path, control) != overlay['sha256']:
                                raise ModelError('unqualified', 'Application patch bytes differ.')
                            with path.open('rb') as stream:
                                publish(name, stream)
        else:
            model = next(m for m in self.profile.data['models'] if m['model_id'] == spec.model_id)
            if 'archive' in model:
                asset = model['archive']
                source = fetch(asset, asset['filename'])
                from mediaforge.infrastructure.models.install import archive_members
                with ReadLocks() as locked:
                    locked.parents(source.parent)
                    locked.add(source)
                    if source.stat().st_nlink != 1 or source.stat().st_size != asset['size'] or digest(source, control) != asset['sha256']:
                        raise ModelError('corrupt', 'Translation archive changed before extraction.')
                    with zipfile.ZipFile(source) as archive:
                        for name, row in archive_members(archive, spec, asset['prefix']).items():
                            with archive.open(row) as stream:
                                publish(name, stream)
                if written != set(expected):
                    raise ModelError('archive_invalid', 'Incomplete translated-model package.')
                return
            for member in model['members']:
                source = fetch(member, member['name'])
                with ReadLocks() as locked:
                    locked.parents(source.parent)
                    locked.add(source)
                    with source.open('rb') as stream:
                        publish(member['name'], stream)
        if written != set(expected):
            raise ModelError('archive_invalid', 'Transformed optional resource is incomplete.')

