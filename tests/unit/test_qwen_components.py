"""Real harmless tiny files exercise the adapted two-component auth barrier."""
import hashlib
import importlib
import json
from pathlib import Path
import sys
import types

import pytest

PATCH = Path(__file__).resolve().parents[2]/'src/mediaforge/resources/dub/patches/qwen-tts/qwen_tts'


@pytest.fixture
def component_module(monkeypatch):
    package = types.ModuleType('_mf_qwen_components_test')
    package.__path__ = [str(PATCH)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    # Register teardown for every isolated dependency loaded below, without
    # importing the ordinary Qwen package (which would import Torch).
    names = ['mf_contracts','mf_constants','mf_native_files','mf_components']
    for name in names:
        full = package.__name__+'.'+name
        monkeypatch.setitem(sys.modules, full, None)
        del sys.modules[full]
    return importlib.import_module(package.__name__+'.mf_components')


def components(module, tmp_path, monkeypatch, *, invalid_selector=False):
    from_module = importlib.import_module(module.__package__+'.mf_contracts')
    revision = from_module.REVISION
    talker = tmp_path/'models/qwen3-tts-06b-customvoice'/revision
    tokenizer = tmp_path/'models/qwen3-tts-tokenizer-12hz'/revision
    talker.mkdir(parents=True); tokenizer.mkdir(parents=True)
    config = dict(model_type='qwen3_tts',tts_model_type='custom_voice',tts_model_size='0b6',
                  tokenizer_type='qwen3_tts_tokenizer_12hz',
                  talker_config=dict(spk_id=from_module.SPEAKERS,codec_language_id=dict(russian=2069)))
    if invalid_selector:
        config['auto_map'] = {'AutoModel':'unavailable_test_placeholder'}
    documents = {'talker': {'config.json':config,'vocab.json':{'implementation':1,'hello':2}},
                 'tokenizer': {'config.json':dict(model_type='qwen3_tts_tokenizer_12hz',output_sample_rate=24000)}}
    models = {}
    for label, folder in (('talker',talker),('tokenizer',tokenizer)):
        rows = []
        for name, value in documents[label].items():
            raw = json.dumps(value).encode()
            (folder/name).write_bytes(raw)
            rows.append(dict(name=name,size=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
        models[label] = rows
    monkeypatch.setattr(module,'MODELS',models)
    return talker,tokenizer


@pytest.mark.native
def test_vocabulary_is_authenticated_data_and_not_a_configuration_selector(component_module,tmp_path,monkeypatch):
    module = component_module
    talker,tokenizer = components(module,tmp_path,monkeypatch)
    assert 'torch' not in sys.modules
    owner = module.Components(tmp_path,talker,tokenizer)
    try:
        assert 'talker/config.json' in owner.documents
        assert 'talker/vocab.json' not in owner.documents
        with pytest.raises(PermissionError):
            (talker/'vocab.json').write_text('{}')
    finally:
        owner.close()


@pytest.mark.native
@pytest.mark.parametrize('mode',['missing','corrupt'])
def test_second_component_refuses_before_either_config_parse(component_module,tmp_path,monkeypatch,mode):
    module = component_module
    talker,tokenizer = components(module,tmp_path,monkeypatch)
    if mode == 'missing':
        (tokenizer/'config.json').unlink()
    else:
        (tokenizer/'config.json').write_text('{}')
    parses = []; real = module.json.loads
    monkeypatch.setattr(module.json,'loads',lambda raw:(parses.append(True),real(raw))[1])
    with pytest.raises(module.ProfileError):
        module.Components(tmp_path,talker,tokenizer)
    assert parses == []
    # The failed barrier also releases its first-component file ownership.
    (talker/'vocab.json').write_text('{}')


@pytest.mark.native
def test_executable_configuration_selector_still_refused(component_module,tmp_path,monkeypatch):
    module = component_module
    talker,tokenizer = components(module,tmp_path,monkeypatch,invalid_selector=True)
    with pytest.raises(module.ProfileError,match='selector'):
        module.Components(tmp_path,talker,tokenizer)
