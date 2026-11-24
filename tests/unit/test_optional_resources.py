"""Final optional resources: byte authority, safe extraction and cancellation."""
import hashlib
import io
import zipfile

import pytest

from mediaforge.application.contracts.models import ModelError
from mediaforge.infrastructure.models import optional_resources as optional


def test_actual_profile_authenticates_both_model_components_without_importing_torch():
    import sys
    before = set(sys.modules)
    profile = optional.load_profile()
    assert len(profile.runtime.members) == 18276
    assert len(profile.models) == 2
    assert sum(m.size for s in profile.models for m in s.members) == 2498386873
    assert profile.data['network_bytes'] == 5406388750
    assert profile.data['runtime_bytes'] == 4679145132
    assert 'torch' not in set(sys.modules) - before


def test_cpu_profile_reuses_locked_provider_bytes_without_dev_launchers():
    profile = optional.load_cpu_profile()
    assert len(profile.runtime.members) == 2272
    assert profile.data['runtime_bytes'] == 249438230
    assert {s.model_id for s in profile.models} == {'whisper-base', 'argos-en-ru'}
    assert not any(m.name.startswith('bin/') for m in profile.runtime.members)
    assert not any(a['name'] in ('piper-tts','pathvalidate') for a in profile.data['assets'])
    assert profile.data['runtime_bytes'] < 2 * 1024**3


def test_combined_requirements_stay_inside_the_one_acquisition_budget(tmp_path):
    from mediaforge.infrastructure.models.dub_requirements import DubRequirements
    from mediaforge.application.services.processing_gate import MediaProcessingGate
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    resources = DubRequirements(ProfilePaths.for_test(tmp_path / 'portable'), MediaProcessingGate())
    sizes = resources.sizes()
    assert sizes['network'] == 5836190711
    assert sizes['installed'] == 7792114455
    assert sizes['peak'] <= optional.MAX_DISK
    assert len(resources.inventory()) == 6


def test_edited_application_registry_is_refused_before_manifest_parse(tmp_path, monkeypatch):
    (tmp_path / 'qwen-profile.json').write_text('{}')
    monkeypatch.setattr(optional, 'RESOURCE_ROOT', tmp_path)
    with pytest.raises(ModelError, match='authentication'):
        optional.load_profile()


@pytest.mark.parametrize('name', ['../x', '/x', 'x\\y', 'x:stream', 'x//y', 'x/CON.txt',
                                  'x/trailing.', 'x/trailing ', 'x\x00y', 'x/../y'])
def test_unsafe_member_names_refused(name):
    with pytest.raises(ModelError):
        optional.safe_member(name)


def make_wheel(names):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w') as wheel:
        for name, value in names:
            wheel.writestr(name, value)
    raw.seek(0)
    return zipfile.ZipFile(raw)


def member(name, raw=b'a'):
    return optional.RuntimeMember(name, len(raw), hashlib.sha256(raw).hexdigest())


def test_every_upstream_member_requires_explicit_mapping_or_omission():
    with make_wheel([('package.py', b'a'), ('unreviewed.py', b'a')]) as wheel:
        with pytest.raises(ModelError, match='Undeclared'):
            optional.wheel_plan(wheel, {}, {'package.py': member('package.py')})


def test_derived_metadata_rename_and_explicit_omission():
    name = 'pkg-1+mf.dist-info/METADATA'
    asset = dict(rename_from='pkg-1.dist-info', rename_to='pkg-1+mf.dist-info',
                 omit=['obsolete.py'], overlays={})
    with make_wheel([('pkg-1.dist-info/METADATA', b'a'), ('obsolete.py', b'old')]) as wheel:
        assert set(optional.wheel_plan(wheel, asset, {name: member(name)})) == {name}


def test_case_colliding_zip_members_refused():
    with make_wheel([('p.py', b'a'), ('P.py', b'a')]) as wheel:
        with pytest.raises(ModelError, match='Duplicate'):
            optional.wheel_plan(wheel, {}, {'p.py': member('p.py'), 'P.py': member('P.py')})


def test_symlink_archive_member_refused():
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as wheel:
        row = zipfile.ZipInfo('p.py')
        row.external_attr = 0o120777 << 16
        wheel.writestr(row, b'a')
    data.seek(0)
    with zipfile.ZipFile(data) as wheel:
        with pytest.raises(ModelError, match='Special'):
            optional.wheel_plan(wheel, {}, {'p.py': member('p.py')})


def test_cancellation_prevents_read_and_progress():
    class Cancel:
        def check(self):
            raise ModelError('cancelled', 'cancelled')
    stream = io.BytesIO(b'private')
    events = []
    checked = optional._CheckedStream(stream, Cancel(), events.append)
    with pytest.raises(ModelError):
        checked.read(1024)
    assert stream.tell() == 0
    assert not events


def test_runtime_member_bounds_do_not_change_c2_model_member_bounds():
    name = 'pkg/' + 'x' * 120 + '.py'
    optional.RuntimeMember(name, 0, '0' * 64)
    with pytest.raises(ValueError):
        optional.ModelMember(name, 0, '0' * 64)


@pytest.mark.native
def test_failed_optional_install_rolls_back_without_adopting_partial_bytes(tmp_path, monkeypatch):
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.application.services.processing_gate import MediaProcessingGate
    class Control:
        def check(self): pass
    source = tmp_path / 'wrong.whl'
    with zipfile.ZipFile(source, 'w') as wheel:
        wheel.writestr('p.py', b'wrong')
    expected = member('p.py', b'expected')
    spec = optional.ResourceSpec('qwen-runtime-test', 'test1', (expected,))
    asset = dict(filename=source.name, size=source.stat().st_size,
                 sha256=hashlib.sha256(source.read_bytes()).hexdigest(), url='unused')
    data = dict(runtime_bytes=expected.size, network_bytes=asset['size'], assets=[asset], models=[],
                runtime_manifest_sha256='0' * 64)
    profile = optional.ResourceProfile(data, spec, (), tmp_path)
    paths = ProfilePaths.for_test(tmp_path / 'portable data Кириллица')
    gate = MediaProcessingGate()
    service = optional.OptionalResources(paths, gate, profile=profile)
    monkeypatch.setattr(optional, 'download', lambda *args: source)
    with pytest.raises(ModelError, match='size'):
        service.install(Control())
    assert not service.store.location(spec).exists()
    assert not list(paths.manifests.glob('.install-pending-*'))
    assert not gate.busy
    assert list(paths.downloads.glob('.optional-*'))  # retained failure, never adopted


@pytest.mark.native
def test_optional_verification_keeps_write_delete_denial_until_explicit_close(tmp_path):
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.infrastructure.models.owned import Ownership
    paths = ProfilePaths.for_test(tmp_path / 'portable')
    paths.prepare()
    store = optional.OptionalResourceStore(paths)
    spec = optional.ResourceSpec('test-resource', 'v1', (member('p.py'),))
    path = store.location(spec)
    path.mkdir(parents=True)
    (path / 'p.py').write_bytes(b'a')
    guard = store.verify(spec)
    try:
        with Ownership() as attacker:
            with pytest.raises(ModelError):
                attacker.open(path / 'p.py', 0x40000000, 7, 3)
        with Ownership() as attacker:
            with pytest.raises(ModelError):
                attacker.open(path / 'p.py', 0x10000, 7, 3)
    finally:
        guard.close()
    with Ownership() as writer:
        writer.open(path / 'p.py', 0x40000000, 7, 3)


@pytest.mark.native
def test_optional_remove_refuses_active_reader_then_retires_only_exact_members(tmp_path):
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.application.services.processing_gate import MediaProcessingGate
    class Control:
        def check(self): pass
    spec = optional.ResourceSpec('test-runtime', 'v1', (member('p.py'),))
    profile = optional.ResourceProfile(dict(runtime_bytes=1, network_bytes=0), spec, (), tmp_path)
    paths = ProfilePaths.for_test(tmp_path / 'portable')
    paths.prepare()
    service = optional.OptionalResources(paths, MediaProcessingGate(), profile=profile)
    target = service.store.location(spec)
    target.mkdir(parents=True)
    (target / 'p.py').write_bytes(b'a')
    sentinel = target.parent / 'unrelated-user-file.txt'
    sentinel.write_text('preserve')
    guard = service.store.verify(spec)
    try:
        with pytest.raises(ModelError):
            service.remove(spec.model_id, Control())
        assert (target / 'p.py').read_bytes() == b'a'
        assert not service.gate.busy
    finally:
        guard.close()
    service.remove(spec.model_id, Control())
    assert not target.exists()
    assert sentinel.read_text() == 'preserve'
    assert not service.gate.busy


def test_runtime_reservation_is_retained_until_native_owner_is_reaped():
    from mediaforge.infrastructure.models.qwen_runtime import RetainedRuntime
    class Stack:
        closed = False
        def close(self): self.closed = True
    class Process:
        def poll(self): return None
    class Owner:
        process = Process()
        native = None
    stack = Stack()
    retained = RetainedRuntime(stack, (Owner(),))
    with pytest.raises(RuntimeError, match='retirement'):
        retained.close()
    assert not stack.closed


@pytest.mark.native
def test_explicit_damaged_reset_does_not_adopt_bytes_and_clears_only_registry_intent(tmp_path):
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.application.services.processing_gate import MediaProcessingGate
    from mediaforge.infrastructure.models.installation_lifecycle import pending_path
    class Control:
        def check(self):pass
    spec=optional.ResourceSpec('test-runtime','v1',(member('nested/p.py'),member('missing.py')))
    profile=optional.ResourceProfile(dict(runtime_bytes=2,network_bytes=0),spec,(),tmp_path)
    paths=ProfilePaths.for_test(tmp_path/'portable');paths.prepare()
    service=optional.OptionalResources(paths,MediaProcessingGate(),profile=profile)
    target=service.store.location(spec);(target/'nested').mkdir(parents=True)
    (target/'nested/p.py').write_bytes(b'changed untrusted component bytes')
    marker=pending_path(service.store,spec);marker.touch()
    untouched=paths.manifests/'.unrelated-marker';untouched.write_text('preserve')
    with pytest.raises(ModelError):service.remove(spec.model_id,Control())
    assert target.exists() and marker.exists()
    service.remove(spec.model_id,Control(),discard_damaged=True)
    assert not target.exists() and not marker.exists() and untouched.read_text()=='preserve'
    assert not service.gate.busy


@pytest.mark.native
def test_damaged_reset_refuses_unknown_members_before_any_deletion(tmp_path):
    from mediaforge.infrastructure.filesystem.profile import ProfilePaths
    from mediaforge.application.services.processing_gate import MediaProcessingGate
    class Control:
        def check(self):pass
    spec=optional.ResourceSpec('test-runtime','v1',(member('p.py'),))
    profile=optional.ResourceProfile(dict(runtime_bytes=1,network_bytes=0),spec,(),tmp_path)
    paths=ProfilePaths.for_test(tmp_path/'portable');paths.prepare()
    service=optional.OptionalResources(paths,MediaProcessingGate(),profile=profile)
    target=service.store.location(spec);target.mkdir(parents=True)
    (target/'p.py').write_bytes(b'a');(target/'user.txt').write_text('preserve')
    with pytest.raises(ModelError,match='Unknown removal'):
        service.remove(spec.model_id,Control(),discard_damaged=True)
    assert (target/'p.py').read_bytes()==b'a' and (target/'user.txt').read_text()=='preserve'
    assert not service.gate.busy
