import json
from pathlib import Path

import pytest

from mediaforge.features.media.resize import ResizeControl, ResizeError, ResizeErrorCode
from mediaforge.infrastructure.ffmpeg.resize import FfmpegResizeExecutor
from mediaforge.infrastructure.ffmpeg.video_duration import first_video_duration
from tests.unit.resize_support import Factory, payload, setup


def error_code(call):
    with pytest.raises(ResizeError) as caught:
        call()
    return caught.value.code


def test_exact_resize_command_uses_required_spatial_timing_and_stream_policy(tmp_path):
    request, runtime = setup(tmp_path)
    command = FfmpegResizeExecutor(runtime).command(request, tmp_path / "partial.mp4")
    assert command[0] == str(runtime.ffmpeg)
    assert command[command.index("-vf") + 1] == "scale=320:180:flags=lanczos,setsar=1/1"
    for pair in [("-map", "0:v:0"), ("-map", "0:a?"), ("-c:v", "libx264"),
                 ("-preset", "medium"), ("-crf", "18"), ("-pix_fmt", "yuv420p"),
                 ("-c:a", "copy"), ("-fps_mode:v:0", "passthrough")]:
        assert any(command[index:index + 2] == list(pair) for index in range(len(command) - 1))
    assert "-noautorotate" in command and command[-1].endswith("partial.mp4")
    joined = " ".join(command)
    assert "crop=" not in joined and "pad=" not in joined and "fps=" not in joined
    assert "-r" not in command and "auto" not in command and "cfr" not in command


@pytest.mark.parametrize("value", [None, "", "0", "-1", "NaN", "Inf", "bad", True])
def test_video_duration_invalid_primary_without_valid_same_stream_tag_fails(value):
    video = {"duration": value} if value is not None else {}
    with pytest.raises((ValueError, TypeError)):
        first_video_duration(video)


@pytest.mark.parametrize("value", [None, "", "0", "NaN", "Inf", "bad"])
def test_video_duration_invalid_primary_uses_same_video_tag(value):
    video = {"duration": value, "tags": {"DURATION": "00:00:02.250"}}
    assert first_video_duration(video) == 2.25


def test_video_duration_primary_wins_over_tag():
    assert first_video_duration({"duration": "2.5", "tags": {"DURATION": "00:00:09.000"}}) == 2.5


@pytest.mark.parametrize("name,family", [("a.mp4", "mov,mp4"), ("a.mov", "mov,mp4"), ("a.mkv", "matroska,webm")])
def test_source_preflight_accepts_suffix_family_and_duration(tmp_path, name, family):
    request, runtime = setup(tmp_path, name)
    factory = Factory(source=payload(family=family))
    result = FfmpegResizeExecutor(runtime, process_factory=factory).execute(request, ResizeControl())
    assert result.width == 320 and result.height == 180 and result.output.is_file()


def test_source_duration_does_not_use_format_or_audio_rescue(tmp_path):
    request, runtime = setup(tmp_path)
    source = payload(duration=None)
    source["format"]["duration"] = "2.0"
    source["streams"][1]["duration"] = "2.0"
    assert error_code(lambda: FfmpegResizeExecutor(runtime, process_factory=Factory(source=source)).execute(
        request, ResizeControl())) == ResizeErrorCode.PREFLIGHT_FAILED


@pytest.mark.parametrize("source,code", [
    (payload(family="matroska"), ResizeErrorCode.UNSUPPORTED_CONTAINER),
    (payload(width=641), ResizeErrorCode.SOURCE_CHANGED),
])
def test_source_preflight_rejects_mismatch(tmp_path, source, code):
    request, runtime = setup(tmp_path)
    assert error_code(lambda: FfmpegResizeExecutor(runtime, process_factory=Factory(source=source)).execute(
        request, ResizeControl())) == code


def test_source_preflight_rejects_rotation_before_encode(tmp_path):
    request, runtime = setup(tmp_path)
    source = payload(tags={"rotate": "90"})
    factory = Factory(source=source)
    assert error_code(lambda: FfmpegResizeExecutor(runtime, process_factory=factory).execute(
        request, ResizeControl())) == ResizeErrorCode.UNSUPPORTED_ORIENTATION
    assert len(factory.calls) == 1


@pytest.mark.parametrize("output", [
    payload(322, 180), payload(320, 182), payload(320, 180, sar="4:3"),
    payload(320, 180, audio=0), payload(320, 180, duration="2.251"),
])
def test_staged_validation_rejects_wrong_geometry_sar_audio_or_duration(tmp_path, output):
    request, runtime = setup(tmp_path)
    assert error_code(lambda: FfmpegResizeExecutor(runtime, process_factory=Factory(output=output)).execute(
        request, ResizeControl())) == ResizeErrorCode.VALIDATION_FAILED


def test_staged_duration_tag_fallback_and_video_only_are_accepted(tmp_path):
    request, runtime = setup(tmp_path)
    source = payload(audio=0, duration=None, tags={"DURATION": "00:00:02.000"})
    output = payload(320, 180, audio=0, duration=None, tags={"DURATION": "00:00:02.200"})
    result = FfmpegResizeExecutor(runtime, process_factory=Factory(source=source, output=output)).execute(
        request, ResizeControl())
    assert result.output.is_file()


def test_capability_requires_exact_libx264(tmp_path):
    request, runtime = setup(tmp_path)
    assert error_code(lambda: FfmpegResizeExecutor(runtime, process_factory=Factory(
        inventory=" V....D libx264rgb RGB encoder\n")).execute(request, ResizeControl())) == ResizeErrorCode.ENCODER_UNAVAILABLE


def test_paths_with_spaces_and_unicode_are_single_arguments(tmp_path):
    request, runtime = setup(tmp_path)
    factory = Factory()
    FfmpegResizeExecutor(runtime, process_factory=factory).execute(request, ResizeControl())
    command, kwargs = factory.calls[2]
    assert str(request.source) in command and kwargs["shell"] is False
    assert Path(command[-1]).parent.name.startswith(".mediaforge-resize-")
