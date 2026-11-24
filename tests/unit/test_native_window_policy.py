"""Geometry/selection regressions without a GUI or machine-specific topology."""
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QMargins, QRect, QSize

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_window_policy import (NativeScreenUnavailable, client_geometry,
                                  local_preference, select_screen)


class Screen:
    def __init__(self, name, geometry, dpr=1):
        self._name, self._geometry, self.dpr = name, QRect(*geometry), dpr

    def name(self):
        return self._name

    def availableGeometry(self):
        return self._geometry


def test_secondary_preference_and_deterministic_tie_break():
    primary = Screen("A", (0, 0, 1920, 1040))
    left = Screen("B", (-1920, -300, 1920, 1040))
    right = Screen("C", (1920, 0, 2560, 1400))
    for screens in ([right, primary, left], [left, right, primary]):
        assert select_screen(screens, primary, QSize(800, 560), QMargins()) is left
        assert select_screen(screens, primary, QSize(800, 560), QMargins(), "C") is right
        # Stale or primary preference cannot override the non-primary policy.
        assert select_screen(screens, primary, QSize(800, 560), QMargins(), "A") is left
        assert select_screen(screens, primary, QSize(800, 560), QMargins(), "missing") is left


def test_single_screen_and_small_secondary_fall_back_without_resizing():
    primary = Screen("primary", (0, 30, 1920, 1010))
    small = Screen("secondary", (-800, 0, 800, 600))
    for screens in ([primary], [small, primary]):
        assert select_screen(screens, primary, QSize(1440, 900), QMargins(8, 31, 8, 8)) is primary


@pytest.mark.parametrize("screens", [[], [Screen("small", (0, 0, 800, 560))]])
def test_no_fit_reports_explicit_skip(screens, capsys):
    with pytest.raises(NativeScreenUnavailable) as error:
        select_screen(screens, None, QSize(800, 560), QMargins(8, 31, 8, 8))
    assert error.value.code == 77
    assert "NATIVE SCREEN SKIP:" in capsys.readouterr().err


@pytest.mark.parametrize("dpr", [1, 1.25, 1.5, 2])
def test_negative_coordinates_taskbar_decorations_and_logical_dpi(dpr):
    screen = Screen("left", (-1920, -1080, 1536, 824), dpr)
    margins = QMargins(8, 31, 8, 8)
    size = QSize(800, 560)
    client = client_geometry(screen.availableGeometry(), size, margins)
    frame = client.marginsAdded(margins)
    assert screen.availableGeometry().contains(frame)
    assert client.size() == size
    assert frame == QRect(-1560, -968, 816, 599)


def test_disconnect_selection_uses_only_current_screens():
    primary = Screen("primary", (0, 0, 1920, 1040))
    secondary = Screen("secondary", (-1920, 0, 1920, 1040))
    assert select_screen([secondary, primary], primary, QSize(800, 560), QMargins()) is secondary
    assert select_screen([primary], primary, QSize(800, 560), QMargins(), "secondary") is primary


@pytest.mark.parametrize("contents, expected", [
    ('{"screen_name": "Монитор B"}', "Монитор B"), ('{}', None),
    ('{"screen_name": 42}', None), ('[]', None), ('broken', None)])
def test_optional_local_preference(tmp_path, contents, expected):
    path = tmp_path / "native-tests.json"
    assert local_preference(path) is None
    path.write_text(contents, encoding="utf-8")
    assert local_preference(path) == expected


def test_frame_too_large_is_not_clipped():
    with pytest.raises(NativeScreenUnavailable):
        client_geometry(QRect(0, 0, 800, 560), QSize(800, 560), QMargins(8, 31, 8, 8))


def test_geometry_breaks_duplicate_name_ties():
    left = Screen("same", (-1920, 0, 1920, 1040))
    right = Screen("same", (1920, 0, 1920, 1040))
    assert select_screen([right, left], None, QSize(800, 560), QMargins()) is left


@pytest.mark.parametrize("exit_code, diagnostics, expected", [
    (0, "success", None), (77, "NATIVE SCREEN SKIP: no screen fits", "skip"),
    (77, "other failure", "fail"), (1, "NATIVE SCREEN SKIP: no screen fits", "fail")])
def test_native_skip_requires_both_reserved_exit_and_diagnostic(exit_code, diagnostics, expected):
    from types import SimpleNamespace
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "presentation"))
    from test_workspace_native import require_native_success
    if expected is None:
        require_native_success(SimpleNamespace(returncode=exit_code), diagnostics)
    else:
        with pytest.raises(pytest.skip.Exception if expected == "skip" else AssertionError):
            require_native_success(SimpleNamespace(returncode=exit_code), diagnostics)
