from pathlib import Path

import pytest

from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.resources.paths import qml_root
from mediaforge.infrastructure.settings.location import settings_file


@pytest.mark.integration
def test_import_is_side_effect_free(run_python, isolated_environment):
    result = run_python("import sys; import mediaforge; assert 'PySide6' not in sys.modules")
    assert result.returncode == 0, result.stderr
    assert not Path(isolated_environment["LOCALAPPDATA"]).exists()
    assert not Path(isolated_environment["TEMP"]).exists()


def test_profile_layout_is_lazy_and_unicode_safe(isolated_environment):
    paths = ProfilePaths.from_environment(isolated_environment)
    assert paths.root == Path(isolated_environment['MEDIAFORGE_DATA_ROOT'])
    assert paths.temporary == paths.root / 'tmp'
    for name in ("config", "data", "cache", "logs", "models"):
        assert getattr(paths, name) == paths.root / name
    assert settings_file(paths) == paths.config / "settings.json"
    assert not paths.root.exists()


@pytest.mark.parametrize("environment", [{}, {"LOCALAPPDATA": "relative", "TEMP": "relative"}])
def test_invalid_environment_fails_without_fallback(environment):
    with pytest.raises(ValueError):
        ProfilePaths.from_environment(environment)


def test_resource_resolution_ignores_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert qml_root().is_absolute()
    assert qml_root().is_file()
