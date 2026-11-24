from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.state.workspace import MediaWorkspaceSession
from mediaforge.presentation.bridge.crop_geometry import Rect, drag_rect, pointer_to_coded
import pytest


def metadata(path: str, width: int = 1921, height: int = 1081):
    return MediaMetadataDto(path.rsplit("/", 1)[-1], path, "mp4", 10.0, "h264",
                            width, height, 25.0)


def test_items_keep_independent_settings_and_canonical_add(tmp_path):
    first = tmp_path / "space кириллица.mp4"
    second = tmp_path / "portrait.mp4"
    session = MediaWorkspaceSession()
    a, added = session.add(metadata(str(first)))
    assert session.set_enabled("trim", True)
    assert added and session.set_trim(1000, 8000)
    assert session.set_crop(20, 22, 500, 400)
    assert session.set_resize(1280, 720)
    b, added = session.add(metadata(str(second), 1081, 1921))
    assert added and b.id != a.id and b.trim.in_ms == 0
    assert b.crop.width == 1080 and b.crop.height == 1920
    session.select(a.id)
    assert (session.active.trim.in_ms, session.active.crop.x,
            session.active.resize.width, session.active.trim.enabled) == (1000, 20, 1280, True)
    again, added = session.add(metadata(str(first.parent / "." / first.name)))
    assert not added and again.id == a.id and len(session.items) == 2


def test_duplicate_reorder_remove_never_touch_source(tmp_path):
    path = tmp_path / "source.mp4"
    path.write_bytes(b"keep")
    session = MediaWorkspaceSession()
    a, _ = session.add(metadata(str(path)))
    assert session.set_crop(10, 12, 100, 100)
    duplicate = session.duplicate(a.id)
    assert duplicate.id != a.id and duplicate.crop.x == 10
    assert duplicate.video is not a.video
    duplicate.video.position_ms=1540
    assert a.video.position_ms==0 and not hasattr(a.video,'quality')
    assert not session.move(a.id, -1)
    assert session.move(a.id, 1)
    session.remove(a.id)
    assert session.active.id == duplicate.id and path.read_bytes() == b"keep"
    session.remove(duplicate.id)
    assert session.active is None and path.exists()


def test_strict_crop_and_trim_validation(tmp_path):
    session = MediaWorkspaceSession()
    session.add(metadata(str(tmp_path / "a.mp4")))
    assert not session.set_crop(1, 0, 100, 100)
    assert not session.set_crop(0, 0, 15, 100)
    assert not session.set_crop(1900, 0, 100, 100)
    assert session.active.crop.x == 0
    assert not session.set_trim(9000, 8000)
    assert session.active.trim.in_ms == 0


@pytest.mark.parametrize("size", [(1920, 1080), (1921, 1081)])
def test_reset_crop_exact_source_disabled_and_independent(tmp_path, size):
    from dataclasses import asdict
    s = MediaWorkspaceSession()
    assert not s.reset_crop()
    path = tmp_path / "reset source Кириллица.mp4"
    path.write_bytes(b"unchanged")
    item, _ = s.add(metadata(str(path), *size))
    s.set_enabled("trim", True); assert s.set_trim(100, 8000)
    assert s.set_resize(1280, 720)
    s.set_enabled("resize", True)
    assert s.set_crop(20, 22, 500, 400)
    item.crop.enabled = item.crop.preview = True
    other = s.duplicate(item.id)
    saved = asdict(item.trim), asdict(item.resize), s.active_id
    assert s.reset_crop()
    assert asdict(item.crop) == dict(enabled=False, x=0, y=0, width=size[0], height=size[1], preview=False)
    assert (asdict(item.trim), asdict(item.resize), s.active_id) == saved
    assert other.crop.enabled and other.crop.preview and other.crop.x == 20
    assert path.read_bytes() == b"unchanged"
    assert s.set_enabled("crop", True)
    assert s.set_crop(0, 0, size[0]//2*2, size[1]//2*2)


def test_content_rect_mapping_and_even_drag():
    assert pointer_to_coded(99, 100, 100, 50, 800, 450, 1920, 1080) is None
    assert pointer_to_coded(500, 275, 100, 50, 800, 450, 1920, 1080) == (960, 540)
    assert pointer_to_coded(500, 275, 400, 50, 200, 450, 1080, 1920) == (540, 960)
    start = Rect(100, 100, 400, 300)
    assert drag_rect(start, "move", 3, 5, 1921, 1081) == Rect(104, 104, 400, 300)
    assert drag_rect(start, "left", 3, 0, 1921, 1081) == Rect(104, 100, 396, 300)
    assert drag_rect(start, "right-bottom", 3, 5, 1921, 1081) == Rect(100, 100, 404, 304)
    assert drag_rect(start, "left-top", 999, 999, 1921, 1081) == Rect(484, 384, 16, 16)


@pytest.mark.parametrize("mode", ["move", "left", "right", "top", "bottom",
                                  "left-top", "right-top", "left-bottom", "right-bottom"])
@pytest.mark.parametrize("delta", [-10000, 3, 10000])
def test_all_crop_gestures_keep_even_bounds_minimum_and_opposite_anchor(mode, delta):
    start = Rect(100, 100, 400, 300)
    rect = drag_rect(start, mode, delta, delta, 1921, 1081)
    assert all(value % 2 == 0 for value in (rect.x, rect.y, rect.width, rect.height))
    assert rect.x >= 0 and rect.y >= 0 and rect.width >= 16 and rect.height >= 16
    assert rect.x + rect.width <= 1921 and rect.y + rect.height <= 1081
    if mode == "move":
        assert (rect.width, rect.height) == (400, 300)
    if "left" in mode:
        assert rect.x + rect.width == start.x + start.width
    if "right" in mode:
        assert rect.x == start.x
    if "top" in mode:
        assert rect.y + rect.height == start.y + start.height
    if "bottom" in mode:
        assert rect.y == start.y


@pytest.mark.parametrize("size", [(16, 16), (7680, 4320), (1024, 768)])
def test_resize_accepts_existing_exact_resize_policy(tmp_path, size):
    session = MediaWorkspaceSession()
    session.add(metadata(str(tmp_path / "source.mp4")))
    assert session.set_resize(*size)


@pytest.mark.parametrize("size", [(15, 16), (17, 16), (16, 17), (7682, 16),
                                  (7680, 7680), (16.0, 16), (True, 16)])
def test_resize_rejects_without_mutating_valid_state(tmp_path, size):
    session = MediaWorkspaceSession()
    session.add(metadata(str(tmp_path / "source.mp4")))
    before = (session.active.resize.width, session.active.resize.height)
    assert not session.set_resize(*size)
    assert (session.active.resize.width, session.active.resize.height) == before


def test_duplicate_edit_objects_are_independent_and_all_states_restore(tmp_path):
    session = MediaWorkspaceSession()
    original, _ = session.add(metadata(str(tmp_path / "a.mp4")))
    for name in ("trim", "crop", "resize"):
        assert session.set_enabled(name, True)
    original.crop.preview = True
    duplicate = session.duplicate(original.id)
    session.select(duplicate.id)
    session.set_enabled("crop", False)
    assert session.set_crop(2, 4, 300, 200)
    assert session.set_trim(200, 500)
    assert session.set_resize(640, 360)
    assert original.crop.enabled and original.crop.x == 0
    assert original.trim.in_ms == 0 and original.resize.width == 1921
    assert duplicate.crop.preview and duplicate.trim.enabled and duplicate.resize.enabled
