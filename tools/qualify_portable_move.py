"""Move only the exact newly generated internal QA deployment and its Data.

The GUI must already be closed. Copies only pinned Whisper cached assets,
installs through the real inert installer, and verifies after folder movement.
No working models, sources, historical artifacts or Git objects are moved.
"""
import argparse
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import time


def cross_copy(paths,source,target,package,gate,control,spec):
    """Copy this owned QA deployment, including its bounded real test Data.

    Keep source objects held, create target members exclusively through the
    native publication primitive, and authenticate again on the other volume.
    The source is preserved. This is not a product migration/resume facility.
    """
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths,local_path
    from mediaforge.infrastructure.filesystem.image_output import ReadLocks
    from mediaforge.infrastructure.models.owned import Ownership
    from mediaforge.infrastructure.models.core_runtime import CoreRuntime
    from mediaforge.infrastructure.models.optional_resources import OptionalResources,load_cpu_profile,digest
    from mediaforge.infrastructure.settings.preferences import PortablePreferences
    source=local_path(source);target=local_path(target)
    if target.exists() or not target.parent.is_dir() or target.anchor.casefold()==source.anchor.casefold():
        raise ValueError('A new explicit target on another local volume is required.')
    with closing(CoreRuntime(source,package['manifest_sha256'])),ReadLocks() as held,Ownership() as output:
        held.parents(source);files={};entries=0
        for file in source.rglob('*'):
            entries+=1;local_path(file)
            if entries>20000:raise ValueError('Cross-volume QA inventory exceeds its bound.')
            if file.is_dir():held.add(file,True);continue
            held.add(file)
            if file.stat().st_nlink!=1:raise ValueError('Aliased QA source is unsupported.')
            files[file.relative_to(source)]=(file.stat().st_size,digest(file,control))
        total=sum(size for size,_ in files.values())
        used=sum(f.stat().st_size for f in paths.root.parent.parent.rglob('*') if f.is_file())
        if total>1536*1024**2 or used+total+256*1024**2>20*1024**3:
            raise ValueError('Cross-volume copy plus reserved evidence exceeds the admitted storage plan.')
        import shutil
        if shutil.disk_usage(target.parent).free<total+max(1024**3,total//10):raise ValueError('Cross-volume headroom unavailable.')
        output.parents.parents(target.parent);target.mkdir();output.parents.add(target,True)
        source_volume=source.stat().st_dev;target_volume=target.stat().st_dev
        if source_volume==target_volume:raise ValueError('Different drive letters do not establish a different native volume.')
        for relative,(size,expected) in sorted(files.items()):
            control.check();dest=target/relative
            # Construct each directory only after its ancestors are leased.
            for folder in reversed((dest.parent,*dest.parent.parents)):
                if folder.is_relative_to(target):
                    output.parents.parents(folder.parent)
                    if not folder.exists():folder.mkdir()
                    output.parents.add(folder,True)
            with (source/relative).open('rb') as stream:output.publish(dest,stream,size,expected)
        if {p.relative_to(source) for p in source.rglob('*') if p.is_file()}!=set(files):
            raise ValueError('Source QA namespace changed during copy; copied objects are not activated.')
        after_paths=ProfilePaths.from_executable(target/'MediaForge.exe')
        after=OptionalResources(after_paths,gate,profile=load_cpu_profile())
        with closing(CoreRuntime(target,package['manifest_sha256'])),closing(after.store.verify(spec,control=control)):
            for relative,(size,expected) in files.items():
                dest=target/relative
                if dest.stat().st_size!=size or digest(dest,control)!=expected:raise ValueError('Copied member differs.')
            if PortablePreferences(after_paths).load()['dub_voice']!='ryan':raise ValueError('Cross-volume preferences differ.')
        return dict(ok=True,source=str(source),target=str(target),source_volume=source_volume,target_volume=target_volume,
            bytes=total,members=len(files),source_preserved=True,all_member_hashes_rechecked=True,
            manifest_sha256=package['manifest_sha256'],model_bytes=sum(m.size for m in spec.members),
            settings_sha256=digest(after_paths.config/'settings.json',control),voice='ryan',network_bytes=0,
            scope='Selected authenticated core plus real Whisper and settings; no complete Qwen runtime/inference relocation claim',
            gui_relaunch_pending=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True)
    p.add_argument('--package-generation',type=int,default=1)
    p.add_argument('--copy-to',type=Path,help='Copy the already Unicode-moved owned QA generation to another volume; preserve source.')
    args=p.parse_args()
    if not 1<=args.package_generation<=20:p.error('Use an existing exact QA generation within 1..20.')
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths,local_path
    from mediaforge.infrastructure.models.optional_resources import OptionalResources,load_cpu_profile,digest
    from mediaforge.infrastructure.models.owned import Ownership
    from mediaforge.infrastructure.filesystem.image_output import ReadLocks
    from mediaforge.infrastructure.models.core_runtime import CoreRuntime
    from mediaforge.infrastructure.settings.preferences import PortablePreferences
    from mediaforge.application.services.processing_gate import MediaProcessingGate
    from mediaforge.features.dub.contracts import DubControl
    paths=ProfilePaths.for_test(args.root);paths.activate_temporary()
    if args.receipt.exists() or not args.receipt.is_relative_to(paths.logs):p.error('New managed receipt required.')
    package=f'portable-package-qa-{args.package_generation:02d}'
    previous=json.loads((paths.logs/(package+'.json')).read_bytes())
    if previous.get('ok') is not True:p.error('Successful authenticated package receipt required.')
    original=local_path(Path(previous['unpacked']));parent=local_path(paths.data/package/'unpacked')
    moved=local_path(parent/'MediaForge перенесён')
    if args.copy_to:
        if not moved.is_dir() or original.exists():p.error('Only the already qualified, exact moved QA deployment may be copied.')
        control=DubControl();gate=MediaProcessingGate();test=ProfilePaths.from_executable(moved/'MediaForge.exe')
        resources=OptionalResources(test,gate,profile=load_cpu_profile());spec=next(s for s in resources.specs if s.model_id=='whisper-base')
        started=time.monotonic()
        try:result=cross_copy(paths,moved,args.copy_to,previous,gate,control,spec)
        except Exception as error:result=dict(ok=False,code=str(getattr(error,'code','qualification_failed')),detail=str(error))
        result.update(seconds=time.monotonic()-started,gate_busy=gate.busy,gate_failure=gate.failure,
            controller_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
        print(json.dumps(result,ensure_ascii=False))
        if not result['ok']:raise SystemExit(2)
        return
    # Both fully resolved absolute targets must stay in the one explicit QA
    # directory, with the expected source name and no existing destination.
    if original!=parent/'MediaForge' or not original.is_relative_to(parent) or not moved.is_relative_to(parent) or moved.exists():
        p.error('Exact original and new contained QA targets required.')
    control=DubControl();gate=MediaProcessingGate();test=ProfilePaths.from_executable(original/'MediaForge.exe')
    resources=OptionalResources(test,gate,profile=load_cpu_profile());spec=next(s for s in resources.specs if s.model_id=='whisper-base')
    model=next(m for m in resources.profile.data['models'] if m['model_id']==spec.model_id)
    result=dict(ok=False,original=str(original),moved=str(moved),network_bytes=0,controller_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    started=time.monotonic()
    try:
        with closing(CoreRuntime(original,previous['manifest_sha256'])):
            if list(test.models.iterdir()) or list(test.downloads.iterdir()):raise ValueError('Clean QA profile is not model/download-empty.')
            if PortablePreferences(test).load()['dub_voice']!='ryan':raise ValueError('GUI preference control was not saved.')
            used=sum(f.stat().st_size for f in paths.root.rglob('*') if f.is_file())
            if used+2*sum(m.size for m in spec.members)+16*1024**2>20*1024**3:raise ValueError('Move qualification exceeds its storage bound.')
            for asset in model['members']:
                source=paths.downloads/('asset-'+asset['sha256']);target=test.downloads/source.name
                with ReadLocks() as held,Ownership() as owner:
                    held.parents(source.parent);held.add(source);owner.parents.parents(target.parent)
                    if source.stat().st_nlink!=1 or source.stat().st_size!=asset['size'] or digest(source,control)!=asset['sha256']:
                        raise ValueError('Cached pinned asset differs.')
                    with source.open('rb') as stream:owner.publish(target,stream,asset['size'],asset['sha256'])
            resources.install(control,selected=('whisper-base',))
            with closing(resources.store.verify(spec,control=control)):pass
            settings_sha=digest(test.config/'settings.json',control)
            before={m.name:(resources.store.location(spec)/m.name).stat().st_ino for m in spec.members}
        # No live app or verifier lease is retained through this owned rename.
        original.rename(moved)
        after_paths=ProfilePaths.from_executable(moved/'MediaForge.exe');after_paths.prepare()
        if after_paths.root!=moved/'Data/v2':raise ValueError('Portable root did not follow executable.')
        after=OptionalResources(after_paths,gate,profile=load_cpu_profile())
        with closing(CoreRuntime(moved,previous['manifest_sha256'])),closing(after.store.verify(spec,control=control)):
            if digest(after_paths.config/'settings.json',control)!=settings_sha or PortablePreferences(after_paths).load()['dub_voice']!='ryan':
                raise ValueError('Moved preferences differ.')
            if any((after.store.location(spec)/m.name).stat().st_ino!=before[m.name] for m in spec.members):
                raise ValueError('Move replaced a model file object.')
        result.update(ok=True,root=str(after_paths.root),settings_sha256=settings_sha,model_bytes=sum(m.size for m in spec.members),
            model_fileids_preserved=True,manifest_sha256=previous['manifest_sha256'],gui_relaunch_pending=True,
            ordinary_move_only=True,cross_disk_copy_not_tested=True)
    except Exception as error:result.update(code=str(getattr(error,'code','qualification_failed')),detail=str(error))
    result.update(seconds=time.monotonic()-started,gate_busy=gate.busy,gate_failure=gate.failure)
    with args.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))
    if not result['ok']:raise SystemExit(2)


if __name__=='__main__':main()
