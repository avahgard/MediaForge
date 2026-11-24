"""Dedicated optional CUDA profile over unchanged C2 Job/NativeGate primitives."""
from contextlib import contextmanager, ExitStack, closing
import ctypes as C
import hashlib
import io
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.models.native_guard import identity
from mediaforge.infrastructure.models.optional_resources import OptionalResources, RESOURCE_ROOT
from mediaforge.infrastructure.models.owned import Ownership
from mediaforge.infrastructure.models.providers import LocalProviders, RuntimeLease
from mediaforge.infrastructure.models.process import run_worker
from mediaforge.infrastructure.models.host_driver import HostDriverLease, POLICY as DRIVER_POLICY

ENTRY = Path(__file__).with_name('qwen_worker.py')
ENTRY_SHA256 = 'b083b6c83f21639fd4fd1a5abd99a624c2528ce03f31aa87eda3f4f8b6ba68e2'
RESOLVER_SHA256 = '534bd141583f311b6fd5c35cbf107b4b02e8c05f3d01768b9d57d16f6d45e0a7'


class RetainedRuntime:
    """Release reservation/outer leases only after the actual native owner exits."""
    def __init__(self, stack, dependencies):
        self.stack, self.dependencies = stack, dependencies

    def close(self):
        for owner in self.dependencies:
            if getattr(owner, 'closed', False):
                continue
            process = getattr(owner, 'process', None)
            native = getattr(owner, 'native', None)
            if process is None or process.poll() is None or native is None or not native.exited or native.pending:
                raise LocalAiError('lifecycle_failed', 'Qwen runtime awaits confirmed native retirement.', retained_resources=(self,))
        self.stack.close()


class QwenRuntime:
    def __init__(self, paths, resources):
        self.paths, self.resources = paths, resources
        # Use the explicitly trusted interpreter whose base installation the
        # accepted NativeGate leases. A venv redirector is a different PE object
        # and is not implicitly admitted. Packaged selection must supply the
        # reviewed private core interpreter rather than the GUI executable.
        self.python = Path(sys._base_executable)
        self.site = resources.store.location(resources.profile.runtime)
        self.manifest = paths.manifests / resources.profile.data['manifest_name']
        self.manifest_sha256 = resources.profile.data['runtime_manifest_sha256']
        self.entry = ENTRY
        self.last_driver_proof = None

    @contextmanager
    def acquire(self, control):
        start = time.monotonic()
        stack = ExitStack()
        try:
            stack.enter_context(self.resources.store.reserve(self.resources.profile.runtime))
            sources = stack.enter_context(ReadLocks())
            sources.parents(self.python.parent)
            sources.add(self.python)
            for source, expected in ((ENTRY, ENTRY_SHA256), (ENTRY.with_name('qwen_resolver.py'), RESOLVER_SHA256)):
                sources.parents(source.parent)
                sources.add(source)
                with source.open('rb') as stream:
                    raw = stream.read(128 * 1024 + 1)
                if source.stat().st_nlink != 1 or len(raw) > 128 * 1024 or hashlib.sha256(raw).hexdigest() != expected:
                    raise LocalAiError('runtime_integrity', 'Qwen application worker source differs.')
            sources.parents(self.manifest.parent)
            sources.add(self.manifest)
            with self.manifest.open('rb') as stream:
                raw = stream.read(8 * 1024**2 + 1)
            if len(raw) > 8 * 1024**2 or hashlib.sha256(raw).hexdigest() != self.manifest_sha256:
                raise LocalAiError('runtime_integrity', 'Qwen installed runtime manifest differs.')
            # Preserve C2's image-section cache retirement on application-owned
            # optional images only. This never opens an installed driver writable.
            for member in self.resources.profile.runtime.members:
                control.check()
                path = self.site / member.name
                if path.suffix.lower() in ('.dll', '.pyd'):
                    with Ownership() as cache:
                        cache.open(path, 0xC0000000, 1, 3)
            guard = stack.enter_context(closing(self.resources.store.verify(self.resources.profile.runtime, control=control)))
            native = {}
            buffer = C.create_unicode_buffer(32768)
            from ctypes import wintypes as W
            api = guard.locks.api
            api.GetFinalPathNameByHandleW.argtypes = [W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD]
            api.GetFinalPathNameByHandleW.restype = W.DWORD
            for handle in guard.locks.handles:
                key, info = identity(api, handle)
                if info.attributes & 0x10:
                    continue
                n = api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
                if not 0 < n < len(buffer):
                    raise LocalAiError('runtime_integrity', 'Qwen runtime handle name unavailable.')
                native[key] = buffer.value.removeprefix('\\\\?\\')
            driver = stack.enter_context(HostDriverLease(control))
            if driver.receipt['policy'] != DRIVER_POLICY:
                raise LocalAiError('runtime_integrity', 'Qwen driver capability has the wrong purpose.')
            if set(native) & set(driver.native_ids):
                raise LocalAiError('runtime_integrity', 'Runtime/driver object identity collision.')
            native.update(driver.native_ids)
            self.last_driver_proof = driver.receipt
            handles = (*guard.locks.handles, *sources.handles, *driver.handles)
            if 2 * len(handles) + 2688 > 90000:
                raise LocalAiError('resource_unavailable', 'Qwen combined handle ceiling exceeded.')
            yield RuntimeLease(native, time.monotonic()-start, handles)
        except BaseException as error:
            dependencies = getattr(error, 'retained_resources', ())
            if dependencies:
                retained = RetainedRuntime(stack.pop_all(), dependencies)
                raise LocalAiError('lifecycle_failed', 'Qwen runtime authority remains retained.',
                                   retained_resources=(*dependencies, retained)) from error
            raise
        finally:
            stack.close()

    def environment(self):
        # Reuse the accepted minimal offline environment, deliberately enabling
        # only the selected GPU for this separately admitted profile.
        env = LocalProviders(self.paths, self, self.resources.gate).environment()
        env.update(CUDA_VISIBLE_DEVICES='0', CUDA_CACHE_PATH=str(self.paths.cache/'cuda'),
                   CUDA_CACHE_MAXSIZE=str(64*1024**2), TORCH_HOME=str(self.paths.cache/'torch'),
                   TORCH_EXTENSIONS_DIR=str(self.paths.cache/'torch_extensions'),
                   TRITON_CACHE_DIR=str(self.paths.cache/'triton'), TORCHDYNAMO_DISABLE='1',
                   TORCHINDUCTOR_CACHE_DIR=str(self.paths.cache/'torch_inductor'),
                   # getpass.getuser() otherwise imports Unix-only pwd in this
                   # deliberately credential-free Windows environment. This is
                   # a fixed cache label, never inherited personal identity.
                   USERNAME='MediaForge')
        return env

    def probe(self, control):
        """Actual CUDA compatibility inspection; no model load or grant on failure."""
        self.paths.prepare()
        session = self.paths.temporary / ('qwen-' + uuid4().hex)
        session.mkdir()
        request_path = session / 'request.json'
        try:
            with self.resources.operation(), self.acquire(control) as guard, Ownership() as request_owner:
                request_owner.parents.parents(session)
                raw = json.dumps(dict(root=str(self.paths.root), site=str(self.site),
                                      runtime_manifest=str(self.manifest), runtime_digest=self.manifest_sha256,
                                      temporary=str(session), action='runtime_probe')).encode()
                request_owner.publish(request_path, io.BytesIO(raw), len(raw), hashlib.sha256(raw).hexdigest())
                request_owner.parents.add(request_path)
                return run_worker([str(self.python), '-I', '-S', '-B', str(ENTRY), str(request_path)],
                                  paths=self.paths, environment=self.environment(), control=control,
                                  timeout=180, limit=2*1024**3, runtime_guard=guard)
        finally:
            if not control.cleanup_failed and not self.resources.gate.failure:
                request_path.unlink(missing_ok=True)
                session.rmdir()
