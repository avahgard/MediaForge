"""Standalone source-authenticated Qwen CUDA entry; no driver exceptions.

One fixed local CustomVoice engine, bounded duplex fragments and no helper
processes. Incomplete generation or unsupported actions fail without mock audio.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import base64

RESOLVER_SHA256 = '534bd141583f311b6fd5c35cbf107b4b02e8c05f3d01768b9d57d16f6d45e0a7'
PROTOCOL_OUTPUT = sys.stdout


def emit(value):
    PROTOCOL_OUTPUT.write(json.dumps(value, separators=(',', ':')) + '\n')
    PROTOCOL_OUTPUT.flush()


def command():
    raw = sys.stdin.buffer.readline(8193)
    if not raw or len(raw) > 8192:
        raise ValueError('command_limit_or_eof')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('command_schema')
    return value


def cuda_luid():
    import ctypes as C
    import struct
    api = C.WinDLL('nvcuda.dll', winmode=0x800)
    api.cuDeviceGet.argtypes = [C.POINTER(C.c_int), C.c_int]
    api.cuDeviceGet.restype = C.c_int
    api.cuDeviceGetLuid.argtypes = [C.c_void_p, C.POINTER(C.c_uint), C.c_int]
    api.cuDeviceGetLuid.restype = C.c_int
    device, node, luid = C.c_int(), C.c_uint(), C.create_string_buffer(8)
    if api.cuDeviceGet(C.byref(device), 0) or api.cuDeviceGetLuid(luid, C.byref(node), device):
        raise ValueError('cuda_luid_unavailable')
    return list(struct.unpack('<Ii', luid.raw)), node.value


def main():
    core=os.environ.get('MEDIAFORGE_CORE_ROOT');digest=os.environ.get('MEDIAFORGE_CORE_MANIFEST_SHA256')
    if bool(core)!=bool(digest):raise ValueError('incomplete_core_capability')
    if core:
        helper=Path(__file__).with_name('core_loader.py');raw=helper.read_bytes()
        if len(raw)>16384 or hashlib.sha256(raw).hexdigest()!='62d683b6ae8ce946b2866a7317f43b759912661e4c1c7540ae31b4448a31f36a':
            raise ValueError('core_loader_integrity')
        namespace=dict(__name__='_mediaforge_private_stdlib',__file__=str(helper))
        exec(compile(raw,str(helper),'exec',dont_inherit=True),namespace)
        namespace['install'](core,digest)
    started = time.monotonic()
    with Path(sys.argv[1]).open('rb') as stream:
        encoded = stream.read(16385)
    if len(encoded) > 16384:
        raise ValueError('request_limit')
    request = json.loads(encoded)
    if set(request) != {'root', 'site', 'runtime_manifest', 'runtime_digest', 'temporary', 'action'}:
        raise ValueError('request_schema')
    if request['action'] not in ('runtime_probe', 'serve'):
        raise ValueError('unsupported_action')
    root, site, temporary = (Path(request[k]) for k in ('root', 'site', 'temporary'))
    if not root.is_absolute() or not site.is_relative_to(root) or not temporary.is_relative_to(root):
        raise ValueError('nonportable_request')
    if not Path(request['runtime_manifest']).is_relative_to(root / 'manifests'):
        raise ValueError('nonportable_manifest')
    # Direct native selectors avoid platform.uname/WMI's unrelated native chain.
    import platform
    version = sys.getwindowsversion()
    if sys.platform != 'win32' or version.major != 10 or version.product_type != 1:
        raise ValueError('unsupported_windows')
    platform.system = lambda: 'Windows'
    platform.release = lambda: '11' if version.build >= 22000 else '10'
    import struct
    if struct.calcsize('P') != 8:
        raise ValueError('unsupported_architecture')
    platform.machine = lambda: 'AMD64'
    resolver_path = Path(__file__).with_name('qwen_resolver.py')
    with resolver_path.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != RESOLVER_SHA256:
        raise ValueError('resolver_integrity')
    namespace = dict(__name__='_mediaforge_qwen_resolver', __file__=str(resolver_path))
    exec(compile(raw, str(resolver_path), 'exec'), namespace)
    policy, origins = namespace['install_runtime'](site, request['runtime_manifest'], request['runtime_digest'])
    tempfile.tempdir = str(temporary)
    violations = []
    writes = []
    def violation(event):
        # Bounded code locations only: no hostnames, network addresses, source
        # text or audit arguments. Denial stays active even if a library catches it.
        frames=[];frame=sys._getframe(2)
        while frame is not None and len(frames)<6:
            frames.append(Path(frame.f_code.co_filename).name+':'+str(frame.f_lineno)+':'+frame.f_code.co_name)
            frame=frame.f_back
        if len(violations)<16:violations.append(dict(event=event,frames=frames))
    def audit(event, args):
        if event == 'exec':
            policy.check_exec(args[0])
        if event.startswith('socket.') and event != 'socket.gethostname':
            violation(event)
            raise PermissionError('offline_network_refused')
        if event.startswith(('subprocess.', 'os.spawn', 'os.exec')) or event in ('os.system', 'os.fork'):
            violation(event)
            raise PermissionError('nested_process_refused')
        if event == 'open':
            name, mode, flags = args
            if isinstance(name, (str, bytes, os.PathLike)) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                target = Path(os.fsdecode(name)).resolve()
                if not target.is_relative_to(root):
                    violation('outside_portable_root')
                    raise PermissionError('outside_portable_root')
                if len(writes) >= 128:
                    raise PermissionError('write_observation_limit')
                writes.append(str(target.relative_to(root)))
    sys.addaudithook(audit)
    # This worker has NO permitted IPv6/network capability. urllib3's import
    # helper otherwise constructs/binds a socket merely to discover IPv6; the
    # audit correctly denies it, even though urllib3 catches the exception.
    # Suppress that unnecessary probe, never the denial or its final receipt.
    import socket
    socket.has_ipv6 = False
    # Libraries may print informational messages. They must never become IPC.
    sys.stdout = sys.stderr
    import ctypes as C
    from ctypes import wintypes as W
    user = C.WinDLL('user32.dll', winmode=0x800)
    user.GetProcessWindowStation.argtypes = []
    user.GetProcessWindowStation.restype = W.HANDLE
    if not user.GetProcessWindowStation():
        raise ValueError('win32_registration_failed')
    sys.stderr.write('MF_PHASE load\n')
    sys.stderr.flush()
    import torch
    if torch.__version__ != '2.10.0+cu128':
        raise ValueError('torch_identity')
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise ValueError('cuda_unavailable')
    properties = torch.cuda.get_device_properties(0)
    if ((properties.major, properties.minor) != (8, 9) or properties.total_memory < 7 * 1024**3
            or 'RTX 4060' not in properties.name):
        raise ValueError('hardware_unqualified')
    loaded = time.monotonic()
    free, total = torch.cuda.mem_get_info(0)
    luid, node = cuda_luid()
    if violations:
        raise ValueError('containment_refused')
    info = dict(ok=True, action=request['action'], inference_performed=False,
                torch_version=torch.__version__, cuda_version=torch.version.cuda,
                gpu_name=properties.name, compute_capability=[properties.major, properties.minor],
                gpu_total=total, gpu_free=free, luid=luid, node_mask=node,
                python_modules=origins, writes=writes,
                violations=violations, load_seconds=loaded-started,
                inference_seconds=time.monotonic()-loaded)
    if request['action'] == 'runtime_probe':
        return info
    emit(dict(info, phase='cuda_ready'))
    allocation = command()
    if (set(allocation) != {'action', 'budget_bytes'} or allocation['action'] != 'load'
            or type(allocation['budget_bytes']) is not int
            or not 2 * 1024**3 <= allocation['budget_bytes'] <= min(6 * 1024**3, free - 1024**3)):
        raise ValueError('gpu_admission_refused')
    # Keep room for driver/context allocations not charged to Torch's allocator.
    torch.cuda.set_per_process_memory_fraction((allocation['budget_bytes'] - 256*1024**2) / total, 0)
    torch.backends.cudnn.benchmark = False
    from qwen_tts import Qwen3TTSModel
    from qwen_tts.mf_contracts import REVISION, SPEAKERS
    engine = None
    try:
        engine = Qwen3TTSModel.from_local_components(root,
            root / 'models/qwen3-tts-06b-customvoice' / REVISION,
            root / 'models/qwen3-tts-tokenizer-12hz' / REVISION)
        torch.cuda.synchronize()
        emit(dict(ok=True, phase='model_ready', speakers=sorted(SPEAKERS),
                  load_seconds=time.monotonic()-loaded))
        import numpy as np
        import gc
        for index in range(5001):
            row = command()
            if row == {'action': 'stop'}:
                return dict(ok=True, phase='closed', inference_performed=index > 0,
                            load_seconds=loaded-started, inference_seconds=time.monotonic()-loaded,
                            writes=writes,violations=violations,python_modules=origins)
            if (index == 5000 or set(row) != {'action', 'text', 'speaker', 'seed', 'seconds'}
                    or row['action'] != 'synthesize' or type(row['seconds']) is not int
                    or not 1 <= row['seconds'] <= 60):
                raise ValueError('synthesis_request_refused')
            begin = time.monotonic()
            output = engine.generate_custom_voice(row['text'], row['speaker'], language='Russian',
                seed=row['seed'], max_new_tokens=1500, max_audio_seconds=row['seconds'])
            wave = np.asarray(output.audio[0], dtype=np.float32)
            if wave.ndim != 1 or not 0 < wave.size <= row['seconds']*24000 or not np.all(np.isfinite(wave)):
                raise ValueError('invalid_waveform')
            pcm = np.clip(np.rint(wave.astype(np.float64) * 32768), -32768, 32767).astype('<i2').tobytes()
            if not any(pcm):
                raise ValueError('silent_waveform')
            torch.cuda.synchronize()
            receipt = output.completion
            emit(dict(ok=True, phase='fragment', request_index=index,
                      text_sha256=hashlib.sha256(row['text'].encode('utf-8')).hexdigest(),
                      speaker=row['speaker'], sample_rate=24000,
                      samples=wave.size, pcm16=base64.b64encode(pcm).decode('ascii'),
                      sha256=hashlib.sha256(pcm).hexdigest(), seconds=time.monotonic()-begin,
                      termination=dict(reason=receipt.reason.value, terminal_eos=receipt.terminal_eos,
                          generated_steps=receipt.generated_steps, codec_frames=receipt.codec_frames,
                          effective_length=receipt.effective_length, cap=receipt.cap),
                      cuda_allocated=torch.cuda.memory_allocated(0), cuda_reserved=torch.cuda.memory_reserved(0)))
            del output, wave, pcm
            gc.collect()
            torch.cuda.empty_cache()
        raise ValueError('segment_limit')
    finally:
        if engine is not None:
            engine._component_owner.close()


if __name__ == '__main__':
    try:
        result = main()
    except Exception as error:
        # No source text, tokens or private model paths in the result/log.
        causes = []
        seen = set()
        while error is not None and id(error) not in seen and len(causes) < 5:
            seen.add(id(error))
            frames = []
            trace = error.__traceback__
            while trace is not None:
                frames.append(Path(trace.tb_frame.f_code.co_filename).name + ':' + str(trace.tb_lineno)
                              + ':' + trace.tb_frame.f_code.co_name)
                trace = trace.tb_next
            causes.append(type(error).__name__ + ': ' + str(error)[:256] + ' [' + ';'.join(frames[-8:]) + ']')
            error = error.__cause__
        result = dict(ok=False, detail=' <- '.join(causes)[:1024])
    emit(result)
    raise SystemExit(0 if result.get('ok') else 2)
