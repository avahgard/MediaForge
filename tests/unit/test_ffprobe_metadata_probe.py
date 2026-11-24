import json
import subprocess

import pytest

from mediaforge.features.media.metadata import MediaProbeError, MediaProbeErrorCode
from mediaforge.infrastructure.ffmpeg import FfprobeMediaMetadataProbe
from mediaforge.infrastructure.resources.paths import configured_ffprobe_path


def valid_payload():
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "mjpeg",
                "width": 600,
                "height": 600,
                "avg_frame_rate": "0/0",
                "disposition": {"attached_pic": 1},
            },
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "24000/1001",
                "r_frame_rate": "24/1",
                "duration": "12.5",
                "disposition": {"attached_pic": 0},
            },
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "12.500000",
        },
    }


@pytest.mark.parametrize("sar, expected", [("4:3", "4:3"), ("1:1", "1"), ("0:1", "1"), (None, "1")])
def test_source_sar_reaches_metadata_without_coded_dimension_changes(tmp_path, sar, expected):
    executable, source = make_files(tmp_path)
    data = valid_payload()
    data["streams"][1]["sample_aspect_ratio"] = sar
    probe = FfprobeMediaMetadataProbe(executable, process_factory=RecordingProcessFactory(process_for(data)))
    result = probe.probe(source)
    assert result.sample_aspect_ratio == expected
    assert (result.width, result.height) == (1920, 1080)


class FakeProcess:
    def __init__(self, returncode=0, stdout="", stderr="", communicate_error=None):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.communicate_error = communicate_error
        self.communicate_timeouts = []
        self.terminated = False
        self.killed = False

    def communicate(self, timeout=None):
        self.communicate_timeouts.append(timeout)
        if self.communicate_error is not None:
            raise self.communicate_error
        return self.stdout, self.stderr

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired(["ffprobe"], timeout)
        return self.returncode


class RecordingProcessFactory:
    def __init__(self, process=None, error=None):
        self.process = process
        self.error = error
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.error is not None:
            raise self.error
        return self.process


def make_files(tmp_path, source_name="Папка с пробелом/Видео файл.mp4"):
    executable = tmp_path / "tools" / "ffprobe.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"fake executable")
    source = tmp_path / source_name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"media source")
    return executable, source


def process_for(payload, returncode=0, stderr=""):
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return FakeProcess(returncode=returncode, stdout=stdout, stderr=stderr)


def test_argument_list_preserves_spaces_and_unicode_and_parses_fields(tmp_path):
    executable, source = make_files(tmp_path)
    process = process_for(valid_payload())
    factory = RecordingProcessFactory(process)
    probe = FfprobeMediaMetadataProbe(executable, process_factory=factory)

    metadata = probe.probe(source)

    command, kwargs = factory.calls[0]
    assert isinstance(command, list)
    assert command[-1] == str(source)
    assert command.count(str(source)) == 1
    assert kwargs["shell"] is False
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.PIPE
    assert process.communicate_timeouts == [15.0]
    assert metadata.source_name == "Видео файл.mp4"
    assert metadata.source_path == source
    assert metadata.video_codec == "h264"
    assert (metadata.width, metadata.height) == (1920, 1080)
    assert metadata.duration_seconds == 12.5
    assert metadata.frame_rate == pytest.approx(24000 / 1001)


def test_frame_rate_falls_back_without_inventing_default(tmp_path):
    executable, source = make_files(tmp_path, "source.mp4")
    payload = valid_payload()
    stream = payload["streams"][1]
    stream["avg_frame_rate"] = "0/0"
    stream["r_frame_rate"] = "invalid"
    stream["nb_frames"] = "300"
    stream["duration"] = "12.5"
    probe = FfprobeMediaMetadataProbe(
        executable,
        process_factory=RecordingProcessFactory(process_for(payload)),
    )
    assert probe.probe(source).frame_rate == 24

    del stream["nb_frames"]
    probe = FfprobeMediaMetadataProbe(
        executable,
        process_factory=RecordingProcessFactory(process_for(payload)),
    )
    assert probe.probe(source).frame_rate is None


@pytest.mark.parametrize(
    ("process", "expected_code"),
    [
        (process_for("", returncode=2, stderr="decoder failed"), MediaProbeErrorCode.PROCESS_FAILED),
        (process_for("not json"), MediaProbeErrorCode.INVALID_OUTPUT),
        (
            process_for({"streams": [], "format": {"format_name": "wav"}}),
            MediaProbeErrorCode.NO_VIDEO_STREAM,
        ),
    ],
)
def test_controlled_process_and_output_errors(tmp_path, process, expected_code):
    executable, source = make_files(tmp_path, "source.mp4")
    probe = FfprobeMediaMetadataProbe(
        executable,
        process_factory=RecordingProcessFactory(process),
    )

    with pytest.raises(MediaProbeError) as raised:
        probe.probe(source)

    assert raised.value.code is expected_code


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("duration", float("inf")),
        ("duration", float("-inf")),
        ("duration", float("nan")),
        ("width", float("inf")),
        ("width", float("-inf")),
        ("width", float("nan")),
        ("width", 10**1000),
        ("width", 1_000_001),
    ],
)
def test_non_finite_overflow_and_excessive_json_numbers_are_controlled(
    tmp_path, field, value
):
    executable, source = make_files(tmp_path, "source.mp4")
    payload = valid_payload()
    if field == "duration":
        payload["format"]["duration"] = value
    else:
        payload["streams"][1][field] = value
    probe = FfprobeMediaMetadataProbe(
        executable,
        process_factory=RecordingProcessFactory(process_for(payload)),
    )

    with pytest.raises(MediaProbeError) as raised:
        probe.probe(source)

    assert raised.value.code is MediaProbeErrorCode.INVALID_OUTPUT


def test_excessive_optional_duration_and_frame_rate_become_unavailable(tmp_path):
    executable, source = make_files(tmp_path, "source.mp4")
    payload = valid_payload()
    payload["format"]["duration"] = 1e300
    stream = payload["streams"][1]
    stream["duration"] = 1e300
    stream["avg_frame_rate"] = "1e100000/1"
    stream["r_frame_rate"] = "1e100000/1"
    probe = FfprobeMediaMetadataProbe(
        executable,
        process_factory=RecordingProcessFactory(process_for(payload)),
    )

    metadata = probe.probe(source)

    assert metadata.duration_seconds is None
    assert metadata.frame_rate is None


def test_timeout_and_unavailable_are_controlled(tmp_path):
    executable, source = make_files(tmp_path, "source.mp4")
    timeout = subprocess.TimeoutExpired([str(executable)], 15)
    process = FakeProcess(returncode=None, communicate_error=timeout)
    with pytest.raises(MediaProbeError) as raised:
        FfprobeMediaMetadataProbe(
            executable,
            process_factory=RecordingProcessFactory(process),
        ).probe(source)
    assert raised.value.code is MediaProbeErrorCode.TIMEOUT
    assert process.terminated

    with pytest.raises(MediaProbeError) as raised:
        FfprobeMediaMetadataProbe(None).probe(source)
    assert raised.value.code is MediaProbeErrorCode.UNAVAILABLE


def test_ffprobe_configuration_is_explicit_and_has_no_path_fallback(tmp_path):
    assert configured_ffprobe_path({"PATH": str(tmp_path)}) is None
    configured = tmp_path / "ffprobe.exe"
    assert configured_ffprobe_path(
        {"MEDIAFORGE_FFPROBE_PATH": str(configured), "PATH": "ignored"}
    ) == configured
    with pytest.raises(ValueError):
        configured_ffprobe_path({"MEDIAFORGE_FFPROBE_PATH": "ffprobe.exe"})
