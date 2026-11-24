"""One authenticated CUDA engine per operation; bounded duplex IPC and ownership.

The creating orchestration thread pumps the accepted NativeGate. No background
process launcher, global driver permission, automatic download or CPU fallback.
"""
import base64
from contextlib import contextmanager, ExitStack, closing
import ctypes as C
import hashlib
import io
import json
from queue import Queue, Empty, Full
import subprocess
from threading import Thread
import time
from uuid import uuid4

from mediaforge.application.contracts.local_ai import LocalAiError, PcmFragment, SynthesisRequest, CompletionReceipt
from mediaforge.infrastructure.filesystem.speech_directory import create_directory
from mediaforge.infrastructure.images.resources import _Memory
from mediaforge.infrastructure.models.gpu_memory import Api, Target, admission, shared_growth
from mediaforge.infrastructure.models.owned import Ownership
from mediaforge.infrastructure.models.native_guard import identity
from mediaforge.infrastructure.models.process import CpuJob
from mediaforge.infrastructure.models.qwen_runtime import RetainedRuntime

GIB = 1024**3
MIB = 1024**2
SEED = 120012
REVISION = '85e237c12c027371202489a0ec509ded67b5e4b5'
SPEAKERS = ('aiden', 'dylan', 'eric', 'ono_anna', 'ryan', 'serena', 'sohee', 'uncle_fu', 'vivian')
# Phase-specific production ceilings. FFmpeg fitting can overlap this resident
# worker, but no second AI worker. Shared backing is host memory, never VRAM.
JOB_BYTES = 6*GIB
SHARED_ALLOWANCE = 256*MIB
HOST_RESERVE = JOB_BYTES + SHARED_ALLOWANCE + 128*MIB + 512*MIB


def reject_constant(value):
    raise ValueError('Nonfinite JSON value.')


def require_host_memory():
    api = C.WinDLL('kernel32', use_last_error=True, winmode=0x800)
    api.GlobalMemoryStatusEx.argtypes = [C.POINTER(_Memory)]
    memory = _Memory(); memory.length = C.sizeof(memory)
    if not api.GlobalMemoryStatusEx(C.byref(memory)):
        raise LocalAiError('resource_unavailable', 'Host memory/commit cannot be measured.')
    physical = max(2*GIB, memory.total_phys*15//100)
    commit = max(2*GIB, memory.total_commit//10)
    if memory.avail_phys < HOST_RESERVE+physical or memory.avail_commit < HOST_RESERVE+commit:
        raise LocalAiError('resource_unavailable', 'Insufficient host memory/commit for the bounded CUDA phase.')
    return dict(available_physical=memory.avail_phys, available_commit=memory.avail_commit,
                admitted=HOST_RESERVE, physical_headroom=physical, commit_headroom=commit)


class QwenSynthesisSession:
    def __init__(self, runtime, control):
        self.runtime, self.control = runtime, control
        self.owner = CpuJob(JOB_BYTES)
        self.process = None
        self.readers = []
        self.messages = Queue(maxsize=2)
        self.read_errors = []
        self.diagnostic = bytearray()
        self.target = None
        self.baseline = None
        self.last_sample = 0.0
        self.started = time.monotonic()
        self.statistics = dict(fragments=0, samples=0, synthesis_seconds=0.0,
                               local_peak=0, nonlocal_peak=0, nonlocal_growth_peak=0,
                               gpu_samples=0, cpu_job=self.owner.metrics)
        self.closed = False

    def _read_output(self):
        try:
            while raw := self.process.stdout.readline(5*MIB+1):
                if len(raw) > 5*MIB or not raw.endswith(b'\n'):
                    raise ValueError('Qwen IPC line exceeds its bound.')
                value = json.loads(raw, parse_constant=reject_constant)
                if not isinstance(value, dict):
                    raise ValueError('Qwen IPC object required.')
                self.messages.put_nowait(value)
        except (ValueError, UnicodeError, Full, OSError) as error:
            self.read_errors.append(type(error).__name__)

    def _read_error(self):
        try:
            while block := self.process.stderr.read(4096):
                # Keep only a bounded private tail for diagnosis; never log text
                # to UI or persist library output containing inference inputs.
                self.diagnostic.extend(block)
                if len(self.diagnostic) > 8192:
                    del self.diagnostic[:-8192]
        except OSError as error:
            self.read_errors.append(type(error).__name__)

    def start(self, guard, request):
        with self.control.starting():
            self.process = self.owner.spawn(
                [str(self.runtime.python), '-I', '-S', '-B', str(self.runtime.entry), str(request)],
                runtime_guard=guard, cwd=self.runtime.paths.temporary,
                env=self.runtime.environment(), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
        if self.owner.metrics['memory_limit'] != JOB_BYTES:
            raise LocalAiError('resource_unavailable', 'The full qualified CUDA Job ceiling was not admitted.')
        self.readers = [Thread(target=self._read_output, daemon=True),
                        Thread(target=self._read_error, daemon=True)]
        for reader in self.readers:
            reader.start()
        info = self.receive('cuda_ready', 180)
        api = Api(); adapter = api.select()
        if list(adapter['luid']) != info.get('luid') or info.get('node_mask') != 1:
            raise LocalAiError('resource_unavailable', 'CUDA and WDDM adapter/node identities differ.')
        original = int(self.process._handle)
        self.target = Target(api, original, api.process_identity(original), adapter)
        self.baseline = self.target.sample()
        ledger = admission(self.baseline, self.target.identity, tuple(adapter['luid']), 0)
        if ledger['C'] < 3*GIB:
            raise LocalAiError('resource_unavailable', 'Less than 3 GiB of bounded target GPU budget is available.')
        self.statistics.update(gpu_admission=ledger, adapter=adapter, target_identity=self.target.identity,
                               runtime_seconds=info['load_seconds'])
        self.send(dict(action='load', budget_bytes=ledger['C']))
        model = self.receive('model_ready', 300)
        if tuple(model.get('speakers', ())) != SPEAKERS:
            raise LocalAiError('invalid_output', 'Authenticated Qwen speaker inventory differs.')
        self.statistics['model_seconds'] = model['load_seconds']
        self.pump()

    def send(self, value):
        self.control.check()
        encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')+b'\n'
        if len(encoded) > 8192 or self.process is None or self.process.poll() is not None:
            raise LocalAiError('provider_failed', 'Qwen command cannot be admitted.')
        try:
            self.process.stdin.write(encoded)
            self.process.stdin.flush()
        except OSError as error:
            raise LocalAiError('provider_failed', 'Qwen worker command channel closed.') from error

    def pump(self):
        self.control.check()
        if self.process is None or self.process.poll() is not None:
            raise LocalAiError('provider_failed', 'Qwen engine exited before its owner completed.')
        self.owner.native.pump(self.process)
        now = time.monotonic()
        if now-self.last_sample >= .2:
            self.owner.sample()
            if self.target is not None:
                sample = self.target.sample()
                ledger = admission(sample, self.target.identity, tuple(self.target.mapping['luid']), 0)
                growth = shared_growth(sample, self.baseline, SHARED_ALLOWANCE)
                self.statistics['gpu_samples'] += 1
                self.statistics['local_peak'] = max(self.statistics['local_peak'], ledger['U'])
                self.statistics['nonlocal_peak'] = max(self.statistics['nonlocal_peak'], sample.nonlocal_[1])
                self.statistics['nonlocal_growth_peak'] = max(self.statistics['nonlocal_growth_peak'], growth)
                self.statistics['last_gpu_budget'] = ledger['B']
            self.last_sample = now
        if self.read_errors:
            raise LocalAiError('invalid_output', 'Qwen bounded IPC failed: '+self.read_errors[0])

    def receive(self, phase, timeout):
        deadline = time.monotonic()+timeout
        while True:
            self.control.check()
            if self.process.poll() is None:
                self.pump()
            else:
                self.owner.native.pump(self.process)
                # Allow the bounded reader to finish its final already-written
                # result, without confusing zero process exit with success.
                self.readers[0].join(.1)
            if self.read_errors:
                raise LocalAiError('invalid_output', 'Qwen IPC failed: '+self.read_errors[0])
            try:
                value = self.messages.get_nowait()
            except Empty:
                if self.process.poll() is not None:
                    raise LocalAiError('provider_failed', 'Qwen engine exited without a valid result.')
                if time.monotonic() > deadline:
                    raise LocalAiError('provider_timeout', 'Qwen phase exceeded its bounded deadline.')
                time.sleep(.01)
                continue
            if value.get('ok') is not True:
                raise LocalAiError('provider_failed', str(value.get('detail', 'Qwen inference failed.'))[:1024])
            if value.get('phase') != phase:
                raise LocalAiError('invalid_output', 'Qwen phase/result generation differs.')
            return value

    def synthesize(self, text, speaker, *, seconds=60):
        SynthesisRequest(text)
        if speaker not in SPEAKERS or type(seconds) is not int or not 1 <= seconds <= 60:
            raise LocalAiError('invalid_request', 'Select an admitted built-in voice and bounded fragment.')
        self.send(dict(action='synthesize', text=text, speaker=speaker, seed=SEED, seconds=seconds))
        row = self.receive('fragment', 300)
        try:
            pcm = base64.b64decode(row['pcm16'], validate=True)
            terminal = row['termination']
            if (row['speaker'] != speaker or row['sample_rate'] != 24000
                    or type(row['request_index']) is not int or row['request_index'] != self.statistics['fragments']
                    or row['text_sha256'] != hashlib.sha256(text.encode('utf-8')).hexdigest()
                    or type(row['samples']) is not int or not 0 < row['samples'] <= seconds*24000
                    or len(pcm) != 2*row['samples'] or not any(pcm)
                    or hashlib.sha256(pcm).hexdigest() != row['sha256']
                    or terminal['reason'] != 'normal_eos' or terminal['terminal_eos'] is not True
                    or type(terminal['cap']) is not int or terminal['cap'] != 1500
                    or any(type(terminal[k]) is not int for k in ('codec_frames','effective_length','generated_steps'))
                    or not 1 <= terminal['codec_frames'] == terminal['effective_length'] < terminal['generated_steps'] <= 1500
                    or terminal['generated_steps'] != terminal['codec_frames']+1):
                raise ValueError('Invalid fragment/EOS receipt.')
        except (ValueError, KeyError, TypeError) as error:
            raise LocalAiError('invalid_output', 'Qwen audio/termination identity failed.') from error
        self.statistics['fragments'] += 1
        self.statistics['samples'] += row['samples']
        self.statistics['synthesis_seconds'] += row['seconds']
        self.pump()
        return PcmFragment(pcm, 24000, REVISION, speaker,CompletionReceipt(**{k:terminal[k] for k in
            ('reason','terminal_eos','codec_frames','effective_length','generated_steps','cap')}))

    def close(self, *, graceful=False):
        error = None
        try:
            if graceful and self.process is not None and self.process.poll() is None:
                self.send(dict(action='stop'))
                closed=self.receive('closed', 10)
                self.statistics['writes']=closed.get('writes')
                self.statistics['violations']=closed.get('violations')
                self.statistics['python_modules']=closed.get('python_modules')
                if closed.get('violations')!=[]:
                    raise LocalAiError('runtime_integrity','Qwen reported a containment violation.')
                deadline = time.monotonic()+5
                while self.process.poll() is None and time.monotonic() < deadline:
                    self.owner.native.pump(self.process)
                    time.sleep(.01)
                if self.process.poll() != 0:
                    raise LocalAiError('provider_failed', 'Qwen normal retirement did not exit successfully.')
        except BaseException as failure:
            error = failure
        try:
            if self.target is not None:
                self.target.close(); self.target = None
            if self.process is not None and self.process.stdin is not None:
                self.process.stdin.close()
            self.owner.close()
            for reader in self.readers:
                reader.join(2)
            if any(reader.is_alive() for reader in self.readers):
                raise LocalAiError('lifecycle_failed', 'Qwen IPC ownership has not retired.', retained_resources=(self,))
            self.closed = True
            self.statistics['elapsed_seconds'] = time.monotonic()-self.started
        except BaseException as failure:
            if getattr(failure, 'retained_resources', ()):
                self.control.retain_unreaped_child()
            raise
        if error is not None:
            raise error


@contextmanager
def synthesis_session(runtime, control, *, gate_owner=None):
    """Borrow only the real orchestration token; otherwise acquire shared gate."""
    resources = runtime.resources
    if gate_owner is not None and not resources.gate.owned_by(gate_owner):
        raise LocalAiError('in_use', 'Qwen was not lent the current processing owner.')
    @contextmanager
    def ownership():
        if gate_owner is None:
            with resources.operation():
                yield
        else:
            try:
                yield
            except BaseException as error:
                if getattr(error, 'retained_resources', ()):
                    resources.retained.extend(error.retained_resources)
                    resources.gate.poison(gate_owner, 'Qwen ownership remains unresolved.')
                raise
    with ownership():
        runtime.paths.prepare()
        host = require_host_memory()
        stack = ExitStack(); engine = QwenSynthesisSession(runtime, control)
        runtime.last_statistics = engine.statistics
        folder = runtime.paths.temporary/('qwen-'+uuid4().hex)
        request = folder/'request.json'
        directory = None; directory_key = None; request_key = None
        try:
            # BOTH complete component sets before any configuration is parsed.
            for spec in resources.profile.models:
                stack.enter_context(resources.store.reserve(spec))
                stack.enter_context(closing(resources.store.verify(spec, control=control)))
            guard = stack.enter_context(runtime.acquire(control))
            directory, directory_handle = create_directory(folder)
            directory_key, _ = identity(directory.api, directory_handle)
            stack.enter_context(directory)
            request_owner = stack.enter_context(Ownership())
            raw = json.dumps(dict(root=str(runtime.paths.root), site=str(runtime.site),
                                  runtime_manifest=str(runtime.manifest), runtime_digest=runtime.manifest_sha256,
                                  temporary=str(folder), action='serve')).encode()
            request_owner.publish(request, io.BytesIO(raw), len(raw), hashlib.sha256(raw).hexdigest())
            request_owner.parents.add(request)
            request_key, _ = identity(request_owner.parents.api, request_owner.parents.handles[-1])
            engine.statistics['host_admission'] = host
            engine.start(guard, request)
            yield engine
            engine.close(graceful=True)
        except BaseException as error:
            if not engine.closed:
                try:
                    engine.close()
                except BaseException as retirement:
                    dependencies = getattr(retirement, 'retained_resources', ())
                    if dependencies:
                        retained = RetainedRuntime(stack.pop_all(), dependencies)
                        raise LocalAiError('lifecycle_failed', 'Qwen leases retained until native retirement.',
                                           retained_resources=(*dependencies, retained)) from error
                    raise
            raise
        finally:
            if engine.closed and not control.cleanup_failed and directory_key is not None:
                # Identity pins prevent FileID reuse while lifetime read locks
                # are upgraded. Reopened deletion handles MUST name those exact
                # objects. Unknown members/refused upgrade stay retained.
                pin = Ownership(); cleanup = Ownership()
                try:
                    pin.parents.parents(folder.parent)
                    handle = pin.open(folder, 0x80, 7, 3, directory=True)
                    if identity(pin.api, handle)[0] != directory_key:
                        raise LocalAiError('storage_refused', 'Qwen temporary directory identity changed.')
                    if request_key is not None:
                        handle = pin.open(request, 0x80, 7, 3)
                        if identity(pin.api, handle)[0] != request_key:
                            raise LocalAiError('storage_refused', 'Qwen request object identity changed.')
                    stack.close()
                    cleanup.parents.parents(folder.parent)
                    directory_handle = cleanup.open(folder, 0x10080, 3, 3, directory=True)
                    if identity(cleanup.api, directory_handle)[0] != directory_key:
                        raise LocalAiError('storage_refused', 'Qwen temporary retirement object changed.')
                    expected = {'request.json'} if request_key is not None else set()
                    if {p.name for p in folder.iterdir()} != expected:
                        raise LocalAiError('storage_refused', 'Unknown Qwen temporary objects; cleanup refused.')
                    if request_key is not None:
                        handle = cleanup.open(request, 0x10080, 1, 3)
                        if identity(cleanup.api, handle)[0] != request_key:
                            raise LocalAiError('storage_refused', 'Qwen request retirement object changed.')
                        cleanup.pending(handle, True)
                        cleanup.require(cleanup.api.CloseHandle(handle)); cleanup.handles.remove(handle)
                        # File pin must close before empty-directory retirement.
                        file_pin = pin.handles.pop()
                        pin.require(pin.api.CloseHandle(file_pin))
                    cleanup.pending(directory_handle, True)
                    cleanup.close(); pin.close()
                except BaseException as error:
                    raise LocalAiError('lifecycle_failed', 'Qwen private temporary ownership retained.',
                                       retained_resources=(pin, cleanup, RetainedRuntime(stack.pop_all(), ()))) from error
            else:
                stack.close()
