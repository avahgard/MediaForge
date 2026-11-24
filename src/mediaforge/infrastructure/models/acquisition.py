"""Plan and execute the approved portable acquisition under one reservation."""
from contextlib import ExitStack, closing, contextmanager
import ctypes as C
from ctypes import wintypes as W
import hashlib
import shutil
import stat

from mediaforge.application.contracts.model_acquisition import AcquisitionPlan, ComponentAcquisition
from mediaforge.application.contracts.models import ModelError
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.filesystem.profile import local_path
from mediaforge.infrastructure.models.installation_lifecycle import pending_path
from mediaforge.infrastructure.models.optional_resources import (
    MAX_ACQUISITION, MAX_DISK, download_authority, file_matches,
)
from mediaforge.infrastructure.models.owned import ModelMutex, Ownership


GIB = 1024**3
MIB = 1024**2
def managed_usage(paths, control):
    used, count = 0, 0
    folders = [local_path(paths.root)]
    while folders:
        folder = folders.pop()
        with ReadLocks() as locks:
            locks.parents(folder)
            for item in folder.iterdir():
                control.check()
                info = item.lstat()
                count += 1
                if count > 100000 or info.st_file_attributes & 0x400:
                    raise ModelError('disk_budget', 'Portable inventory is aliased or exceeds accounting bounds.')
                if stat.S_ISDIR(info.st_mode):
                    folders.append(item)
                elif stat.S_ISREG(info.st_mode):
                    if info.st_nlink != 1:
                        raise ModelError('unsafe_alias', 'Portable inventory contains a linked file.')
                    used += info.st_size
                else:
                    raise ModelError('storage_refused', 'Unsupported object in portable Data.')
    return used


def file_state(path, size, digest, control):
    try:
        path.lstat()
    except FileNotFoundError:
        return 'missing'
    return 'verified' if file_matches(path, size, digest, control) else 'damaged'


@contextmanager
def hold_damaged(path, size, expected, limit, control):
    """Prove corruption and hold the exact single-link object until retirement."""
    local_path(path)
    with Ownership() as owner:
        owner.parents.parents(path.parent)
        handle = owner.open(path, 0x80010000, 1, 3)
        info = owner.info(handle)
        observed = (info.high << 32) | info.low
        if observed > limit:
            raise ModelError('storage_refused', f'Damaged file exceeds its repair bound: {path.name}')
        if observed == size:
            read = owner.api.ReadFile
            read.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
            read.restype = W.BOOL
            buffer, received = C.create_string_buffer(MIB), W.DWORD()
            digest, total = hashlib.sha256(), 0
            while total < size:
                control.check()
                owner.require(read(handle, buffer, min(len(buffer), size - total), C.byref(received), None))
                if not received.value:
                    raise ModelError('storage_refused', 'Held repair file could not be read completely.')
                total += received.value
                digest.update(buffer.raw[:received.value])
            if digest.hexdigest() == expected:
                raise ModelError('consent_changed', f'The file is now healthy and was preserved: {path.name}')
        yield owner, handle


class DubAcquisition:
    def __init__(self, requirements):
        self.paths = requirements.paths
        self.profiles = requirements.profiles
        self.entries = {spec.model_id: (profile, spec) for profile in self.profiles for spec in profile.specs}

    @contextmanager
    def reserve(self):
        # The shared gate is captured by the CPU profile and lent to CUDA.
        # Both remain inside the same cross-instance acquisition reservation.
        with self.profiles[0].operation(), ModelMutex(self.paths, 'optional-acquisition'), ExitStack() as stack:
            for profile, spec in self.entries.values():
                stack.enter_context(profile.store.reserve(spec))
            yield

    def prepare(self, selected, repair, control, progress):
        with self.reserve():
            return self._plan(selected, repair, control, progress)

    def dispatch(self, accepted, control, progress):
        with self.reserve():
            current = self._plan(accepted.selected, accepted.repair, control, progress)
            if not accepted.authorizes(current):
                return current
            used_network = set()
            downloads = {asset.sha256: asset for asset in accepted.downloads}
            for step in current.components:
                control.check()
                self._execute(step, control, progress, used_network, downloads)
        return None

    def _selected(self, selected):
        if not selected:
            return set(self.entries)
        if selected not in self.entries:
            raise ModelError('unknown_model', 'Choose a registered optional component.')
        profile, spec = self.entries[selected]
        selected_keys = {selected, profile.profile.runtime.model_id}
        if selected == 'qwen3-tts-06b-customvoice':
            selected_keys.add('qwen3-tts-tokenizer-12hz')
        return selected_keys

    def _payload_state(self, profile, spec, control):
        marker = pending_path(profile.store, spec)
        try:
            marker.lstat()
        except FileNotFoundError:
            has_intent = False
        else:
            local_path(marker)
            info = marker.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size:
                raise ModelError('storage_refused', 'Foreign or nonempty installation marker. Repair is refused.')
            has_intent = True
        # Reject a foreign revision's intent before any repair retirement.
        for index, sibling in enumerate(marker.parent.iterdir()):
            if index >= 4096:
                raise ModelError('storage_refused', 'Installation marker namespace exceeds its inspection bound.')
            if sibling != marker and sibling.name.casefold().startswith(f'.install-pending-{spec.model_id}--'):
                raise ModelError('storage_refused', 'An unexpected installation marker blocks this component.')
        if not profile.store.exists(spec):
            return 'missing', has_intent
        try:
            with closing(profile.store.verify_at(spec, profile.store.location(spec), control=control)):
                pass
        except ModelError as error:
            if error.code == 'corrupt' and not error.retained_resources:
                return 'damaged', has_intent
            raise
        return 'verified', has_intent

    def _plan(self, selected, repair, control, progress):
        chosen = self._selected(selected)
        if repair and not selected:
            raise ModelError('unknown_model', 'Repair requires one diagnosed component.')
        steps, retained, downloads = [], [], []
        assets, net = {}, 0
        installed = retained_bytes = staged = largest_stage = staged_companions = 0
        blocked = ''
        for key, (profile, spec) in self.entries.items():
            control.check()
            progress('checking', key, 0, 0)
            path = profile.store.location(spec)
            companion = profile.companion(spec)
            try:
                payload, intent = self._payload_state(profile, spec, control)
                companion_state, companion_bytes = '', 0
                if companion:
                    raw, expected = profile.companion_pin()
                    companion_bytes = len(raw)
                    companion_state = file_state(companion, companion_bytes, expected, control)
            except (OSError, ValueError, ModelError) as error:
                if getattr(error, 'code', '') == 'cancelled' or getattr(error, 'retained_resources', ()):
                    raise
                if key in chosen:
                    raise ModelError('storage_refused', f'Cannot check {key}: {error}') from error
                continue
            size = sum(member.size for member in spec.members)
            if payload == 'verified' and intent:
                profile.last_checks[key] = 'incomplete'
                if key in chosen:
                    blocked = f'{key} has a complete payload and an unresolved installation intent. Files are preserved.'
                continue
            if payload == 'verified':
                retained_bytes += size
                installed += size
                retained.append(key)
            if companion_state == 'verified':
                retained_bytes += companion_bytes
                installed += companion_bytes
            state = ('incomplete' if intent else 'corrupt' if payload == 'damaged'
                     or companion_state == 'damaged' or payload == 'verified' and companion_state == 'missing'
                     else 'verified_unqualified' if payload == 'verified' else 'missing')
            profile.last_checks[key] = state
            if key not in chosen:
                continue
            if state in ('corrupt', 'incomplete') and not (repair and key == selected):
                blocked = f'{key} requires targeted Repair before continuing.'
                continue
            action = {'missing': 'install', 'verified': 'reuse', 'damaged': 'replace'}[payload]
            companion_action = {'': '', 'missing': 'publish', 'verified': 'reuse',
                                'damaged': 'replace'}[companion_state]
            cached, damaged = [], []
            if action != 'reuse':
                for asset in profile.assets_for(spec):
                    asset_hash, asset_bytes = asset['sha256'], asset['size']
                    target = self.paths.downloads / ('asset-' + asset_hash)
                    if asset_hash not in assets:
                        assets[asset_hash] = file_state(target, asset_bytes, asset_hash, control)
                        if assets[asset_hash] != 'verified':
                            net += asset_bytes
                            downloads.append(download_authority(asset))
                    if assets[asset_hash] == 'verified':
                        cached.append(asset_hash)
                    elif assets[asset_hash] == 'damaged':
                        if not repair or key != selected:
                            profile.last_checks[key] = 'cache_damaged'
                            blocked = f'{key} has a damaged managed download. Use targeted Repair.'
                        damaged.append(target)
                staged += size
                largest_stage = max(largest_stage, size)
                installed += size
            if companion_action in ('publish', 'replace'):
                staged_companions += companion_bytes
                installed += companion_bytes
            if action != 'reuse' or companion_action in ('publish', 'replace') or intent:
                steps.append(ComponentAcquisition(key, spec.revision, path, action, companion,
                                                   companion_action, size, tuple(cached), tuple(damaged), intent))
        if not steps and not blocked:
            raise ModelError('nothing_to_install', 'The selected components already verify offline.')
        retained_data = managed_usage(self.paths, control)
        temporary = net + largest_stage + staged_companions
        additional = net + staged + staged_companions
        if staged:
            additional += 2 * GIB
        elif temporary:
            temporary = max(MIB, temporary)
            additional = temporary
        required = additional + max(GIB, additional // 10) if staged else additional
        available = shutil.disk_usage(self.paths.root).free
        if net > MAX_ACQUISITION or additional + retained_data > MAX_DISK:
            blocked = 'The plan and retained Data exceed the managed installation limit.'
        if available < required:
            blocked = 'The Data drive has insufficient free space for this plan.'
        publishers = tuple(sorted({asset.publisher for asset in downloads}))
        return AcquisitionPlan(selected, repair, tuple(steps), tuple(retained), publishers, net,
                               installed, retained_bytes, retained_data, temporary, required, available,
                               tuple(downloads), blocked)

    def _execute(self, step, control, progress, used_network, downloads):
        profile, spec = self.entries[step.key]
        def report(phase, name, count, total):
            progress(phase, step.key, count, total, name)

        report('install', '', 0, 0)
        needed = step.installed_bytes + GIB if step.action != 'reuse' else MIB
        if shutil.disk_usage(self.paths.root).free < needed:
            raise ModelError('disk_budget', 'Data drive space fell below the write headroom.')
        with ExitStack() as stack:
            retirement = []
            if step.companion_action == 'replace':
                raw, expected = profile.companion_pin()
                retirement.append(stack.enter_context(hold_damaged(step.companion, len(raw), expected,
                                                                   8 * MIB, control)))
            assets = {('asset-' + a['sha256']): a for a in profile.assets_for(spec)}
            for path in step.damaged_assets:
                asset = assets[path.name]
                retirement.append(stack.enter_context(hold_damaged(path, asset['size'], asset['sha256'],
                                                                   3 * GIB, control)))
            control.check()
            if step.action == 'replace' or step.reset_intent:
                profile.remove(step.key, control, discard_damaged=True, reservation_held=True,
                               require_damage=True, authorized_intent=step.reset_intent)
            for owner, handle in retirement:
                owner.pending(handle, True)
        if step.action != 'reuse':
            if profile.store.exists(spec):
                raise ModelError('consent_changed', 'The component destination changed. Review a fresh plan.')
            profile.store.check_mutation(spec)
            profile.install_payload(spec, control, report, offline_assets=step.cached_assets,
                                    used_network=used_network, downloads=downloads)
        if step.companion:
            report('verify', step.companion.name, 0, 0)
            if step.action == 'reuse':
                with closing(profile.store.verify(spec, control=control)):
                    profile.publish_companion(spec, control)
            else:
                profile.publish_companion(spec, control)
        profile.last_checks[step.key] = 'verified_unqualified'
