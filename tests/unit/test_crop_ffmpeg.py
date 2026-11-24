import json
from pathlib import Path

import pytest

from crop_support import Factory, payload, setup
from mediaforge.features.media.crop import CropControl, CropError, CropErrorCode
from mediaforge.infrastructure.ffmpeg.crop import FfmpegCropExecutor


def test_exact_command_and_four_phase_managed_pair(tmp_path):
    request, pair = setup(tmp_path)
    factory = Factory()
    result = FfmpegCropExecutor(pair, process_factory=factory).execute(request, CropControl())
    commands = [call[0] for call in factory.calls]
    assert [c[0] for c in commands] == [str(pair.ffprobe), str(pair.ffmpeg), str(pair.ffmpeg), str(pair.ffprobe)]
    assert commands[0][-1] == str(request.source)
    assert commands[1] == [str(pair.ffmpeg), "-hide_banner", "-encoders"]
    product = commands[2]
    assert product == [str(pair.ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-n",
                       "-noautorotate", "-i", str(request.source), "-map", "0:v:0", "-map", "0:a?",
                       "-vf", "crop=320:240:100:40", "-c:v", "libx264", "-preset", "medium",
                       "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-map_chapters", "-1", str(factory.staged)]
    assert commands[3][-1] == str(factory.staged) != str(result.output)
    assert all(options["shell"] is False for _, options in factory.calls)
    assert result.output.read_bytes() == b"STAGED" and request.source.read_bytes() == b"SOURCE"
    assert (result.width, result.height) == (320, 240)
    assert not list(tmp_path.glob(".mediaforge-*"))


@pytest.mark.parametrize("suffix,family,accepted", [
    (".mp4", "mov,mp4,m4a,3gp,3g2,mj2", True), (".mov", "mp4", True),
    (".mkv", "matroska,webm", True), (".MP4", " MOV ", True), (".MOV", "mov", True),
    (".MKV", "matroska", True), (".avi", "mov,mp4", False), (".mp4", "matroska", False),
    (".mov", "avi", False), (".mkv", "webm", False), (".mp4", "mp4fake", False),
])
def test_suffix_and_container_family(tmp_path, suffix, family, accepted):
    request, pair = setup(tmp_path, "source" + suffix)
    factory = Factory(source=payload(family=family))
    executor = FfmpegCropExecutor(pair, process_factory=factory)
    if accepted:
        assert executor.execute(request, CropControl()).output.suffix == suffix
    else:
        with pytest.raises(CropError) as error:
            executor.execute(request, CropControl())
        assert error.value.code == CropErrorCode.UNSUPPORTED_CONTAINER
        assert len(factory.calls) == 1 and list(tmp_path.iterdir()) == [request.source]


IDENTITY = "\n00000000: 65536 0 0\n00000001: 0 65536 0\n00000002: 0 0 1073741824\n"


@pytest.mark.parametrize("change,accepted", [
    ({"tags": {"rotate": "0"}}, True), ({"tags": {"rotate": "90"}}, False),
    ({"tags": {"rotate": "NaN"}}, False),
    ({"side_data_list": [{"side_data_type": "Display Matrix", "rotation": 0, "displaymatrix": IDENTITY}]}, True),
    ({"side_data_list": [{"side_data_type": "Display Matrix", "rotation": 90, "displaymatrix": IDENTITY}]}, False),
    ({"side_data_list": [{"side_data_type": "Display Matrix", "rotation": 0, "displaymatrix": IDENTITY.replace("65536", "-65536", 1)}]}, False),
    ({"side_data_list": [{"side_data_type": "Display Matrix", "rotation": 0}]}, False),
    ({"side_data_list": [{"displaymatrix": "broken"}]}, False),
])
def test_orientation(tmp_path, change, accepted):
    request, pair = setup(tmp_path)
    source = payload()
    source["streams"][0].update(change)
    factory = Factory(source=source)
    executor = FfmpegCropExecutor(pair, process_factory=factory)
    if accepted:
        executor.execute(request, CropControl())
    else:
        with pytest.raises(CropError) as error:
            executor.execute(request, CropControl())
        assert error.value.code == CropErrorCode.UNSUPPORTED_ORIENTATION
        assert len(factory.calls) == 1 and list(tmp_path.iterdir()) == [request.source]


@pytest.mark.parametrize("inventory,accepted", [
    (" V....D libx264 H.264", True), (" V..... libx264 H.264", True),
    (" V....D libx264rgb H.264", False), (" A..... libx264 fake", False),
    ("libx264 mentioned in a diagnostic", False), ("", False),
])
def test_exact_encoder_inventory(tmp_path, inventory, accepted):
    request, pair = setup(tmp_path)
    factory = Factory(inventory=inventory)
    executor = FfmpegCropExecutor(pair, process_factory=factory)
    if accepted:
        executor.execute(request, CropControl())
    else:
        with pytest.raises(CropError) as error:
            executor.execute(request, CropControl())
        assert error.value.code == CropErrorCode.ENCODER_UNAVAILABLE
        assert len(factory.calls) == 2 and list(tmp_path.iterdir()) == [request.source]


@pytest.mark.parametrize("phase,expected", [(0, "preflight_failed"), (1, "encoder_unavailable"),
                                          (2, "process_failed"), (3, "validation_failed")])
def test_phase_exit_failure_not_inferred_from_stderr(tmp_path, phase, expected):
    request, pair = setup(tmp_path)
    factory = Factory(exit_failure=phase)
    with pytest.raises(CropError) as error:
        FfmpegCropExecutor(pair, process_factory=factory).execute(request, CropControl())
    assert error.value.code.value == expected
    assert len(factory.calls) == phase + 1
    assert list(tmp_path.iterdir()) == [request.source]


@pytest.mark.parametrize("source_audio,output_audio,accepted", [(0, 0, True), (1, 1, True), (3, 3, True), (2, 1, False), (0, 1, False), (1, 0, False)])
def test_exact_audio_count(tmp_path, source_audio, output_audio, accepted):
    request, pair = setup(tmp_path)
    factory = Factory(source=payload(audio=source_audio), output=payload(320, 240, output_audio))
    executor = FfmpegCropExecutor(pair, process_factory=factory)
    if accepted:
        executor.execute(request, CropControl())
    else:
        with pytest.raises(CropError) as error:
            executor.execute(request, CropControl())
        assert error.value.code == CropErrorCode.VALIDATION_FAILED
        assert list(tmp_path.iterdir()) == [request.source]


@pytest.mark.parametrize("field,value", [("width", 318), ("height", 238), ("codec_name", "hevc"),
    ("duration", "0"), ("duration", "-1"), ("duration", "NaN"), ("duration", "inf"),
    ("duration", "invalid"), ("duration", None), ("duration", True), ("duration", float("inf")), ("disposition", {"attached_pic": 1})])
def test_invalid_staged_video_never_publishes(tmp_path, field, value):
    request, pair = setup(tmp_path)
    output = payload(320, 240)
    output["streams"][0][field] = value
    factory = Factory(output=output)
    with pytest.raises(CropError) as error:
        FfmpegCropExecutor(pair, process_factory=factory).execute(request, CropControl())
    assert error.value.code == CropErrorCode.VALIDATION_FAILED
    assert list(tmp_path.iterdir()) == [request.source]


@pytest.mark.parametrize("output", [{}, [], {"streams": []}, {"streams": [None]},
    {"streams": [{"codec_type": "audio"}]}])
def test_malformed_staged_output(tmp_path, output):
    request, pair = setup(tmp_path)
    with pytest.raises(CropError) as error:
        FfmpegCropExecutor(pair, process_factory=Factory(output=output)).execute(request, CropControl())
    assert error.value.code == CropErrorCode.VALIDATION_FAILED
    assert list(tmp_path.iterdir()) == [request.source]


@pytest.mark.parametrize("tag,accepted", [(None, False), ("00:00:02.123", True), ("00:00:00.000", False), ("NaN", False)])
def test_missing_stream_duration_and_matroska_video_tag(tmp_path, tag, accepted):
    request, pair = setup(tmp_path)
    output = payload(320, 240)
    del output["streams"][0]["duration"]
    output["format"]["duration"] = "99"  # Audio/container duration cannot rescue missing video duration.
    if tag is not None:
        output["streams"][0]["tags"] = {"DURATION": tag}
    executor = FfmpegCropExecutor(pair, process_factory=Factory(output=output))
    if accepted:
        executor.execute(request, CropControl())
    else:
        with pytest.raises(CropError):
            executor.execute(request, CropControl())
        assert list(tmp_path.iterdir()) == [request.source]


@pytest.mark.parametrize("primary", ["0", "-1", "", "malformed", "NaN", "Inf"])
def test_present_invalid_primary_duration_does_not_fall_back_to_valid_tag(tmp_path, primary):
    request, pair = setup(tmp_path)
    output = payload(320, 240)
    output["streams"][0]["duration"] = primary
    output["streams"][0]["tags"] = {"DURATION": "00:00:02.000"}
    executor = FfmpegCropExecutor(pair, process_factory=Factory(output=output))
    with pytest.raises(CropError) as caught:
        executor.execute(request, CropControl())
    assert caught.value.code == CropErrorCode.VALIDATION_FAILED
    assert list(tmp_path.iterdir()) == [request.source]


def test_changed_coded_dimensions_rejected_before_encoding(tmp_path):
    request, pair = setup(tmp_path)
    factory = Factory(source=payload(642, 360))
    with pytest.raises(CropError) as error:
        FfmpegCropExecutor(pair, process_factory=factory).execute(request, CropControl())
    assert error.value.code == CropErrorCode.SOURCE_CHANGED and len(factory.calls) == 1


@pytest.mark.parametrize("missing", [True, False])
def test_missing_empty_staging(tmp_path, missing):
    request, pair = setup(tmp_path)
    factory = Factory()
    def wrapped(command, **kwargs):
        child = factory(command, **kwargs)
        if len(factory.calls) == 3:
            factory.staged.unlink() if missing else factory.staged.write_bytes(b"")
        return child
    with pytest.raises(CropError) as error:
        FfmpegCropExecutor(pair, process_factory=wrapped).execute(request, CropControl())
    assert error.value.code == CropErrorCode.INVALID_OUTPUT
    assert len(factory.calls) == 3 and list(tmp_path.iterdir()) == [request.source]


@pytest.mark.parametrize("phase,expected", [(0, "preflight_failed"), (3, "validation_failed")])
def test_malformed_json_from_each_probe(tmp_path, phase, expected):
    request, pair = setup(tmp_path)
    factory = Factory()
    def wrapped(command, **kwargs):
        child = factory(command, **kwargs)
        if len(factory.calls) == phase + 1:
            child.text = "{not valid JSON"
        return child
    with pytest.raises(CropError) as error:
        FfmpegCropExecutor(pair, process_factory=wrapped).execute(request, CropControl())
    assert error.value.code.value == expected and len(factory.calls) == phase + 1
    assert list(tmp_path.iterdir()) == [request.source]
