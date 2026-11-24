import threading

import pytest

from mediaforge.features.media.trim import TrimControl, TrimError
from mediaforge.infrastructure.filesystem import trim_output


@pytest.mark.parametrize("folder,name", [("normal", "video.mp4"), ("with spaces", "my video.mov"), ("Кириллица 🎬", "видео.mkv")])
@pytest.mark.parametrize("collisions", [
    0, pytest.param(1, marks=pytest.mark.native), pytest.param(3, marks=pytest.mark.native),
])
def test_naming_and_preservation(tmp_path, folder, name, collisions):
    source = tmp_path / folder / name
    source.parent.mkdir()
    source.write_bytes(b"SOURCE")
    existing = []
    for i in range(collisions):
        p = source.with_name(source.stem + "_trim" + (f"_{i:03d}" if i else "") + source.suffix)
        p.write_bytes(b"USER")
        existing.append(p)
    with trim_output.TrimOutput(source) as output:
        output.staged.write_bytes(b"TRIM")
        final = output.publish(TrimControl())
    assert final.name == source.stem + "_trim" + (f"_{collisions:03d}" if collisions else "") + source.suffix
    assert final.read_bytes() == b"TRIM"
    assert source.read_bytes() == b"SOURCE"
    assert all(p.read_bytes() == b"USER" for p in existing)
    assert not list(source.parent.glob(".mediaforge-trim-*"))


@pytest.mark.native
def test_race_at_atomic_publication(tmp_path, monkeypatch):
    source = tmp_path / "video.mp4"
    real_publish = trim_output.publish_new
    calls = []

    def racing(staged, target):
        calls.append(target)
        if len(calls) == 1:
            target.write_bytes(b"CONCURRENT USER")
        return real_publish(staged, target)

    monkeypatch.setattr(trim_output, "publish_new", racing)
    with trim_output.TrimOutput(source) as output:
        output.staged.write_bytes(b"TRIM")
        final = output.publish(TrimControl())
    assert final.name == "video_trim_001.mp4"
    assert calls[0].read_bytes() == b"CONCURRENT USER"


def test_cancel_before_commit_and_after_commit(tmp_path):
    source = tmp_path / "video.mp4"
    control = TrimControl()
    with trim_output.TrimOutput(source) as output:
        output.staged.write_bytes(b"TRIM")
        assert control.cancel()
        with pytest.raises(TrimError):
            output.publish(control)
    assert list(tmp_path.iterdir()) == []
    control = TrimControl()
    with trim_output.TrimOutput(source) as output:
        output.staged.write_bytes(b"TRIM")
        result = output.publish(control)
        assert not control.cancel()
    assert result.read_bytes() == b"TRIM"


def test_cancel_during_collision_prevents_retry(tmp_path, monkeypatch):
    control = TrimControl()
    def collision(staged, target):
        # Request cancel from another thread after this atomic attempt releases
        # the publication gate; deterministic synchronization through the error.
        raise FileExistsError()
    monkeypatch.setattr(trim_output, "publish_new", collision)
    original_check = control.check
    calls = []
    def check():
        calls.append(1)
        if len(calls) == 2:
            control.cancel()
        original_check()
    monkeypatch.setattr(control, "check", check)
    with trim_output.TrimOutput(tmp_path / "video.mp4") as output:
        output.staged.write_bytes(b"TRIM")
        with pytest.raises(TrimError):
            output.publish(control)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("phase", ["staging", "publication"])
def test_filesystem_errors_remain_controlled(tmp_path, monkeypatch, phase):
    def denied(*args, **kwargs):
        raise PermissionError("private filesystem detail")
    if phase == "staging":
        monkeypatch.setattr(trim_output.tempfile, "mkdtemp", denied)
    else:
        monkeypatch.setattr(trim_output, "publish_new", denied)
    with pytest.raises(TrimError) as error:
        with trim_output.TrimOutput(tmp_path / "video.mp4") as output:
            output.staged.write_bytes(b"TRIM")
            output.publish(TrimControl())
    assert error.value.code.value == ("output_unavailable" if phase == "staging" else "publication_failed")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.native
def test_real_concurrent_publishers_never_replace(tmp_path):
    source = tmp_path / "video.mp4"
    barrier = threading.Barrier(2)
    results, errors = [], []
    def worker(data):
        try:
            with trim_output.TrimOutput(source) as output:
                output.staged.write_bytes(data)
                barrier.wait(2)
                results.append(output.publish(TrimControl()))
        except Exception as error:
            errors.append(error)
    workers = [threading.Thread(target=worker, args=(data,)) for data in (b"FIRST", b"SECOND")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(3)
    assert not errors and all(not w.is_alive() for w in workers)
    assert {p.name for p in results} == {"video_trim.mp4", "video_trim_001.mp4"}
    assert {p.read_bytes() for p in results} == {b"FIRST", b"SECOND"}


def test_cancel_cannot_relabel_committed_output(tmp_path):
    control = TrimControl()
    entered, release = threading.Event(), threading.Event()
    outcomes = []
    def publish():
        def atomic_action():
            entered.set()
            assert release.wait(2)
            return tmp_path / "committed.mp4"
        control.publish(atomic_action)
    publisher = threading.Thread(target=publish)
    publisher.start()
    assert entered.wait(1)
    canceller = threading.Thread(target=lambda: outcomes.append(control.cancel()))
    canceller.start()
    release.set()
    publisher.join(2)
    canceller.join(2)
    assert outcomes == [False]
