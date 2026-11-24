"""Real Windows installation/repair against small pinned payloads with scripted HTTPS."""
from contextlib import closing
from dataclasses import asdict
from email.message import Message
from hashlib import sha256
import io
from itertools import groupby
import json
from types import SimpleNamespace
from urllib.request import HTTPHandler, HTTPSHandler, ProxyHandler, build_opener
from urllib.response import addinfourl
import zipfile

import pytest

from mediaforge.application.contracts.models import ModelError, ModelMember
from mediaforge.application.services.optional_models import ModelsControl
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.models import optional_resources as optional
from mediaforge.infrastructure.models.acquisition import DubAcquisition
from mediaforge.infrastructure.models.installation_lifecycle import pending_path
from mediaforge.infrastructure.settings.preferences import PortablePreferences, ThemePreference
from mediaforge.infrastructure.settings.storage import measure_storage


pytestmark = [pytest.mark.native, pytest.mark.integration]


def pin(raw):
    return sha256(raw).hexdigest()


class Payloads:
    def __init__(self, root, monkeypatch):
        root.mkdir()
        self.paths = ProfilePaths.for_test(root / 'Portable Кириллица' / 'Data/v2')
        self.paths.prepare()
        self.gate = MediaProcessingGate()
        self.calls = []
        self.objects = {}
        self.on_open = lambda: None
        self.registries = root / 'bundled'
        self.registries.mkdir()
        self.profiles = (
            self.profile('dub-cpu-runtime', 'whisper-base', 'argos-en-ru'),
            self.profile('qwen-runtime-cu128', 'qwen3-tts-06b-customvoice', 'qwen3-tts-tokenizer-12hz'),
        )
        self.acquisition = DubAcquisition(SimpleNamespace(paths=self.paths, profiles=self.profiles))
        monkeypatch.setattr(optional.urllib.request, 'build_opener', lambda *args: SimpleNamespace(open=self.open))

    def asset(self, name, raw):
        url = 'https://huggingface.co/fixture/' + name
        self.objects[url] = raw
        return dict(url=url, size=len(raw), sha256=pin(raw))

    def open(self, request, timeout):
        self.calls.append(request.full_url)
        self.on_open()
        raw = self.objects[request.full_url]
        response = io.BytesIO(raw)
        response.status, response.url = 200, request.full_url
        response.headers = {'Content-Length': str(len(raw))}
        return response

    def profile(self, runtime, *models):
        payload = runtime.encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as wheel:
            wheel.writestr('package.py', payload)
        entry = optional.RuntimeMember('package.py', len(payload), pin(payload))
        spec = optional.ResourceSpec(runtime, 'runtime1', (entry,))
        wheel = self.asset(runtime + '.whl', buffer.getvalue())
        wheel['filename'] = runtime + '.whl'
        companion_name = runtime + '.json'
        companion = json.dumps([asdict(entry)]).encode()
        (self.registries / companion_name).write_bytes(companion)
        entries, registry_items = [], []
        for model in models:
            raw = model.encode()
            asset = self.asset(model, raw)
            entries.append(optional.ResourceSpec(model, 'model1', (ModelMember('model.bin', len(raw), pin(raw)),)))
            registry_items.append(dict(model_id=model, members=[dict(name='model.bin', **asset)]))
        profile = optional.ResourceProfile(
            dict(assets=[wheel], models=registry_items, manifest_name=companion_name,
                 manifest_resource=companion_name, runtime_manifest_sha256=pin(companion)),
            spec, tuple(entries), self.registries)
        return optional.OptionalResources(self.paths, self.gate, profile=profile)


@pytest.fixture
def payloads(tmp_path, monkeypatch):
    return Payloads(tmp_path / 'fixture', monkeypatch)


def control():
    return ModelsControl(lambda: ModelError('cancelled', 'cancelled'))


def ignore_progress(*args):
    pass


def scripted_https(monkeypatch, respond):
    """Run urllib's real redirect loop with transport that only accepts scripted HTTPS."""
    contacts, bodies = [], []

    class Transport(HTTPSHandler):
        def https_open(self, request):
            contacts.append(request.full_url)
            status, fields, payload, final_url = respond(request.full_url)
            headers = Message()
            for name, value in fields.items():
                headers[name] = str(value)
            body = io.BytesIO(payload)
            bodies.append(body)
            response = addinfourl(body, headers, final_url or request.full_url, status)
            response.msg = 'Scripted response'
            return response

    class NoCleartext(HTTPHandler):
        def http_open(self, request):
            pytest.fail(f'Unexpected cleartext transport for {request.host}')

    def opener(*handlers):
        proxy, = (handler for handler in handlers if isinstance(handler, ProxyHandler))
        assert proxy.proxies == {}
        return build_opener(*handlers, NoCleartext(), Transport())

    monkeypatch.setattr(optional.urllib.request, 'build_opener', opener)
    return contacts, bodies


@pytest.mark.parametrize('foreign', [
    'https://files.pythonhosted.org/fixture/undisclosed',
    'http://cdn-lfs.huggingface.co/fixture/cleartext',
])
def test_targeted_install_refuses_unauthorized_redirect_before_contact_and_retry_uses_cdn(
        payloads, monkeypatch, foreign):
    p = payloads
    runtime_url, model_url = list(p.objects)[:2]
    cdn_url = 'https://cdn-lfs.huggingface.co/fixture/whisper'
    plan = p.acquisition.prepare('whisper-base', False, control(), ignore_progress)
    assert plan.publishers == ('Hugging Face',)
    assert {asset.url for asset in plan.downloads} == {runtime_url, model_url}

    def refuse_target(url):
        if url == model_url:
            return 302, {'Location': foreign}, b'', None
        raw = p.objects[url]
        return 200, {'Content-Length': len(raw)}, raw, None

    contacts, bodies = scripted_https(monkeypatch, refuse_target)
    with pytest.raises(ModelError, match='authorized publisher') as failure:
        p.acquisition.dispatch(plan, control(), ignore_progress)
    assert failure.value.code == 'source_refused'
    assert contacts == [runtime_url, model_url] and all(body.closed for body in bodies)
    assert not p.gate.busy and not p.gate.failure
    assert [r['state'] for r in p.profiles[0].inventory()][:2] == ['verified_unqualified', 'missing']

    retry = p.acquisition.prepare('whisper-base', False, control(), ignore_progress)
    assert retry.publishers == ('Hugging Face',)
    assert [asset.url for asset in retry.downloads] == [model_url]

    def follow_cdn(url):
        if url == model_url:
            return 302, {'Location': cdn_url}, b'', None
        assert url == cdn_url
        raw = p.objects[model_url]
        return 200, {'Content-Length': len(raw)}, raw, None

    contacts, bodies = scripted_https(monkeypatch, follow_cdn)
    assert p.acquisition.dispatch(retry, control(), ignore_progress) is None
    assert contacts == [model_url, cdn_url] and all(body.closed for body in bodies)
    assert p.profiles[0].inventory()[1]['state'] == 'verified_unqualified'
    assert not p.gate.busy


@pytest.mark.parametrize(('host', 'redirect_host'), [
    ('huggingface.co', 'huggingface.co'),
    ('huggingface.co', 'cdn-lfs-us-1.huggingface.co'),
    ('huggingface.co', 'cas-bridge.xethub.hf.co'),
    ('huggingface.co', 'us.aws.cdn.hf.co'),
    ('download-r2.pytorch.org', 'download.pytorch.org'),
    ('argos-net.com', 'data.argosopentech.com'),
])
def test_authorized_redirects_retain_exact_byte_pins(payloads, monkeypatch, host, redirect_host):
    start, target = f'https://{host}/start', f'https://{redirect_host}/object'
    raw = b'pinned fixture'
    asset = dict(url=start, size=len(raw), sha256=pin(raw))

    def respond(url):
        if url == start:
            return 302, {'Location': target}, b'', None
        assert url == target
        return 200, {'Content-Length': len(raw)}, raw, None

    contacts, bodies = scripted_https(monkeypatch, respond)
    path = optional.download(payloads.paths, asset, control(), ignore_progress,
                             authority=optional.download_authority(asset))
    assert path.read_bytes() == raw
    assert contacts == [start, target] and all(body.closed for body in bodies)


@pytest.mark.parametrize('final_url', [
    'https://files.pythonhosted.org/object',
    'http://huggingface.co/object',
    'https://huggingface.co:444/object',
])
def test_final_response_cannot_escape_download_authority(payloads, monkeypatch, final_url):
    start = 'https://huggingface.co/start'
    raw = b'pinned fixture'
    asset = dict(url=start, size=len(raw), sha256=pin(raw))
    contacts, bodies = scripted_https(monkeypatch, lambda _: (200, {}, raw, final_url))
    with pytest.raises(ModelError, match='Unexpected optional response'):
        optional.download(payloads.paths, asset, control(), ignore_progress,
                          authority=optional.download_authority(asset))
    assert contacts == [start] and all(body.closed for body in bodies)
    assert not any(payloads.paths.downloads.iterdir())


@pytest.mark.parametrize(('advertised_length', 'payload', 'message'), [
    (5, b'abcd', 'response length differs'),
    (None, b'abc', 'Incomplete model member'),
    (None, b'abcde', 'size/hash mismatch'),
    (4, b'xxxx', 'size/hash mismatch'),
])
def test_authorized_download_still_enforces_size_and_hash(payloads, monkeypatch, advertised_length, payload, message):
    start = 'https://huggingface.co/start'
    asset = dict(url=start, size=4, sha256=pin(b'abcd'))
    fields = {'Content-Length': advertised_length} if advertised_length is not None else {}
    contacts, bodies = scripted_https(monkeypatch, lambda _: (200, fields, payload, None))
    with pytest.raises(ModelError, match=message):
        optional.download(payloads.paths, asset, control(), ignore_progress,
                          authority=optional.download_authority(asset))
    assert contacts == [start] and all(body.closed for body in bodies)
    assert not any(payloads.paths.downloads.iterdir())


def test_same_publisher_url_and_endpoint_changes_require_renewed_consent(payloads, monkeypatch):
    p = payloads
    plan = p.acquisition.prepare('whisper-base', False, control(), ignore_progress)
    asset = p.profiles[0].profile.data['models'][0]['members'][0]
    asset['url'] += '-new'
    renewed = p.acquisition.dispatch(plan, control(), ignore_progress)
    assert renewed.publishers == plan.publishers and renewed.network_bytes == plan.network_bytes
    assert renewed.downloads != plan.downloads and not p.calls

    origins, cdns = optional.PUBLISHER_ENDPOINTS['Hugging Face']
    monkeypatch.setitem(optional.PUBLISHER_ENDPOINTS, 'Hugging Face',
                        (origins, cdns - {'us.aws.cdn.hf.co'}))
    scope_changed = p.acquisition.dispatch(renewed, control(), ignore_progress)
    assert scope_changed.publishers == renewed.publishers and scope_changed.downloads != renewed.downloads
    assert not p.calls and not any(p.paths.models.iterdir()) and not p.gate.busy


def test_disclosed_second_publisher_does_not_authorize_redirect_for_other_asset(payloads, monkeypatch):
    p = payloads
    wheel = p.profiles[0].profile.data['assets'][0]
    wheel['url'] = wheel['url'].replace('huggingface.co', 'files.pythonhosted.org')
    plan = p.acquisition.prepare('whisper-base', False, control(), ignore_progress)
    assert plan.publishers == ('Hugging Face', 'PyPI')
    contacts, bodies = scripted_https(monkeypatch, lambda _: (
        302, {'Location': 'https://huggingface.co/fixture/foreign-for-pypi'}, b'', None))
    with pytest.raises(ModelError, match='authorized publisher'):
        p.acquisition.dispatch(plan, control(), ignore_progress)
    assert contacts == [wheel['url']] and all(body.closed for body in bodies)
    assert not p.gate.busy


def test_cancelled_redirect_closes_response_before_following(payloads, monkeypatch):
    p = payloads
    cancelled = control()
    plan = p.acquisition.prepare('whisper-base', False, control(), ignore_progress)

    def respond(_):
        cancelled.cancel()
        return 302, {'Location': 'https://cdn-lfs.huggingface.co/fixture'}, b'', None

    contacts, bodies = scripted_https(monkeypatch, respond)
    with pytest.raises(ModelError, match='cancelled'):
        p.acquisition.dispatch(plan, cancelled, ignore_progress)
    assert contacts == [plan.downloads[0].url] and all(body.closed for body in bodies)
    assert not any(p.paths.models.iterdir()) and not list(p.paths.downloads.glob('asset-*'))
    assert not p.gate.busy and not p.gate.failure


def test_cold_plan_consent_change_install_verify_and_portable_move(payloads, tmp_path):
    acquire = payloads.acquisition
    before = [row for profile in payloads.profiles for row in profile.inventory()]
    assert [row['state'] for row in before] == ['missing'] * 6
    plan = acquire.prepare('', False, control(), ignore_progress)
    assert len(plan.components) == 6 and not plan.blocked_reason
    assert plan.publishers == ('Hugging Face',)
    assert plan.network_bytes == sum(map(len, payloads.objects.values()))
    assert not payloads.calls

    asset = payloads.profiles[0].profile.data['assets'][0]
    cache = payloads.paths.downloads / ('asset-' + asset['sha256'])
    cache.write_bytes(payloads.objects[asset['url']])
    renewed = acquire.dispatch(plan, control(), ignore_progress)
    assert not payloads.calls and renewed.network_bytes == plan.network_bytes - asset['size']
    assert not any(payloads.paths.models.iterdir())
    progress = []
    assert acquire.dispatch(renewed, control(), lambda *event: progress.append(event)) is None
    members = [event for event in progress if event[0] == 'install' and event[3] > 0]
    assert members
    for (_, _, total), advances in groupby(members, key=lambda event: (event[1], event[4], event[3])):
        counts = [event[2] for event in advances]
        assert counts[0] == 0 and counts[-1] > 0 and sum(counts) == total
    assert len(payloads.calls) == 5 and not payloads.gate.busy
    for profile in payloads.profiles:
        profile.verify(control(), complete_inventory=True)
        assert {row['state'] for row in profile.inventory()} == {'verified_unqualified'}
    assert len(payloads.calls) == 5

    PortablePreferences(payloads.paths).save(dict(dub_voice='aiden', dub_audio_format='wav'))
    ThemePreference(payloads.paths).save(dict(theme='Light'))
    old = payloads.paths.root.parent.parent
    moved = tmp_path / 'New location Кириллица'
    old.rename(moved)
    new_paths = ProfilePaths.for_test(moved / 'Data/v2')
    assert ThemePreference(new_paths).load() == {'theme': 'Light'}
    assert PortablePreferences(new_paths).load()['dub_audio_format'] == 'wav'
    for old_profile in payloads.profiles:
        new_profile = optional.OptionalResources(new_paths, payloads.gate, profile=old_profile.profile)
        new_profile.verify(control(), complete_inventory=True)
        assert {row['state'] for row in new_profile.inventory()} == {'verified_unqualified'}
    snapshot = measure_storage(new_paths)
    assert snapshot.models.state == snapshot.cache.state == 'complete'
    assert snapshot.models.bytes and snapshot.cache.bytes


def test_manifest_only_repair_is_offline_and_keeps_payload(payloads):
    acquire = payloads.acquisition
    profile = payloads.profiles[0]
    spec = profile.profile.runtime
    path = profile.store.location(spec)
    path.mkdir(parents=True)
    member = path / 'package.py'
    member.write_bytes(spec.model_id.encode())
    manifest = profile.companion(spec)
    manifest.write_bytes(b'bad')
    before = member.stat()
    profile.verify(control(), complete_inventory=True)
    assert profile.inventory()[0]['state'] == 'corrupt'
    plan = acquire.prepare(spec.model_id, True, control(), ignore_progress)
    assert not plan.blocked_reason and plan.network_bytes == 0 and plan.publishers == ()
    step, = plan.components
    assert step.action == 'reuse' and step.companion_action == 'replace'
    assert acquire.dispatch(plan, control(), ignore_progress) is None
    assert manifest.read_bytes() == (payloads.registries / manifest.name).read_bytes()
    assert member.stat().st_ino == before.st_ino and member.read_bytes() == spec.model_id.encode()
    assert not payloads.calls


def test_cancel_during_transfer_retries_from_pins_and_preserves_prior_components(payloads):
    acquire = payloads.acquisition
    plan = acquire.prepare('', False, control(), ignore_progress)
    cancelled = control()
    def interrupt_third_request():
        if len(payloads.calls) == 3:
            cancelled.cancel()
    payloads.on_open = interrupt_third_request
    with pytest.raises(ModelError, match='cancelled'):
        acquire.dispatch(plan, cancelled, ignore_progress)
    assert not payloads.gate.busy and len(payloads.calls) == 3
    profile = payloads.profiles[0]
    assert [r['state'] for r in profile.inventory()] == ['verified_unqualified', 'verified_unqualified', 'missing']
    complete = acquire.prepare('', False, control(), ignore_progress)
    assert complete.network_bytes < plan.network_bytes
    assert acquire.dispatch(complete, control(), ignore_progress) is None


def test_pending_absent_payload_and_foreign_entries_are_never_adopted(payloads):
    acquire = payloads.acquisition
    profile = payloads.profiles[0]
    spec = profile.profile.runtime
    marker = pending_path(profile.store, spec)
    marker.write_bytes(b'')
    assert profile.inventory()[0]['state'] == 'incomplete'
    assert acquire.prepare('', False, control(), ignore_progress).blocked_reason
    repair = acquire.prepare(spec.model_id, True, control(), ignore_progress)
    marker.write_bytes(b'foreign')
    with pytest.raises(ModelError, match='nonempty'):
        acquire.dispatch(repair, control(), ignore_progress)
    assert marker.read_bytes() == b'foreign'
    assert not payloads.calls


def test_replacement_that_becomes_healthy_requires_new_consent(payloads):
    acquire = payloads.acquisition
    profile = payloads.profiles[0]
    spec = profile.profile.runtime
    path = profile.store.location(spec)
    path.mkdir(parents=True)
    file = path / 'package.py'
    file.write_bytes(b'damaged')
    before = acquire.prepare(spec.model_id, True, control(), ignore_progress)
    assert before.components[0].action == 'replace'
    file.write_bytes(spec.model_id.encode())
    changed = acquire.dispatch(before, control(), ignore_progress)
    assert changed.components[0].action == 'reuse'
    assert not payloads.calls
    with closing(profile.store.verify(spec)):
        pass
