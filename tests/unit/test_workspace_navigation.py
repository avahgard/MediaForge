from fractions import Fraction
from pathlib import Path
import math

import pytest

from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe
from mediaforge.presentation.bridge.frame_navigation import FrameNavigation


def cadence_payload(rate=Fraction(25), count=75):
    ticks = 512
    time_base = 1 / (rate * ticks)
    return {
        "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264",
                     "width": 320, "height": 180, "time_base": str(time_base),
                     "avg_frame_rate": str(rate), "r_frame_rate": str(rate),
                     "duration": str(float(count / rate)), "nb_frames": str(count)}],
        "format": {"format_name": "mp4", "duration": str(float(count / rate))},
        # Include B-frame packet ordering; navigation concerns presentation order.
        "packets": [{"stream_index": 0, "pts": i * ticks, "duration": ticks}
                    for i in reversed(range(min(count,65)))],
    }


@pytest.mark.parametrize("rate", [Fraction(25), Fraction(24000, 1001), Fraction(30000, 1001)])
def test_cfr_stream_basis_and_fractional_navigation(rate):
    value = FfprobeMediaMetadataProbe._parse(cadence_payload(rate), Path("test.mp4"))
    assert value.navigation_frame_rate == pytest.approx(float(rate))
    assert value.navigation_frame_count == 75
    assert value.navigation_end_us == round(75 * 1_000_000 / rate)
    navigation = FrameNavigation.from_basis(value.navigation_frame_rate, 75)
    assert navigation.rate == pytest.approx(float(rate)) and navigation.count == 75
    # Stream eligibility is deliberately not an ordinal proof.
    assert not hasattr(navigation, "presented_index")


@pytest.mark.parametrize("problem", ["no_packets", "irregular", "same_average_vfr",
                                     "count", "duration", "shifted", "missing_pts",
                                     "missing_duration", "unknown_count", "rate", "time_base"])
def test_unreliable_basis_never_promotes_average_fps_to_exact_frames(problem):
    payload = cadence_payload()
    stream = payload["streams"][0]
    if problem == "no_packets":
        payload.pop("packets")
    elif problem in ("irregular", "same_average_vfr"):
        # Leave rates, total duration and count unchanged: summary-only detection fails.
        payload["packets"][20]["pts"] += 256
    elif problem == "count":
        stream["nb_frames"] = "74"
    elif problem == "duration":
        stream["duration"] = "4"
    elif problem == "shifted":
        stream["start_time"] = "1"
    elif problem == "missing_pts":
        payload["packets"][20].pop("pts")
    elif problem == "missing_duration":
        payload["packets"][20].pop("duration")
    elif problem == "unknown_count":
        stream["nb_frames"] = "N/A"
    elif problem == "rate":
        stream["r_frame_rate"] = "50"
    else:
        stream["time_base"] = "0/0"
    value = FfprobeMediaMetadataProbe._parse(payload, Path("test.mp4"))
    assert value.frame_rate == 25  # Existing informational metadata still works.
    assert value.navigation_frame_rate is None and value.navigation_frame_count is None


def test_long_cfr_stream_is_eligible_without_whole_file_scan():
    payload = cadence_payload(count=10800)
    payload['packets'] = list(reversed(payload['packets']))[:65]
    value = FfprobeMediaMetadataProbe._parse(payload, Path("long.mp4"))
    assert value.navigation_frame_rate == 25
    assert value.navigation_frame_count == 10800
    assert value.navigation_rate_ratio == '25' and value.navigation_time_base == '1/12800'


@pytest.mark.parametrize("rate,count", [(None, 75), (float("nan"), 75),
                                       (float("inf"), 75), (0, 75), (-25, 75),
                                       (1001, 75), (25, None), (25, 1), (25, True)])
def test_navigation_rejects_invalid_optional_basis(rate, count):
    assert FrameNavigation.from_basis(rate, count) is None
