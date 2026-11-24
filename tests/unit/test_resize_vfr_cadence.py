import pytest

from tests.check_resize_runtime import assert_vfr_cadence


def test_vfr_cadence_accepts_common_mux_offset_and_rounding():
    source = [0.100, 0.133, 0.167, 0.233, 0.300]
    output = [1.000, 1.033, 1.067, 1.133, 1.200]
    assert assert_vfr_cadence(source, output) <= .0015


def test_vfr_cadence_rejects_same_count_cfr_timestamps():
    source = [0.000, 0.033, 0.067, 0.133, 0.200]
    cfr = [0.000, 0.050, 0.100, 0.150, 0.200]
    with pytest.raises(AssertionError, match="cadence"):
        assert_vfr_cadence(source, cfr)


@pytest.mark.parametrize("output", [[], [0.0, 0.033]])
def test_vfr_cadence_rejects_missing_or_extra_timestamp_entries(output):
    with pytest.raises(AssertionError):
        assert_vfr_cadence([0.0, 0.033, 0.100], output)


def test_vfr_cadence_rejects_cfr_source_fixture():
    with pytest.raises(AssertionError, match="not demonstrably VFR"):
        assert_vfr_cadence([0.0, 0.04, 0.08], [0.0, 0.04, 0.08])
