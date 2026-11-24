"""Actual pinned Whisper install/removal/recovery in a new isolated portable profile.

Uses only already authenticated cached assets. Never downloads or touches the
working model installation. All destructive tests target newly owned fixtures.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import time


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True)
    args=p.parse_args()
    from mediaforge.application.services.processing_gate import MediaProcessingGate
    from mediaforge.application.contracts.models import ModelError
    from mediaforge.features.dub.contracts import DubControl
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.infrastructure.filesystem.image_output import ReadLocks
    from mediaforge.infrastructure.models.optional_resources import OptionalResources,load_cpu_profile,digest
    paths=ProfilePaths.for_test(args.root);paths.prepare()
    if args.receipt.exists() or not args.receipt.is_relative_to(paths.logs):p.error('New portable receipt required.')
    test=paths.root/'data/model-management-native-01/Data/v2'
    if test.exists():p.error('New isolated fixture profile required; no replacement.')
    fixture=ProfilePaths.for_test(test);fixture.prepare();control=DubControl();gate=MediaProcessingGate()
    resources=OptionalResources(fixture,gate,profile=load_cpu_profile())
    spec=next(s for s in resources.specs if s.model_id=='whisper-base')
    model=next(m for m in resources.profile.data['models'] if m['model_id']==spec.model_id)
    sentinel=fixture.data/'unrelated-user-evidence.txt';sentinel.write_bytes(b'KEEP: unrelated test-owned sentinel')
    result=dict(ok=False,isolated_root=str(test),network_bytes=0,checks=[],controller_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    started=time.monotonic()
    def absent():return not resources.store.location(spec).exists()
    def record(name):
        if gate.busy or gate.failure:raise RuntimeError('Unretired model-management gate.')
        if sentinel.read_bytes()!=b'KEEP: unrelated test-owned sentinel':raise RuntimeError('Unrelated bytes changed.')
        result['checks'].append(name)
    try:
        for asset in model['members']:
            source=paths.downloads/('asset-'+asset['sha256'])
            target=fixture.downloads/source.name
            with ReadLocks() as held:
                held.parents(source.parent);held.add(source)
                if source.stat().st_nlink!=1 or source.stat().st_size!=asset['size'] or digest(source,control)!=asset['sha256']:
                    raise ValueError('Existing cached model asset differs; no acquisition authorized.')
                with source.open('rb') as stream,target.open('xb') as output:
                    while raw:=stream.read(1024*1024):control.check();output.write(raw)
        if not absent():raise ValueError('Fixture was not empty.')
        resources.install(control,selected=('whisper-base',))
        with closing(resources.store.verify(spec,control=control)):pass
        record('actual_frozen_model_install_and_verification')
        with resources.store.reserve(spec),closing(resources.store.verify(spec,control=control)):
            try:resources.remove(spec.model_id,control)
            except ModelError as error:
                if error.code!='in_use':raise
            else:raise AssertionError('Active model removal was admitted.')
        record('in_use_removal_refused_without_corrupt_status')
        resources.remove(spec.model_id,control)
        if not absent():raise AssertionError('Model remained after removal.')
        record('exact_object_removal')
        resources.install(control,selected=('whisper-base',));record('explicit_cached_reinstall')
        damaged=resources.store.location(spec)/'config.json'
        with damaged.open('r+b') as stream:
            original=stream.read(1);stream.seek(0);stream.write(bytes([original[0]^1]))
        try:resources.store.verify(spec,control=control)
        except ModelError as error:
            if error.code!='corrupt':raise
        else:raise AssertionError('Damaged model accepted.')
        result['damaged_sha256']=digest(damaged,control)
        try:resources.remove(spec.model_id,control)
        except ModelError as error:
            if error.code!='corrupt':raise
        else:raise AssertionError('Normal removal silently accepted corrupt objects.')
        record('damaged_model_refuses_verification_and_normal_removal')
        resources.remove(spec.model_id,control,discard_damaged=True)
        if not absent():raise AssertionError('Explicit damaged reset incomplete.')
        resources.install(control,selected=('whisper-base',))
        with closing(resources.store.verify(spec,control=control)):pass
        record('explicit_exact_known_object_reset_and_verified_reinstall')
        result['ok']=True
    except Exception as error:result.update(detail=str(error),code=str(getattr(error,'code','qualification_failed')))
    result.update(seconds=time.monotonic()-started,gate_busy=gate.busy,gate_failure=gate.failure,
                  installed_bytes=sum(m.size for m in spec.members),sentinel_preserved=sentinel.exists())
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))
    if not result['ok']:raise SystemExit(2)


if __name__=='__main__':main()
