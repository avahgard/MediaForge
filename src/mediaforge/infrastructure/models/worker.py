"""Standalone isolated provider entry. Only stdlib loads before containment hooks.

Launched with -I -S -B. Imports exact approved runtime paths supplied by the
trusted parent. All native libraries die with this one owned CPU worker.
"""
import base64
import ctypes
import json
import math
import os
from pathlib import Path
import re
import sys
import time


def install_runtime(site,manifest,digest):
    """Resolve optional code by exact manifest members, never directory search.

    Parent holds every source/image object and ancestor until child retirement.
    Windows native-image admission is enforced separately by the parent debugger.
    """
    import hashlib
    import importlib.abc
    import importlib.machinery as machinery
    import importlib.util
    import importlib.metadata
    encoded=Path(manifest).read_bytes()
    if len(encoded)>4*1024*1024 or hashlib.sha256(encoded).hexdigest()!=digest:
        raise ValueError('Runtime import manifest is not authenticated')
    members={r['name']:r for r in json.loads(encoded)}
    site=Path(site).resolve()
    standard=tuple(sys.path)
    modules={};ranks={};namespaces=set();executed=set();origins=[]
    for name,row in members.items():
        suffix=next((s for s in machinery.EXTENSION_SUFFIXES if name.endswith(s)),None)
        if name.endswith('.py'):stem=name[:-3];kind='python'
        elif suffix:stem=name[:-len(suffix)];kind='native'
        else:continue
        parts=stem.split('/')
        package=parts[-1]=='__init__'
        if package:parts=parts[:-1]
        if not parts or not all(p.isidentifier() for p in parts):continue
        key='.'.join(parts)
        # CPython packages precede same-name modules, and native extensions
        # precede sources (PyAV legitimately ships both .py and .pyd members).
        rank=(not package,kind!='native',machinery.EXTENSION_SUFFIXES.index(suffix) if suffix else 0)
        if key in modules and rank==ranks[key]:raise ValueError('Ambiguous runtime module inventory')
        if key not in modules or rank<ranks[key]:modules[key]=(name,kind,package);ranks[key]=rank
        for i in range(1,len(parts)):namespaces.add('.'.join(parts[:i]))

    class Source(machinery.SourceFileLoader):
        def get_code(self,fullname):
            # SourceFileLoader's normal bytecode-cache preference is forbidden.
            relative=Path(self.path).relative_to(site).as_posix()
            raw=Path(self.path).read_bytes();row=members[relative]
            if len(raw)!=row['size'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:
                raise ImportError('Runtime source object failed import-time authentication')
            code=compile(raw,self.path,'exec',dont_inherit=True,optimize=sys.flags.optimize)
            executed.add(code)
            if len(origins)<256:origins.append(dict(name=fullname,path=self.path))
            return code

    class Resolver(importlib.abc.MetaPathFinder):
        def find_spec(self,fullname,path=None,target=None):
            # Standard library precedes optional packages. For submodules, never
            # delegate a runtime directory to Python's filesystem path finder.
            search=standard if path is None else tuple(p for p in path if not Path(os.path.abspath(p)).is_relative_to(site))
            spec=machinery.PathFinder.find_spec(fullname,search)
            if spec is not None:return spec
            row=modules.get(fullname)
            if row is not None:
                relative,kind,package=row
                filename=str(site/relative)
                loader=Source(fullname,filename) if kind=='python' else machinery.ExtensionFileLoader(fullname,filename)
                if kind=='native' and len(origins)<256:origins.append(dict(name=fullname,path=filename))
                return importlib.util.spec_from_file_location(fullname,filename,loader=loader,
                    submodule_search_locations=[str(Path(filename).parent)] if package else None)
            if fullname in namespaces:
                spec=machinery.ModuleSpec(fullname,None,is_package=True)
                spec.submodule_search_locations=[str(site/Path(*fullname.split('.')))]
                return spec
            return None

        def find_distributions(self,context=None):
            wanted=context.name if context is not None else None
            normalized=lambda x:re.sub('[-_.]+','-',x).lower()
            for name in members:
                if name.endswith('.dist-info/METADATA') and '/' not in name.removesuffix('/METADATA'):
                    distribution=importlib.metadata.PathDistribution((site/name).parent)
                    if wanted is None or normalized(distribution.metadata['Name'])==normalized(wanted):yield distribution

        def check_exec(self,code):
            # co_filename is a compiler label, often an absent build-machine
            # path in Cython. Never perform filesystem I/O from an exec audit
            # callback. Actual source identities are held by the parent and
            # authenticated by Source.get_code, not by resolving this label.
            try:inside=Path(code.co_filename).is_absolute() and Path(os.path.normpath(code.co_filename)).is_relative_to(site)
            except (ValueError,OSError):inside=False
            if inside and code not in executed:raise PermissionError('Unverified runtime code/bytecode execution refused')

    policy=Resolver()
    # Builtin/Frozen are the trusted interpreter; all filesystem imports pass
    # the resolver. Optional directories are absent from global sys.path.
    sys.meta_path[:]=[machinery.BuiltinImporter,machinery.FrozenImporter,policy]
    return policy,origins


def run(request):
    import numpy as np
    model=Path(request['model'])
    action=request['action']
    started=time.monotonic()
    def phase(name):
        sys.stderr.write('MF_PHASE '+name+'\n');sys.stderr.flush()
    phase('load')
    if action=='recognize':
        import onnxruntime as ort
        ort.disable_telemetry_events()  # includes the local Silero VAD lifecycle
        from faster_whisper import WhisperModel
        if not (model/'tokenizer.json').is_file():raise ValueError('Explicit local tokenizer is missing')
        engine=WhisperModel(str(model),device='cpu',compute_type='int8',cpu_threads=4,num_workers=1,local_files_only=True)
        loaded=time.monotonic()
        phase('inference')
        raw=base64.b64decode(request['pcm16'],validate=True)
        if len(raw)%2 or len(raw)>960000:raise ValueError('Invalid recognition window')
        audio=np.frombuffer(raw,dtype='<i2').astype(np.float32)/32768.0
        rows=[]
        if audio.size>=1600 and np.max(np.abs(audio),initial=0)>0.0001:
            segments,info=engine.transcribe(audio,language='en',task='transcribe',beam_size=5,
                condition_on_previous_text=False,vad_filter=True,word_timestamps=True)
            previous=0
            for segment in segments:
                if len(rows)>=128:raise ValueError('Excessive recognition segments')
                a,b=segment.start,segment.end
                if not all(math.isfinite(v) for v in (a,b)) or a<0 or b<=a or b>audio.size/16000+0.02:
                    raise ValueError(f'Invalid recognition timestamps: start={a}, end={b}, window={audio.size/16000}')
                first,last=round(a*16000),min(audio.size,round(b*16000))
                if first<previous or last<=first or not segment.text.strip():raise ValueError('Malformed recognition segment')
                previous=last
                rows.append(dict(start=first,end=last,text=segment.text.strip()))
        result=dict(segments=rows,samples=len(raw)//2)
    elif action=='translate':
        import ctranslate2
        import sentencepiece
        metadata=json.loads((model/'metadata.json').read_text(encoding='utf-8'))
        if (metadata['from_code'],metadata['to_code'],metadata['package_version'])!=('en','ru','1.9'):
            raise ValueError('Wrong translation package identity')
        # SentencePiece 0.2.1's Windows narrow filename loader fails on Cyrillic
        # directories. Its public serialized-proto constructor receives the same
        # authenticated bounded bytes through Python's Unicode-capable open.
        tokenizer_bytes=(model/'sentencepiece.model').read_bytes()
        if not 0<len(tokenizer_bytes)<=8*1024*1024:raise ValueError('Invalid tokenizer size')
        tokenizer=sentencepiece.SentencePieceProcessor(model_proto=tokenizer_bytes)
        engine=ctranslate2.Translator(str(model/'model'),device='cpu',compute_type='int8',inter_threads=1,intra_threads=4)
        loaded=time.monotonic()
        phase('inference')
        text=request['text']
        if len(text)>4000 or '\x00' in text:raise ValueError('Invalid translation input')
        spans=[];position=0
        # Preserve a contiguous source partition; split only at whitespace. No
        # CT2 max_input_length truncation and no tokenizer decode round trip.
        while position<len(text):
            end=min(len(text),position+600)
            sentence=re.search(r'[.!?](?:\s+|$)',text[position:end])
            if sentence:end=position+sentence.end()
            elif end<len(text):
                split=text.rfind(' ',position+1,end)
                if split>position:end=split+1
            while True:
                source=text[position:end]
                tokens=tokenizer.encode(source,out_type=str)
                if len(tokens)<=256:break
                split=text.rfind(' ',position+1,end-1)
                if split<=position:raise ValueError('Unsegmentable excessive token input')
                end=split+1
            if len(spans)>=128:raise ValueError('Excessive translation partitions')
            russian=''
            if tokens:
                row=engine.translate_batch([tokens],beam_size=4,max_input_length=0,max_decoding_length=512,
                    return_end_token=True)[0]
                output=row.hypotheses[0]
                if not output or output[-1]!='</s>' or len(output)>=512:
                    raise ValueError('Incomplete or excessive translation hypothesis')
                russian=tokenizer.decode(output[:-1]).strip()
                if not russian or '\ufffd' in russian or '<unk>' in output or any(ord(c)<32 and c not in '\t\n\r' for c in russian):
                    raise ValueError('Malformed tokenizer output')
            spans.append(dict(start=position,end=end,source=source,russian=russian))
            position=end
        result=dict(spans=spans)
    elif action=='synthesize':
        import onnxruntime as ort
        from piper import PiperVoice
        from piper.config import PiperConfig
        from piper.phonemize_espeak import ESPEAK_DATA_DIR
        config=json.loads((model/'ru_RU-ruslan-medium.onnx.json').read_text(encoding='utf-8'))
        if config['num_speakers']!=1 or config['audio']['sample_rate']!=22050 or config['espeak']['voice']!='ru' or config['phoneme_type']!='espeak':
            raise ValueError('Unqualified Ruslan configuration')
        options=ort.SessionOptions();options.intra_op_num_threads=4;options.inter_op_num_threads=1
        options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
        ort.disable_telemetry_events()
        session=ort.InferenceSession(str(model/'ru_RU-ruslan-medium.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
        # The bundled espeak-ng bridge uses a narrow Windows data-path API.
        # Set the child-only cwd via Python's wide API and pass an ASCII relative
        # member path into the SAME authenticated runtime directory. No alias,
        # copy, system cache, short-name dependency or compiled-path fallback.
        os.chdir(ESPEAK_DATA_DIR.parent)
        if not Path('espeak-ng-data/phontab').is_file():raise ValueError('Pinned offline phonemizer data is missing')
        voice=PiperVoice(session=session,config=PiperConfig.from_dict(config),espeak_data_dir=Path('espeak-ng-data'),
            download_dir=Path(request['temporary']),use_tashkeel=False)
        loaded=time.monotonic()
        phase('inference')
        text=request['text']
        if not text.strip() or len(text)>500 or '\x00' in text:raise ValueError('Invalid synthesis input')
        chunks=[];size=0;energy=0
        for chunk in voice.synthesize(text):
            if chunk.sample_rate!=22050 or chunk.sample_width!=2 or chunk.sample_channels!=1 or not np.all(np.isfinite(chunk.audio_float_array)):
                raise ValueError('Invalid Piper PCM format or nonfinite samples')
            pcm=chunk.audio_int16_bytes;size+=len(pcm)
            if not pcm or len(pcm)%2 or size>60*22050*2:raise ValueError('Piper PCM exceeds fragment bound')
            energy=max(energy,int(np.max(np.abs(np.frombuffer(pcm,dtype='<i2').astype(np.int32)),initial=0)))
            chunks.append(pcm)
        if not chunks or energy<16:raise ValueError('Empty or silent synthesized output')
        result=dict(pcm16=base64.b64encode(b''.join(chunks)).decode('ascii'),sample_rate=22050,peak=energy)
    else:raise ValueError('Unsupported local action')
    result.update(load_seconds=loaded-started,inference_seconds=time.monotonic()-loaded)
    return result


def main():
    # Parent validates this immutable request file before spawn; bound before parse.
    with Path(sys.argv[1]).open('rb') as stream:data=stream.read(2*1024*1024+1)
    if len(data)>2*1024*1024:raise ValueError('Oversized worker request')
    request=json.loads(data)
    root=Path(request['root']).resolve()
    site=Path(request['site']).resolve()
    if not site.is_relative_to(root):raise ValueError('Runtime outside portable root')
    import tempfile
    tempfile.tempdir=request['temporary']
    # CPython 3.12 platform falls back from WMI to three shell `ver` children.
    # Supply its version probe from the native Windows fact instead. This local
    # worker shim never changes the parent or permits an unmanaged helper.
    import platform
    version=sys.getwindowsversion().platform_version
    if not version or not hasattr(platform,'_syscmd_ver'):
        raise ValueError('Native Windows version probe is unavailable')
    def native_version(system='',release='',version_text='',**options):
        return 'Windows',str(version[0]),'.'.join(map(str,version))
    platform._syscmd_ver=native_version
    policy,origins=install_runtime(site,request['runtime_manifest'],request['runtime_digest'])
    events=[];violations=[]
    def audit(event,args):
        if event=='exec':policy.check_exec(args[0])
        if event.startswith('socket.') and event not in ('socket.gethostname',):
            violations.append(event)
            raise PermissionError('Offline inference refuses network operations')
        if event in ('subprocess.Popen','os.system','os.spawn','os.exec'):
            violations.append(event)
            raise PermissionError('Nested child creation is forbidden')
        if event=='open':
            path,mode,flags=args
            if isinstance(path,(str,bytes,os.PathLike)):
                writing=bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
                if writing:
                    target=Path(os.fsdecode(path)).resolve()
                    if len(events)<128:events.append(str(target))
                    if not target.is_relative_to(root):
                        violations.append(str(target))
                        raise PermissionError('Portable inference refuses outside-root write')
    sys.addaudithook(audit)
    try:
        result=run(request)
        if violations:raise ValueError('Containment violation attempted: '+repr(violations))
        result.update(ok=True,writes=events,violations=violations,python_modules=origins)
    except Exception as error:
        result=dict(ok=False,error=type(error).__name__,detail=str(error)[:1024],writes=events,violations=violations)
    sys.stdout.write(json.dumps(result,ensure_ascii=True,separators=(',',':')))
    sys.stdout.flush()
    return 0 if result['ok'] else 2


if __name__=='__main__':
    raise SystemExit(main())
