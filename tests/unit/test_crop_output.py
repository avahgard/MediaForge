import threading

import pytest

from mediaforge.features.media.crop import CropControl, CropError
from mediaforge.infrastructure.filesystem.crop_output import CropOutput
from mediaforge.infrastructure.filesystem import staged_output


@pytest.mark.parametrize("name", ["video.mp4", "my video.MOV", "видео 🎬.mkv"])
@pytest.mark.parametrize("collisions", [
    0, pytest.param(1, marks=pytest.mark.native), pytest.param(2, marks=pytest.mark.native),
])
def test_crop_names_preserve_suffix_source_and_existing_files(tmp_path, name, collisions):
    source = tmp_path / name
    source.write_bytes(b"SOURCE")
    existing = []
    for index in range(collisions):
        path = source.with_name(source.stem + "_crop" + (f"_{index:03d}" if index else "") + source.suffix)
        path.write_bytes(b"USER")
        existing.append(path)
    with CropOutput(source) as output:
        assert output.staged.parent.parent == source.parent and output.staged.suffix == source.suffix
        output.staged.write_bytes(b"CROP")
        final = output.publish(CropControl())
    assert final.name == source.stem + "_crop" + (f"_{collisions:03d}" if collisions else "") + source.suffix
    assert final.read_bytes() == b"CROP" and source.read_bytes() == b"SOURCE"
    assert all(path.read_bytes() == b"USER" for path in existing)
    assert not list(tmp_path.glob(".mediaforge-*"))


@pytest.mark.native
def test_concurrent_crop_publication_never_overwrites(tmp_path):
    barrier = threading.Barrier(2)
    results, errors = [], []
    def publish(data):
        try:
            with CropOutput(tmp_path / "video.mp4") as output:
                output.staged.write_bytes(data)
                barrier.wait(2)
                results.append(output.publish(CropControl()))
        except Exception as error:
            errors.append(error)
    threads = [threading.Thread(target=publish, args=(data,)) for data in (b"FIRST", b"SECOND")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
    assert not errors and all(not thread.is_alive() for thread in threads)
    assert {path.name for path in results} == {"video_crop.mp4", "video_crop_001.mp4"}
    assert {path.read_bytes() for path in results} == {b"FIRST", b"SECOND"}


@pytest.mark.parametrize("phase", ["staging", "publication"])
def test_output_permission_failures(tmp_path, monkeypatch, phase):
    def denied(*args, **kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr(staged_output.tempfile if phase == "staging" else staged_output.os,
                        "mkdtemp" if phase == "staging" else "rename", denied)
    with pytest.raises(CropError) as error:
        with CropOutput(tmp_path / "source.mp4") as output:
            output.staged.write_bytes(b"CROP")
            output.publish(CropControl())
    assert error.value.code.value == ("output_unavailable" if phase == "staging" else "publication_failed")
    assert list(tmp_path.iterdir()) == []
