"""Explicit real CUDA compatibility run under the unchanged default NativeGate."""
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import sys

from mediaforge.application.contracts.models import ModelError
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.features.media.processing_control import ProcessingControl
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.models.optional_resources import OptionalResources
from mediaforge.infrastructure.models.qwen_runtime import QwenRuntime


def ordinary_token():
    kernel = C.WinDLL('kernel32', use_last_error=True)
    security = C.WinDLL('advapi32', use_last_error=True)
    kernel.GetCurrentProcess.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    security.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
    security.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
    security.GetSidSubAuthorityCount.argtypes = [C.c_void_p]
    security.GetSidSubAuthorityCount.restype = C.POINTER(C.c_ubyte)
    security.GetSidSubAuthority.argtypes = [C.c_void_p, W.DWORD]
    security.GetSidSubAuthority.restype = C.POINTER(W.DWORD)
    token, required = W.HANDLE(), W.DWORD()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 8, C.byref(token)):
        raise OSError('Ordinary token cannot be verified.')
    try:
        elevated = W.DWORD()
        if not security.GetTokenInformation(token, 20, C.byref(elevated), C.sizeof(elevated), C.byref(required)):
            raise OSError('Elevation query failed.')
        raw = C.create_string_buffer(1024)
        if not security.GetTokenInformation(token, 25, raw, len(raw), C.byref(required)):
            raise OSError('Integrity query failed.')
        sid = C.cast(raw, C.POINTER(C.c_void_p))[0]
        count = security.GetSidSubAuthorityCount(sid)[0]
        rid = security.GetSidSubAuthority(sid, count - 1)[0]
        if elevated.value or rid != 8192:
            raise OSError('Actual ordinary non-elevated medium token is required.')
        return dict(integrity=rid, elevated=False)
    finally:
        if not kernel.CloseHandle(token):
            raise OSError('Token handle retirement failed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    args = parser.parse_args()
    paths = ProfilePaths.for_test(args.root)
    receipt = args.receipt.absolute()
    if not receipt.is_relative_to(paths.root) or receipt.exists():
        parser.error('Receipt must be a new path under the explicit portable root.')
    token = ordinary_token()
    gate = MediaProcessingGate()
    resources = OptionalResources(paths, gate)
    control = ProcessingControl(lambda: ModelError('cancelled', 'Qwen compatibility check cancelled.'))
    runtime = QwenRuntime(paths, resources)
    try:
        result = runtime.probe(control)
        code = 0
    except Exception as error:
        result = dict(ok=False, code=getattr(error, 'code', 'runtime_failed'), detail=str(error),
                      retained=bool(getattr(error, 'retained_resources', ())))
        code = 2
    result.update(token=token, gate_busy=gate.busy, gate_failure=gate.failure,
                  inference_performed=False, driver_policy='qwen-cuda-driver-v1',
                  driver_authentication=runtime.last_driver_proof)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with receipt.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    summary = {k: result.get(k) for k in ('ok', 'code', 'detail', 'gpu_name', 'torch_version',
                                         'cuda_version', 'gate_busy', 'gate_failure')}
    summary['native_image_count'] = len(result.get('metrics', {}).get('native_images', ()))
    summary['receipt'] = str(receipt)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return code


if __name__ == '__main__':
    sys.exit(main())
