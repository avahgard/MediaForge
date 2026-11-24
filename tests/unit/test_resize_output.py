from pathlib import Path

import pytest

from mediaforge.features.media.resize import ResizeControl, ResizeError, ResizeErrorCode
from mediaforge.infrastructure.filesystem.resize_output import ResizeOutput


@pytest.mark.native
@pytest.mark.parametrize("name", ["video.mp4", "video.MOV", "видео файл.mkv"])
def test_resize_naming_collision_and_unicode(tmp_path, name):
    source = tmp_path / name; source.write_bytes(b"source")
    existing = source.with_name(f"{source.stem}_resize{source.suffix}"); existing.write_bytes(b"keep")
    with ResizeOutput(source) as output:
        output.staged.write_bytes(b"new")
        result = output.publish(ResizeControl())
    assert result.name == f"{source.stem}_resize_001{source.suffix}"
    assert existing.read_bytes() == b"keep" and result.read_bytes() == b"new"


def test_cancelled_publication_never_publishes(tmp_path):
    source = tmp_path / "video.mp4"; source.write_bytes(b"source"); control = ResizeControl()
    with ResizeOutput(source) as output:
        output.staged.write_bytes(b"new"); control.cancel()
        with pytest.raises(ResizeError) as caught: output.publish(control)
    assert caught.value.code == ResizeErrorCode.CANCELLED
    assert not (tmp_path / "video_resize.mp4").exists()
