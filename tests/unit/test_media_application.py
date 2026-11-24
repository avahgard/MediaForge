from pathlib import Path

import pytest

from mediaforge.application.contracts.media import OpenMediaErrorCode
from mediaforge.application.services.media import OpenMediaService
from mediaforge.features.media.metadata import (
    MediaMetadata,
    MediaProbeError,
    MediaProbeErrorCode,
)


class FakeProbe:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.sources = []
        self.cancelled = False

    def probe(self, source: Path) -> MediaMetadata:
        self.sources.append(source)
        if self.error is not None:
            raise self.error
        return self.result

    def cancel(self):
        self.cancelled = True


def test_valid_path_and_fake_probe_return_stable_metadata(tmp_path):
    source = tmp_path / "Видео с пробелом.mp4"
    source.write_bytes(b"unchanged source")
    metadata = MediaMetadata(
        source_name=source.name,
        source_path=source,
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        duration_seconds=12.5,
        video_codec="h264",
        width=1920,
        height=1080,
        frame_rate=24000 / 1001,
    )
    probe = FakeProbe(result=metadata)

    result = OpenMediaService(probe).open_media(str(source))

    assert result.succeeded
    assert result.metadata is not None
    assert result.metadata.source_name == source.name
    assert result.metadata.source_path == str(source)
    assert result.metadata.frame_rate == pytest.approx(23.976, abs=0.001)
    assert probe.sources == [source]
    assert source.read_bytes() == b"unchanged source"


def test_missing_or_relative_source_returns_stable_error(tmp_path):
    probe = FakeProbe()
    service = OpenMediaService(probe)

    for source in (str(tmp_path / "missing.mp4"), "relative.mp4", ""):
        result = service.open_media(source)
        assert result.error_code is OpenMediaErrorCode.INVALID_SOURCE
        assert not result.succeeded
    assert probe.sources == []


@pytest.mark.parametrize(
    ("probe_code", "application_code"),
    [
        (MediaProbeErrorCode.UNAVAILABLE, OpenMediaErrorCode.FFPROBE_UNAVAILABLE),
        (MediaProbeErrorCode.PROCESS_FAILED, OpenMediaErrorCode.PROBE_FAILED),
        (MediaProbeErrorCode.INVALID_OUTPUT, OpenMediaErrorCode.INVALID_PROBE_OUTPUT),
        (MediaProbeErrorCode.NO_VIDEO_STREAM, OpenMediaErrorCode.NO_VIDEO_STREAM),
        (MediaProbeErrorCode.TIMEOUT, OpenMediaErrorCode.PROBE_TIMEOUT),
        (MediaProbeErrorCode.ABORTED, OpenMediaErrorCode.PROBE_ABORTED),
    ],
)
def test_infrastructure_failures_are_mapped_to_application_errors(
    tmp_path, probe_code, application_code
):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    service = OpenMediaService(
        FakeProbe(error=MediaProbeError(probe_code, "technical detail"))
    )

    result = service.open_media(str(source))

    assert result.error_code is application_code
    assert "technical detail" not in result.error_message
    assert not result.succeeded


@pytest.mark.integration
def test_application_and_feature_contracts_do_not_import_qt(run_python):
    result = run_python(
        "import sys; "
        "import mediaforge.application.contracts.media; "
        "import mediaforge.application.services.media; "
        "import mediaforge.features.media.metadata; "
        "assert 'PySide6' not in sys.modules"
    )
    assert result.returncode == 0, result.stderr


def test_cancel_is_forwarded_only_to_metadata_probe():
    probe = FakeProbe()

    OpenMediaService(probe).cancel()

    assert probe.cancelled
