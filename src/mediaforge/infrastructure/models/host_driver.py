"""Qwen-only device-bound Microsoft catalog authority (Owner approved).

This is neither DriverStore prefix trust nor enrollment of a refused path.
Only fixed CUDA roles from the selected device's authenticated package become
exact held-file capabilities BEFORE a child exists. NativeGate stays unchanged.
"""
from dataclasses import dataclass
import ctypes as C
from ctypes import wintypes as W
import hashlib
from pathlib import Path
import time

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.filesystem.profile import local_path
from mediaforge.infrastructure.models.driver_discovery import Discovery
from mediaforge.infrastructure.models.driver_trust import CatalogTrust
from mediaforge.infrastructure.models.native_guard import identity


POLICY = 'qwen-cuda-driver-v1'
# CUDA driver and its driver-package PTX/compiler roles. No graphics/overlay
# helpers, executables, arbitrary dependency recursion or NVIDIA directory scan.
MANDATORY = ('nvcuda64.dll',)
# Runtime-04 stopped at nvapi64_impl. Read-only held-byte inspection established
# nvcuda64 -> nvapi64.dll (UTF16 reference) -> nvapi64_impl.dll (System32 thunk
# reference). This is the closed CUDA device-query dependency, not enrollment
# by denial or permission for unrelated NVAPI/graphics helpers.
# nvcuda64 directly names nvdxgdmal64 and nvldumdx; the WDDM allocation layer
# itself names nvldumdx. These exact roles are preauthenticated for CUDA's
# Windows allocation path. Referenced capture/overlay/stereo DLLs stay excluded.
OPTIONAL = ('nvptxJitCompiler64.dll', 'nvgpucomp64.dll', 'nvapi64_impl.dll',
            'nvdxgdmal64.dll', 'nvldumdx.dll', 'nvdiagclt64.dll', 'nvobjectloader64.dll',
            'Display.NvContainer/NvMessageBus.dll')
# nvdiagclt is directly named by the authenticated CUDA driver and is reached
# during runtime-06 initialization. It is the package's internal diagnostic
# client, not permission to launch diagnostic tools/services or admit overlays.
# nvobjectloader64 is also a direct nvcuda64 reference, reached in runtime-07;
# only that installed-package loader object is authorized, not arbitrary objects
# it may request. Every subsequent native image still passes NativeGate.
# Runtime-08 reached the package's message-bus DLL. Its literal references are
# present in NVAPI/UMD, and its exact nested object must pass the SAME catalog
# membership/chain/lease rules. NvContainer executables/services are not granted.
ROOT_2010_SHA256 = 'df545bf919a2439c36983b54cdfc903dfa4f37d3996d8d84b4c31eec6f3c163e'
ROOT_2010_SHA1 = '3b1efd3a66ea28b16697394703a72ca340a05bd5'
MAX_IMAGE_BYTES = 256 * 1024**2
MAX_PACKAGE_PROOF_BYTES = 16 * 1024**2


def catalog_choice(fields):
    for key in ('CatalogFile.NTamd64', 'CatalogFile.NT', 'CatalogFile'):
        if key in fields:
            values = fields[key]
            if len(values) != 1 or Path(values[0]).name != values[0] or not values[0].lower().endswith('.cat'):
                raise LocalAiError('driver_authentication', 'Unsafe installed catalog selector.')
            if any(c in values[0] for c in '\\/:'):
                raise LocalAiError('driver_authentication', 'Catalog must be a package-local leaf.')
            return values[0]
    raise LocalAiError('driver_authentication', 'No selected-package catalog.')


def verify_chain(record):
    if record.get('wintrust_status') != 0 or record.get('state_close_status') != 0 or not record.get('verified'):
        raise LocalAiError('driver_authentication', 'Cache-only Windows catalog verification failed.')
    chain = record.get('signer_chain', {})
    certs = chain.get('certificates', [])
    if (chain.get('signer_error') != 0 or not 2 <= len(certs) <= 16
            or certs[0].get('subject') != 'Microsoft Windows Hardware Compatibility Publisher'
            or certs[-1].get('der_sha256') != ROOT_2010_SHA256
            or certs[-1].get('der_sha1') != ROOT_2010_SHA1
            or not certs[-1].get('trusted_root')
            or any(c.get('error') != 0 or c.get('revoked_reason') != 0 or c.get('test_certificate') for c in certs)):
        raise LocalAiError('driver_authentication', 'Catalog is not the pinned Microsoft hardware trust chain.')


@dataclass(frozen=True)
class DriverCapability:
    native_ids: dict
    handles: tuple
    receipt: dict


class HostDriverLease:
    def __init__(self, control):
        self.control = control
        self.locks = ReadLocks()
        self.closed = False
        self.records = {}
        self.trust = None

    def _held(self, path, maximum):
        self.control.check()
        path = local_path(Path(path))
        self.locks.parents(path.parent)
        handle = self.locks.add(path)
        key, info = identity(self.locks.api, handle)
        size = (info.high << 32) | info.low
        if info.links != 1 or info.attributes & (0x400 | 0x10) or not 0 < size <= maximum:
            raise LocalAiError('driver_authentication', 'Driver object type/link/size is unsupported.')
        api = self.locks.api
        api.GetFinalPathNameByHandleW.argtypes = [W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD]
        api.GetFinalPathNameByHandleW.restype = W.DWORD
        name = C.create_unicode_buffer(32768)
        n = api.GetFinalPathNameByHandleW(handle, name, len(name), 0)
        if not 0 < n < len(name) or Path(name.value.removeprefix('\\\\?\\')) != path:
            raise LocalAiError('driver_authentication', 'Driver held object/path differs.')
        api.ReadFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
        api.ReadFile.restype = W.BOOL
        api.SetFilePointerEx.argtypes = [W.HANDLE, C.c_int64, C.c_void_p, W.DWORD]
        api.SetFilePointerEx.restype = W.BOOL
        if not api.SetFilePointerEx(handle, 0, None, 0):
            raise LocalAiError('driver_authentication', 'Cannot read held driver object.')
        hashed, count = hashlib.sha256(), 0
        buffer, got = C.create_string_buffer(1024**2), W.DWORD()
        while True:
            self.control.check()
            if not api.ReadFile(handle, buffer, len(buffer), C.byref(got), None):
                raise LocalAiError('driver_authentication', 'Held driver read failed.')
            if not got.value:
                break
            count += got.value
            if count > size:
                raise LocalAiError('driver_authentication', 'Driver object grew.')
            hashed.update(buffer.raw[:got.value])
        if count != size:
            raise LocalAiError('driver_authentication', 'Incomplete driver object hash.')
        row = dict(path=str(path), identity=key, bytes=size, sha256=hashed.hexdigest(),
                   access='GENERIC_READ', sharing='FILE_SHARE_READ', links=info.links)
        self.records[path] = row
        return handle, row

    def __enter__(self):
        started = time.monotonic()
        try:
            self.control.check()
            discovery = Discovery()
            selected = discovery.selected()
            if selected['driver'].get('ProviderName') != 'NVIDIA':
                raise LocalAiError('driver_authentication', 'Selected device provider is unsupported.')
            inf = local_path(Path(selected['store_inf']))
            published = local_path(Path(selected['published_inf']))
            inf_handle, inf_row = self._held(inf, MAX_PACKAGE_PROOF_BYTES)
            published_handle, published_row = self._held(published, MAX_PACKAGE_PROOF_BYTES)
            if inf_row['sha256'] != published_row['sha256']:
                raise LocalAiError('driver_authentication', 'Published and package INF differ.')
            fields = discovery.version_fields(inf)
            if fields.get('ClassGuid', [''])[0].strip('{}').lower() != '4d36e968-e325-11ce-bfc1-08002be10318':
                raise LocalAiError('driver_authentication', 'Selected INF is not a display-driver package.')
            version = fields.get('DriverVer', [])
            if len(version) != 2 or version[1] != selected['driver'].get('DriverVersion'):
                raise LocalAiError('driver_authentication', 'Device/package driver versions differ.')
            catalog = local_path(inf.parent / catalog_choice(fields))
            _, catalog_row = self._held(catalog, MAX_PACKAGE_PROOF_BYTES)
            self.trust = CatalogTrust()
            proofs = []
            for file, handle in ((inf, inf_handle), (published, published_handle)):
                record = self.trust.member(self.locks.api, file, handle, catalog)
                verify_chain(record)
                proofs.append(record)
            native, total = {}, 0
            names = [*MANDATORY, *(n for n in OPTIONAL if (inf.parent / n).exists())]
            if len(names) > 12:
                raise LocalAiError('driver_authentication', 'Closed driver-role count exceeded.')
            for role in names:
                file = inf.parent / role
                handle, row = self._held(file, MAX_IMAGE_BYTES)
                total += row['bytes']
                if total > MAX_IMAGE_BYTES:
                    raise LocalAiError('driver_authentication', 'Driver-role byte ceiling exceeded.')
                record = self.trust.member(self.locks.api, file, handle, catalog)
                verify_chain(record)
                proofs.append(record)
                native[tuple(row['identity'])] = str(file)
            # Trust state/admin lifetime settles before granting a capability;
            # file/ancestor deny-write/delete ownership remains until worker exit.
            self.trust.close()
            self.trust = None
            self.control.check()
            self.receipt = dict(policy=POLICY, authority='device-bound-microsoft-driver-catalog',
                                selected=selected, inf_fields=fields, catalog=catalog_row,
                                objects=list(self.records.values()), proofs=proofs,
                                seconds=time.monotonic()-started)
            return DriverCapability(native, tuple(self.locks.handles), self.receipt)
        except BaseException as error:
            if getattr(error, 'retained_resources', ()):
                raise LocalAiError('lifecycle_failed', 'Driver trust state/ownership remains retained.',
                                   retained_resources=(*error.retained_resources, self)) from error
            self.close()
            raise

    def close(self):
        try:
            if self.trust is not None:
                self.trust.close()
                self.trust = None
            self.locks.close()
            self.closed = True
        except BaseException as error:
            raise LocalAiError('lifecycle_failed', 'Host-driver ownership retained.', retained_resources=(self,)) from error

    def __exit__(self, kind, error, tb):
        self.close()
