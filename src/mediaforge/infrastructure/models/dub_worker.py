"""C3 entry: direct Windows selectors, authenticated unchanged C2 execution.

Trusted application source, never selected from request/environment. All optional
imports/inference still enter the original resolver/offline audit and NativeGate.
"""
import hashlib,json,struct,sys
from pathlib import Path

DELEGATE_SHA256='991a3a8e2822e27b59ddf58dbfbdd071d6814ac09153fb503412590e0526bdc2'
MAX_SOURCE=65536
VAD_SHA256='4cbf549b8326f60f80f2536d9eefeb450a9abe83365a098031c89719f1be17d2'

def activity(request):
    # Called only inside the unchanged delegate's resolver/offline audit setup.
    import base64,time
    import numpy as np
    import onnxruntime as ort
    from faster_whisper.vad import SileroVADModel
    started=time.monotonic();encoded=request.get('pcm16')
    if not isinstance(encoded,str) or len(encoded)>1280000:raise ValueError('speech_boundary_uncertain')
    raw=base64.b64decode(encoded,validate=True)
    if len(raw)%2 or not 3072<=len(raw)<=960000:raise ValueError('speech_boundary_uncertain')
    asset=Path(request['site'])/'faster_whisper/assets/silero_vad_v6.onnx'
    with asset.open('rb') as stream:source=stream.read(1245152)
    if len(source)!=1245151 or hashlib.sha256(source).hexdigest()!=VAD_SHA256:raise ValueError('runtime_integrity')
    ort.disable_telemetry_events()
    sys.stderr.write('MF_PHASE load\n');sys.stderr.flush()
    model=SileroVADModel(str(asset));loaded=time.monotonic()
    sys.stderr.write('MF_PHASE inference\n');sys.stderr.flush()
    audio=np.frombuffer(raw,dtype='<i2').astype(np.float32)/32768.0;rows=[]
    for grid in (0,256):
        wave=audio[grid:].copy();complete=wave.size//512
        wave=np.pad(wave,(0,(-wave.size)%512))
        scores=np.asarray(model(wave)).reshape(-1)[:complete]
        if scores.size!=complete or not np.all(np.isfinite(scores)) or np.any((scores<0)|(scores>1)):
            raise ValueError('speech_boundary_uncertain')
        rows.append(dict(grid=grid,scores=[float(n) for n in scores]))
    return dict(observations=rows,asset_sha256=VAD_SHA256,load_seconds=loaded-started,inference_seconds=time.monotonic()-loaded)

def native_facts(snapshot=None,*,host=None,width=None):
    import platform
    if (sys.platform if host is None else host)!='win32' or (struct.calcsize('P') if width is None else width)!=8:
        raise ValueError('native_platform_unavailable')
    raw=sys.getwindowsversion() if snapshot is None else snapshot
    version=getattr(raw,'platform_version',None);product=getattr(raw,'product_type',None)
    if (not isinstance(version,tuple) or len(version)!=3 or any(type(v) is not int or v<0 for v in version)
            or product not in (1,2,3)):
        raise ValueError('native_platform_unavailable')
    table=platform._WIN32_CLIENT_RELEASES if product==1 else platform._WIN32_SERVER_RELEASES
    release=next((label for minimum,label in table if version>=minimum),None)
    if release is None or release.startswith('post'):raise ValueError('native_platform_unavailable')
    return dict(system='Windows',release=release,version=version,product_type=product)

def verified_delegate():
    path=Path(__file__).with_name('worker.py')
    with path.open('rb') as stream:source=stream.read(MAX_SOURCE+1)
    if len(source)>MAX_SOURCE or hashlib.sha256(source).hexdigest()!=DELEGATE_SHA256:
        raise ValueError('runtime_integrity')
    namespace=dict(__name__='_mediaforge_c2_delegate',__file__=str(path))
    # Compile the exact bounded authenticated bytes, not a second path read.
    exec(compile(source,str(path),'exec',dont_inherit=True),namespace)
    return namespace

def main():
    try:
        import platform
        facts=native_facts();calls=dict(system=0,release=0)
        def system():calls['system']+=1;return facts['system']
        def release():calls['release']+=1;return facts['release']
        platform.system=system;platform.release=release
        delegate=verified_delegate();original=delegate['run']
        def dispatch(request):
            if request.get('action')=='c3_activity':result=activity(request)
            elif request.get('action') in ('recognize','translate','synthesize'):result=original(request)
            else:raise ValueError('Unsupported C3 action')
            result.update(native_facts=facts,platform_selectors=dict(calls),delegate_sha256=DELEGATE_SHA256)
            return result
        delegate['run']=dispatch
        return delegate['main']()
    except Exception as error:
        code=str(error) if str(error) in ('runtime_integrity','native_platform_unavailable') else 'provider_failed'
        sys.stdout.write(json.dumps(dict(ok=False,detail=code)));sys.stdout.flush();return 2

if __name__=='__main__':raise SystemExit(main())
