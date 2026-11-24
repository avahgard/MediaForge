"""Typed sequential adapters over one model service and shared processing gate."""
import base64
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import time
from threading import local
from uuid import uuid4

from mediaforge.application.contracts.local_ai import (
    LocalAiError,RecognitionResult,SpeechSegment,TranslationResult,TranslatedSpan,PcmFragment,
    RecognitionRequest,TranslationRequest,SynthesisRequest,
)
from mediaforge.application.contracts.models import ModelError
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.filesystem.profile import local_path
from mediaforge.infrastructure.models.owned import Ownership
from mediaforge.infrastructure.models.process import run_worker
from mediaforge.infrastructure.models.native_guard import identity


@dataclass(frozen=True)
class RuntimeLease:
    native_ids:dict
    verification_seconds:float
    handles:tuple


@dataclass(frozen=True)
class WorkerRuntime:
    python:Path
    site:Path
    manifest:Path
    manifest_sha256:str
    entry:Path=Path(__file__).with_name('worker.py')

    @contextmanager
    def acquire(self,paths,control=None):
        try:
            with self._acquire(paths,control) as guard:yield guard
        except ModelError:raise
        except Exception as error:
            retained=getattr(error,'retained_resources',())
            raise LocalAiError('lifecycle_failed' if retained else 'runtime_unavailable',
                'Runtime object ownership/authentication is unavailable; inference refused.',retained_resources=retained) from error

    @contextmanager
    def _acquire(self,paths,control=None):
        """Authenticate all optional installed bytes on every use, under locks."""
        started=time.monotonic()
        if not local_path(self.site).is_relative_to(paths.root) or not local_path(self.manifest).is_relative_to(paths.manifests):
            raise LocalAiError('runtime_missing','Provider runtime must be inside the portable root.')
        with ReadLocks() as locks:
            locks.parents(self.manifest.parent);locks.add(self.manifest)
            if self.manifest.stat().st_nlink!=1:
                raise LocalAiError('incompatible','Aliased runtime manifest.')
            with self.manifest.open('rb') as stream:encoded=stream.read(4*1024*1024+1)
            if len(encoded)>4*1024*1024 or hashlib.sha256(encoded).hexdigest()!=self.manifest_sha256:
                raise LocalAiError('incompatible','Runtime manifest does not match its trusted digest.')
            members=json.loads(encoded)
            if not isinstance(members,list) or not 1<=len(members)<=10000:
                raise LocalAiError('incompatible','Invalid runtime inventory.')
            expected={}
            folded=set();total=0
            for row in members:
                if control:control.check()
                if not isinstance(row,dict) or set(row)!={'name','size','sha256'} or not isinstance(row['name'],str) or type(row['size']) is not int or not 0<=row['size']<=2*1024**3 or not isinstance(row['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',row['sha256']):
                    raise LocalAiError('incompatible','Malformed runtime member.')
                name=row['name']
                parts=name.split('/')
                if not parts or any(not p or p in ('.','..') or p.endswith((' ','.')) or ':' in p or '\\' in p or '\0' in p for p in parts) or name.casefold() in folded or name.startswith('/'):
                    raise LocalAiError('incompatible','Unsafe runtime member.')
                folded.add(name.casefold());total+=row['size']
                if total>2*1024**3:raise LocalAiError('incompatible','Runtime exceeds its size bound.')
                expected[name]=row
            locks.parents(self.site)
            expected_directories={parent.as_posix() for name in expected for parent in Path(name).parents if parent!=Path('.')}
            files=[]
            for file in self.site.rglob('*'):
                if control:control.check()
                if len(files)>10000:raise LocalAiError('incompatible','Runtime inventory exceeds bound.')
                if file.lstat().st_file_attributes&0x400:raise LocalAiError('incompatible','Reparse runtime member.')
                if file.is_dir():
                    if file.relative_to(self.site).as_posix() not in expected_directories:
                        raise LocalAiError('incompatible','Unexpected runtime directory.')
                    locks.add(file,True)
                else:files.append(file)
            if {p.relative_to(self.site).as_posix() for p in files}!=set(expected):
                raise LocalAiError('incompatible','Runtime has extra or missing files.')
            native_ids={}
            for file in files:
                if control:control.check()
                if file.stat().st_nlink!=1:raise LocalAiError('incompatible','Aliased runtime member.')
                if file.suffix.lower() in ('.dll','.pyd'):
                    # Windows can retain an unused image section's old name
                    # after folder relocation, yielding a NULL debugger hFile.
                    # Opening for FILE_WRITE_DATA asks the filesystem to discard
                    # unused image sections (MmFlushForWrite). No bytes, ACLs or
                    # attributes are written. An active image/writer or denied
                    # access refuses this admission; there is no name-only load.
                    # This is cache retirement, not authenticity: acquire the
                    # deny-write lease and rehash the resulting object below.
                    with Ownership() as cache_owner:
                        cache_owner.open(file,0xC0000000,1,3)
                handle=locks.add(file);row=expected[file.relative_to(self.site).as_posix()]
                key,info=identity(locks.api,handle)
                if info.links!=1:raise LocalAiError('incompatible','Opened runtime member has unsafe links.')
                native_ids[key]=str(file)
                hashed=hashlib.sha256()
                with file.open('rb') as stream:
                    while block:=stream.read(1024*1024):
                        if control:control.check()
                        hashed.update(block)
                if file.stat().st_size!=row['size'] or hashed.hexdigest()!=row['sha256']:
                    raise LocalAiError('incompatible','Runtime member digest mismatch.')
            for file in (self.python,self.entry):
                local_path(file);locks.parents(file.parent);locks.add(file)
            yield RuntimeLease(native_ids,time.monotonic()-started,tuple(locks.handles))


class LocalProviders:
    def __init__(self,paths,runtime,gate,*,timeout=180):
        self.paths=paths;self.runtime=runtime;self.gate=gate;self.timeout=timeout
        self.models=None
        self.receipts=[]
        self._owners=local()

    def environment(self):
        paths=self.paths
        env=paths.child_environment({})
        # Inference needs OS runtime facts, not the parent's credentials, proxy
        # configuration, developer hooks or arbitrary executable search paths.
        for key in ('SYSTEMROOT','WINDIR','SYSTEMDRIVE','COMSPEC','PROCESSOR_ARCHITECTURE',
                    'PROCESSOR_ARCHITEW6432','PROCESSOR_IDENTIFIER','NUMBER_OF_PROCESSORS'):
            if key in os.environ:env[key]=os.environ[key]
        system=env.get('SYSTEMROOT')
        if not system:raise LocalAiError('runtime_unavailable','Windows system root is unavailable.')
        env['PATH']=str(Path(system)/'System32')+os.pathsep+system
        env.update(PYTHONDONTWRITEBYTECODE='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
            HF_HUB_DISABLE_TELEMETRY='1',HF_HOME=str(paths.cache/'huggingface'),
            HUGGINGFACE_HUB_CACHE=str(paths.cache/'huggingface/hub'),
            XDG_CACHE_HOME=str(paths.cache),XDG_DATA_HOME=str(paths.data),XDG_CONFIG_HOME=str(paths.config),
            ARGOS_PACKAGES_DIR=str(paths.models),ARGOS_TRANSLATE_PACKAGES_DIR=str(paths.models),
            HOME=str(paths.config),USERPROFILE=str(paths.config),APPDATA=str(paths.config/'roaming'),
            LOCALAPPDATA=str(paths.config/'local'),OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',
            OPENBLAS_NUM_THREADS='4',NUMEXPR_NUM_THREADS='4',TOKENIZERS_PARALLELISM='false',
            CUDA_VISIBLE_DEVICES='',ORT_DISABLE_TELEMETRY='1')
        return env

    def _run(self,spec,guard,action,payload,control,*,on_started=None,on_phase=None):
        token=getattr(self._owners,'token',None)
        owns_gate=token is None
        if owns_gate:token=self.gate.acquire()
        if token is None:raise LocalAiError('busy','Another Media or local model operation owns processing.')
        session=None
        try:
            control.check()
            self.paths.prepare()
            session=self.paths.temporary/('inference-'+uuid4().hex)
            session.mkdir()
            borrowed_runtime=getattr(self._owners,'runtime_guard',None)
            if borrowed_runtime is not None and borrowed_runtime[0] is not self.runtime:
                raise LocalAiError('runtime_integrity','Operation runtime lease belongs to another runtime.')
            context=(nullcontext(borrowed_runtime[1]) if borrowed_runtime is not None
                     else self.runtime.acquire(self.paths,control))
            with context as runtime_guard,Ownership() as owned:
                owned.parents.parents(session)
                request=dict(action=action,model=str(guard.path),site=str(self.runtime.site),root=str(self.paths.root),
                    temporary=str(session),runtime_manifest=str(getattr(self.runtime,'manifest','')),
                    runtime_digest=getattr(self.runtime,'manifest_sha256',''),**payload)
                encoded=json.dumps(request,separators=(',',':')).encode()
                path=session/'request.json'
                owned.publish(path,io.BytesIO(encoded),len(encoded),hashlib.sha256(encoded).hexdigest())
                owned.parents.add(path)
                result=run_worker([str(self.runtime.python),'-I','-S','-B',str(self.runtime.entry),str(path)],
                    paths=self.paths,environment=self.environment(),control=control,timeout=self.timeout,on_started=on_started,on_phase=on_phase,runtime_guard=runtime_guard)
            # Receipts contain timing/resource/containment facts, not PCM/transcripts.
            self.receipts.append(dict(model=spec.model_id,revision=spec.revision,action=action,
                metrics=result['metrics'],load_seconds=result['load_seconds'],inference_seconds=result['inference_seconds'],
                writes=result.get('writes'),violations=result.get('violations'),
                runtime_verification_seconds=0 if borrowed_runtime is not None else getattr(runtime_guard,'verification_seconds',0),
                operation_runtime_lease=borrowed_runtime is not None,
                python_modules=result.get('python_modules')))
            self.receipts[:]=self.receipts[-64:]
            return result
        except BaseException as error:
            if getattr(error,'retained_resources',()):
                self.gate.poison(token,str(error))
                raise LocalAiError('lifecycle_failed','Local inference resources remain retained.',retained_resources=error.retained_resources) from error
            if isinstance(error,Exception) and not isinstance(error,ModelError):
                raise LocalAiError('runtime_unavailable','Local runtime/model files are missing, inaccessible or incompatible.') from error
            raise
        finally:
            try:
                if not control.cleanup_failed and not self.gate.failure and session is not None:
                    # Only exact files this invocation created, never recursive cleanup.
                    (session/'request.json').unlink(missing_ok=True)
                    session.rmdir()
            except OSError as error:
                self.gate.poison(token,'Inference workspace cleanup unresolved.')
                raise LocalAiError('lifecycle_failed','Inference workspace retained.',retained_resources=(session,)) from error
            finally:
                if control.cleanup_failed and not self.gate.failure:self.gate.poison(token,'Unreaped inference child.')
                if owns_gate and not self.gate.failure:self.gate.release(token)

    def smoke(self,spec,guard,*,control=None):
        from mediaforge.features.media.processing_control import ProcessingControl
        if control is None:control=ProcessingControl(lambda:LocalAiError('cancelled','Local smoke cancelled.'))
        action,payload={
            'whisper-base':('recognize',{'pcm16':''}),
            'argos-en-ru':('translate',{'text':'This is a local test.'}),
            'piper-ruslan':('synthesize',{'text':'Проверка локального голоса.'}),
        }[spec.model_id]
        self._run(spec,guard,action,payload,control)

    def _selected(self,key,action,payload,control,**options):
        if self.models is None:raise LocalAiError('runtime_missing','Shared ModelService is not bound.')
        token=self.gate.acquire()
        if token is None:raise LocalAiError('busy','Another Media or local-model operation owns processing.')
        self._owners.token=token
        try:
            with self.models.acquire(key,control=control) as guard:
                spec=self.models.spec(key)
                return self._run(spec,guard,action,payload,control,**options),spec
        except BaseException as error:
            if getattr(error,'retained_resources',()) or control.cleanup_failed:
                if not self.gate.failure:self.gate.poison(token,'Local model/worker ownership remains unresolved.')
            raise
        finally:
            del self._owners.token
            if not self.gate.failure:self.gate.release(token)

    def recognize(self,request:RecognitionRequest,control,**options):
        row,spec=self._selected('whisper-base','recognize',{'pcm16':base64.b64encode(request.pcm16).decode()},control,**options)
        return RecognitionResult(tuple(SpeechSegment(s['start']+request.offset_samples,s['end']+request.offset_samples,s['text']) for s in row['segments']),row['samples'],spec.revision)

    def translate(self,request:TranslationRequest,control,**options):
        from mediaforge.features.dub.glossary import protected_spans, preserved, mask_context, restore_context, POLICY
        identifiers=protected_spans(request.text)
        reference=self._translate_plain(request,control,**options)
        if not identifiers:return reference
        if preserved(reference.text,identifiers):
            # Native Argos already kept every exact identifier in order. Keep
            # its complete sentence grammar and punctuation unchanged.
            return TranslationResult(reference.spans,reference.model_revision,reference.spans,POLICY)
        try:
            masked,markers=mask_context(request.text,identifiers)
            result=self._translate_plain(TranslationRequest(masked),control,**options)
            effective=restore_context(result.text,markers,request.text,reference.text)
        except ValueError as error:
            failure=LocalAiError('terminology_unqualified',
                'Argos could not preserve the recognized technical identifiers with complete context. '
                'No incomplete Dub is published; the original recognition and translation are retained.')
            failure.reference_spans=reference.spans
            raise failure from error
        return TranslationResult((TranslatedSpan(0,len(request.text),request.text,effective),),
                                 reference.model_revision,reference.spans,POLICY)

    def _translate_plain(self,request:TranslationRequest,control,**options):
        row,spec=self._selected('argos-en-ru','translate',{'text':request.text},control,**options)
        spans=tuple(TranslatedSpan(**s) for s in row['spans'])
        position=0
        for span in spans:
            if span.start!=position or span.source!=request.text[span.start:span.end] or not span.start<span.end<=len(request.text):
                raise LocalAiError('invalid_output','Translation did not cover the complete input.')
            position=span.end
        if position!=len(request.text):raise LocalAiError('invalid_output','Translation silently lost source text.')
        return TranslationResult(spans,spec.revision)

    def synthesize(self,request:SynthesisRequest,control,**options):
        row,spec=self._selected('piper-ruslan','synthesize',{'text':request.text},control,**options)
        raw=base64.b64decode(row['pcm16'],validate=True)
        if not raw or len(raw)%2 or len(raw)>60*22050*2 or row['sample_rate']!=22050:
            raise LocalAiError('invalid_output','Invalid Ruslan fragment.')
        return PcmFragment(raw,22050,spec.revision)
