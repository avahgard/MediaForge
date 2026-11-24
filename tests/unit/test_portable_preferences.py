import json
import pytest

from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.settings.preferences import PortablePreferences


@pytest.mark.native
def test_selected_voice_and_format_persist_only_inside_movable_portable_root(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'Portable Кириллица'/'Data/v2');paths.prepare()
    store=PortablePreferences(paths)
    assert store.load()==dict(dub_voice='aiden',dub_audio_format='mp3')
    store.save(dict(dub_voice='serena',dub_audio_format='wav'))
    store.save(dict(dub_voice='aiden',dub_audio_format='wav'))
    assert store.load()==dict(dub_voice='aiden',dub_audio_format='wav')
    assert not list(paths.config.glob('.settings-prior-*'))
    moved=tmp_path/'Moved portable';paths.root.parent.parent.rename(moved)
    other=PortablePreferences(ProfilePaths.for_test(moved/'Data/v2'))
    assert other.load()==dict(dub_voice='aiden',dub_audio_format='wav')


@pytest.mark.native
@pytest.mark.parametrize('content',[b'corrupt',{ 'schema':2,'dub_voice':'aiden','dub_audio_format':'mp3'},
                                    {'schema':1,'dub_voice':{},'dub_audio_format':'mp3'}])
def test_invalid_existing_settings_are_preserved_and_never_executed(tmp_path,content):
    paths=ProfilePaths.for_test(tmp_path/'root');paths.prepare();store=PortablePreferences(paths)
    raw=content if isinstance(content,bytes) else json.dumps(content).encode()
    store.path.write_bytes(raw)
    with pytest.raises((ValueError,TypeError)):store.save(dict(dub_voice='serena',dub_audio_format='mp3'))
    assert store.path.read_bytes()==raw


@pytest.mark.native
def test_unknown_interrupted_backups_are_bounded_and_never_adopted(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'root');paths.prepare();store=PortablePreferences(paths)
    for name in ('first','second'):(paths.config/('.settings-prior-'+name)).write_bytes(b'preserve')
    with pytest.raises(ValueError,match='Prior interrupted'):store.save(dict(dub_voice='serena',dub_audio_format='mp3'))
    assert not store.path.exists()
