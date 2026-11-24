from fractions import Fraction
from itertools import product

import pytest

from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.state.workspace import MediaWorkspaceSession
from mediaforge.features.media.reformat import (
    GeometryError, PRESETS, dimensions, locked_dimension, plan, scaled_dimensions, scale_raster,
)
from mediaforge.presentation.bridge.viewer_viewport import ViewerViewport


@pytest.mark.parametrize("scale,size", [("0.5", (960, 540)), ("1.0", (1920, 1080)),
                                       ("1.5", (2880, 1620)), ("2.0", (3840, 2160))])
def test_scale_exact(scale, size):
    result = scaled_dimensions(1920, 1080, scale)
    assert (result.width, result.height) == size


@pytest.mark.parametrize("w,h,text", [(1920, 1080, "5"), (16, 16, ".5"),
                                      (29, 29, ".5"), (7680, 4320, "1.001"),
                                      (7681, 4320, "1"),
                                      (5759, 5761, "1"),
                                      (1920, 1080, "NaN"), (1920, 1080, "1e999999"),
                                      (1920, 1080, "-1"), (1920, 1080, "0")])
def test_scale_rejects_invalid_and_out_of_envelope_without_clamping(w, h, text):
    with pytest.raises(GeometryError):
        scaled_dimensions(w, h, text)


@pytest.mark.parametrize("w,h,text,expected", [
    (1024, 768, "0.6", (614, 460)), (1024, 768, "0.7", (716, 538)),
    (1921, 1080, "0.5", (960, 540)), (1920, 1081, "1", (1920, 1082)),
    (1920, 1080, "0.3333333333333333", (640, 360)),
    (100, 100, "0.17", (18, 18)), (100, 100, "0.19", (20, 20)),
    (31, 31, ".5", (16, 16)), (7680, 4320, "1.00001", (7680, 4320)),
])
def test_scale_nearest_even_ties_up_exact_decimal(w, h, text, expected):
    from decimal import Decimal
    result = scale_raster(w, h, text)
    assert (result.output.width, result.output.height) == expected
    assert result.desired_width == w * Fraction(Decimal(text))
    assert result.desired_height == h * Fraction(Decimal(text))
    assert result.quantized
    assert abs(result.output.width - result.desired_width) <= 1
    assert abs(result.output.height - result.desired_height) <= 1


@pytest.mark.parametrize("preset", PRESETS)
@pytest.mark.parametrize("placement", ["fit", "fill", "stretch"])
def test_presets_exact_canvas_and_uniform_placement(preset, placement):
    _, w, h = preset
    p = plan(1920, 1080, dimensions(w, h), placement)
    assert (p.output.width, p.output.height) == (w, h)
    if placement != "stretch":
        assert p.picture_width / p.picture_height == Fraction(16, 9)
    assert p.x * 2 + p.picture_width == w
    assert p.y * 2 + p.picture_height == h


def test_crop_then_stretch_and_fit_have_distinct_geometry():
    stretch = plan(1440, 1080, dimensions(1920, 1080), "stretch")
    fit = plan(1440, 1080, dimensions(1920, 1080), "fit")
    fill = plan(1440, 1080, dimensions(1920, 1080), "fill")
    assert stretch.x == 0 and stretch.picture_width == 1920
    assert fit.x == 240 and fit.picture_width == 1440
    assert fill.y == -180 and fill.picture_height == 1440
    assert fit.encoding_geometry() == (1440, 1080, 240, 0)


def test_portrait_and_fractional_intermediates_are_exact_in_preview():
    p = plan(1080, 1920, dimensions(1920, 1080), "fit")
    assert p.picture_width == Fraction(1215, 2)
    assert p.picture_width / p.picture_height == Fraction(9, 16)
    assert p.encoding_geometry() == (607, 1080, 656, 0)
    g = plan(16, 7680, dimensions(7680, 4320), "fill").encoding_window()
    assert (g.width, g.height) == (7680, 4800)
    assert (g.source_y, g.source_height) == (3835, 10)


@pytest.mark.parametrize("preset", PRESETS)
@pytest.mark.parametrize("placement", ["fit", "fill"])
@pytest.mark.parametrize("sar", ["1:1", "4:3", "3:4"])
def test_portrait_all_presets_rasterize_contain_cover_and_center(preset, placement, sar):
    _, w, h = preset
    p = plan(1080, 1920, dimensions(w, h), placement, sar)
    g = p.encoding_window()
    assert (p.output.width, p.output.height) == (w, h)
    if placement == "fit":
        assert 0 <= p.picture_width - g.width < 1
        assert 0 <= p.picture_height - g.height < 1
        assert g.width <= w and g.height <= h
    else:
        assert g.width >= w and g.height >= h
        assert g.source_x * 2 + g.source_width == 1080
        assert g.source_y * 2 + g.source_height == 1920
        factor = max(Fraction(w, 1080) / p.sar, Fraction(h, 1920))
        assert 0 <= g.width - g.source_width * p.sar * factor < 1
        assert 0 <= g.height - g.source_height * factor < 1
    assert abs(g.x + g.width / 2 - w / 2) <= .5
    assert abs(g.y + g.height / 2 - h / 2) <= .5


def test_sar_fit_fill_crop_and_square_pixel_controls(tmp_path):
    p = plan(192, 108, dimensions(512, 288), "fit", "4:3")
    assert (p.picture_width, p.picture_height, p.x, p.y) == (512, 216, 0, 36)
    assert p.encoding_geometry() == (512, 216, 0, 36)
    p = plan(192, 108, dimensions(512, 288), "fill", "4:3")
    assert (p.picture_width, p.picture_height, p.x, p.y) == (Fraction(2048, 3), 288, Fraction(-256, 3), 0)
    assert plan(320, 180, dimensions(160, 120), "fit").encoding_geometry() == (160, 90, 0, 15)
    s = MediaWorkspaceSession()
    s.add(MediaMetadataDto("sar.mp4", str(tmp_path/'sar.mp4'), "mp4", 1., "h264", 192, 108, 25., sample_aspect_ratio="4:3"))
    assert s.set_crop(24, 0, 144, 108)
    s.set_enabled("crop", True)
    s.set_reformat("mode", "format"); s.set_reformat("lock", "false")
    s.set_reformat("width", "512"); s.set_reformat("height", "288")
    s.set_reformat("placement", "fit")
    p = s.reformat_plan()
    assert (p.input_width, p.input_height, p.picture_width, p.picture_height) == (144, 108, 512, 288)
    assert plan(192, 108, dimensions(512, 288), "stretch", "4:3").encoding_geometry() == (512, 288, 0, 0)


@pytest.mark.parametrize("sar", ["1:1", "4:3", "3:4"])
@pytest.mark.parametrize("size", [(16, 16), (7680, 4320), (4320, 7680), (7680, 16)])
def test_edge_canvases_remain_exact_and_internal_geometry_bounded(sar, size):
    for mode in ("fit", "fill"):
        p = plan(320, 180, dimensions(*size), mode, sar)
        g = p.encoding_window()
        assert p.output == dimensions(*size)
        assert g.width * g.height <= 99_532_800


@pytest.mark.parametrize("sar", ["1:10000", "10000:1", "1:2147483647", "2147483647:1"])
def test_extreme_sar_fill_samples_without_virtual_raster_allocations(sar):
    p = plan(192, 108, dimensions(512, 288), "fill", sar)
    steps = p.encoding_steps()
    assert len(steps) < 100
    for step in steps:
        assert step[1] * step[2] <= 2 * 512 * 288
        assert step[1] <= 1024 and step[2] <= 576
    last = steps[-1]
    assert last[:3] == ("crop", 512, 288)
    g = p.encoding_window()
    ax = ay = Fraction(1)
    bx, by = -Fraction(g.source_x), -Fraction(g.source_y)
    w, h = g.source_width, g.source_height
    for step in steps:
        nw, nh = step[1:3]
        if step[0] == "scale":
            ax, bx = ax*nw/w, bx*nw/w
            ay, by = ay*nh/h, by*nh/h
        else:
            bx -= step[3]; by -= step[4]
        w, h = nw, nh
    # Independently invert the composed affine map at the visible edges. Compare
    # with the continuous picture, in output pixels rather than virtual extent.
    for extent, a, b, ideal_a, ideal_b in (
        (512, ax, bx, p.picture_width/p.input_width, p.x),
        (288, ay, by, p.picture_height/p.input_height, p.y),
    ):
        for edge in (0, extent):
            error = abs(((edge-b)/a - (edge-ideal_b)/ideal_a)*ideal_a)
            assert error <= 1, (sar, extent, edge, error)


def test_custom_lock_rejects_fractional_and_odd_without_fixup():
    assert locked_dimension(1280, 1920, 1080) == 720
    assert locked_dimension(720, 1080, 1920) == 1280
    for value in (1282, 17, 8000):
        with pytest.raises(GeometryError):
            locked_dimension(value, 1920, 1080)


def session(tmp_path):
    s = MediaWorkspaceSession()
    item, _ = s.add(MediaMetadataDto("source.mp4", str(tmp_path / "source.mp4"),
                                  "mp4", 5., "h264", 1920, 1080, 25.))
    return s, item


@pytest.mark.parametrize("trim,crop,reformat", product((False, True), repeat=3))
def test_all_enabled_combinations_effective_size(tmp_path, trim, crop, reformat):
    s, item = session(tmp_path)
    s.set_crop(240, 0, 1440, 1080)
    for op, enabled in (("trim", trim), ("crop", crop), ("resize", reformat)):
        s.set_enabled(op, enabled)
    s.set_reformat("scale", "0.5")
    p = s.reformat_plan()
    assert (p.output.width, p.output.height) == ((720 if crop else 960), 540)
    assert item.resize.enabled == reformat


def test_modes_items_duplicate_reorder_keep_independent_values(tmp_path):
    s, a = session(tmp_path)
    assert s.set_reformat("scale", "1.5")
    assert s.set_reformat("mode", "format")
    assert s.set_reformat("preset", "HD")
    assert s.set_reformat("placement", "fill")
    clone = s.duplicate(a.id)
    s.select(clone.id)
    assert s.set_reformat("scale", "2.0")
    s.move(clone.id, -1)
    s.select(a.id)
    assert a.resize.scale == "1.5" and a.resize.preset == "HD"
    assert s.set_reformat("mode", "scale")
    assert s.reformat_plan().output.width == 2880
    assert s.set_reformat("mode", "format")
    assert s.reformat_plan().output.width == 1280
    assert s.set_reformat("preset", "Custom")
    assert s.set_reformat("lock", "true")
    assert s.set_reformat("width", "1920")
    assert a.resize.height == 1080
    assert not s.set_reformat("width", "1282")
    assert (a.resize.width, a.resize.height) == (1920, 1080)


def test_viewport_pointer_anchor_pan_clamp_and_hidpi():
    v = ViewerViewport()
    v.configure(800, 600, 1920, 1080, 1.5)
    assert v.scale == pytest.approx(800 / 1920)
    v.wheel(600, 300, 4)
    assert v.panX == pytest.approx(200 * (1 - 1.2 ** 4))
    v.pan(1e6, -1e6)
    assert v.panX == pytest.approx((1920 * v.scale - 800) / 2)
    assert v.panY == pytest.approx(-(1080 * v.scale - 600) / 2)
    v.pixels()
    assert v.scale * 1.5 == 1 and v.panX == v.panY == 0
    v.configure(600, 800, 1080, 1920, 2.)
    assert v.scale == .5
    v.wheel(300, 400, 1e6)
    assert v.scale <= max(32 * 800 / 1920, .5)
    v.fit()
    assert v.panX == v.panY == 0 and v.mode == "fit"


@pytest.mark.parametrize("dpr", [1., 1.5, 2.])
def test_fit_minimum_relative_percentage_anchor_and_reset(dpr):
    v = ViewerViewport()
    v.configure(800, 600, 1920, 1080, dpr)
    fit = v.scale
    assert v.zoomText == "100.0%"
    v.wheel(600, 300, -1000)
    assert v.scale == fit and v.panX == v.panY == 0
    v.wheel(600, 300, 4)
    assert (600 - 400 - v.panX) / v.scale == pytest.approx(200 / fit)
    assert float(v.zoomText[:-1]) == pytest.approx(1.2**4 * 100, abs=.05)
    v.wheel(600, 300, -1000)
    assert v.scale == fit and v.panX == v.panY == 0
    v.wheel(600, 300, 1000)
    assert v.scale == fit * 32
    v.pan(50, 50); v.fit()
    assert v.zoomText == "100.0%" and v.panX == v.panY == 0
    v.configure(800, 600, 160, 120, dpr); v.pixels()
    # Owner OC-01 (PROJECT_INVARIANTS, 2026-10-19): the pixel control is
    # independent of Fit. One source pixel occupies one physical pixel even
    # for a source smaller than the viewport. The wheel's Fit bound above
    # remains unchanged; zoomText is the separate Fit-relative readout.
    assert v.mode == "pixels" and v.scale == pytest.approx(1 / dpr)
    assert v.pixelZoomText == "100.0%" and v.panX == v.panY == 0
    assert float(v.zoomText[:-1]) == pytest.approx(100 / (5 * dpr), abs=.05)
    v.fit()
    assert v.mode == "fit" and v.scale == 5 and v.zoomText == "100.0%"


def test_source_preset_restores_original_canvas_without_resetting_crop_or_method(tmp_path):
    s, a = session(tmp_path)
    assert s.set_enabled("trim", True) and s.set_trim(100, 3000)
    assert s.set_crop(240, 0, 1440, 1080) and s.set_enabled("crop", True)
    assert s.set_reformat("mode", "format")
    assert s.set_reformat("placement", "fill")
    assert s.set_reformat("preset", "HD")
    assert s.set_reformat("preset", "Source")
    p = s.reformat_plan()
    assert (p.input_width, p.input_height) == (1440, 1080)
    assert (p.output.width, p.output.height) == (1920, 1080)
    assert a.resize.placement == "fill" and a.crop.x == 240 and a.trim.in_ms == 100
    clone = s.duplicate(a.id)
    assert clone.resize.preset == "Source" and clone.resize is not a.resize
    assert s.move(clone.id, -1); s.select(clone.id)
    assert s.set_reformat("scale", ".7")
    s.select(a.id); assert a.resize.scale == "1.0"
    assert not s.set_reformat("width", "1025")  # Custom remains exact even-only.
    assert a.resize.width == 1920
