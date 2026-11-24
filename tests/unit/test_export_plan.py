from dataclasses import replace
from fractions import Fraction
from itertools import product
from pathlib import Path

import pytest

from mediaforge.application.services.export import capture, summary
from mediaforge.application.state.workspace import MediaWorkspaceSession
from mediaforge.features.media.export import ExportError, output_aspect
from mediaforge.features.media.reformat import GeometryError
from tests.unit.resize_support import metadata


def item_at(tmp_path, width=1024, height=768):
    path = tmp_path / "исходное видео.mp4"
    path.write_bytes(b"original")
    session = MediaWorkspaceSession()
    item, _ = session.add(metadata(path, width, height))
    output = tmp_path / 'outputs'
    output.mkdir(exist_ok=True)
    item.output.folder = str(output)
    return session, item


def test_copy_and_independent_per_item_defaults(tmp_path):
    session, item = item_at(tmp_path)
    request = capture(item)
    assert request.exact_copy and request.sar == 1
    assert request.destination.name == "исходное видео.mp4"
    clone = session.duplicate(item.id)
    clone.output.codec = "prores"
    clone.resize.pixel_aspect = "16:9"
    assert item.output.codec == "source" and item.resize.pixel_aspect == "square"


@pytest.mark.parametrize("trim,crop,resize", list(product((False, True), repeat=3)))
def test_capture_enabled_operations_only(tmp_path, trim, crop, resize):
    session, item = item_at(tmp_path)
    item.trim.enabled, item.crop.enabled, item.resize.enabled = trim, crop, resize
    item.trim.in_ms, item.trim.out_ms = 400, 1200
    item.crop.x, item.crop.y, item.crop.width, item.crop.height = 100, 100, 640, 480
    item.resize.scale = "0.5"
    item.resize.pixel_aspect = "16:9"
    request = capture(item)
    assert bool(request.trim) == trim and bool(request.crop) == crop and bool(request.reformat) == resize
    assert (request.width, request.height) == ((320,240) if crop and resize else (512,384) if resize else (640,480) if crop else (1024,768))
    assert request.sar == (Fraction(4,3) if resize else 1)
    item.resize.scale = "2"
    assert request.width <= 1024  # the running snapshot is immutable


def test_pixel_aspect_exact_and_fit_on_non_square_canvas(tmp_path):
    session, item = item_at(tmp_path)
    item.resize.enabled = True
    item.resize.pixel_aspect = "16:9"
    request = capture(item)
    assert (request.width, request.height, request.sar, request.dar) == (1024,768,Fraction(4,3),Fraction(16,9))
    item.resize.mode = "format"
    item.resize.placement = "fit"
    p = capture(item).reformat
    assert p.picture_width == 768 and p.picture_height == 768
    assert p.x == 128
    item.resize.pixel_aspect = "square"
    assert capture(item).reformat.picture_width == 1024


def test_source_sar_preserves_pixel_shape_after_crop_and_scale(tmp_path):
    session, item = item_at(tmp_path)
    item.metadata = replace(item.metadata, sample_aspect_ratio="4:3")
    item.crop.enabled = True
    item.crop.width, item.crop.height = 640,480
    item.resize.enabled = True
    item.resize.pixel_aspect = "source"
    assert capture(item).sar == Fraction(4,3)
    assert summary(item)["dar"] == "16:9"
    with pytest.raises(GeometryError):
        output_aspect("source", 16,16,"1:100000")


@pytest.mark.parametrize("container,codec,valid", [("mp4","h264",True),("mov","prores",True),("mp4","prores",False)])
def test_matrix_and_quality(tmp_path, container, codec, valid):
    _, item = item_at(tmp_path)
    item.output.container, item.output.codec = container, codec
    if valid:
        r = capture(item)
        assert r.codec == codec and r.destination.suffix == "." + container
    else:
        with pytest.raises(ExportError, match="MOV"):
            capture(item)


@pytest.mark.parametrize("name", ["../x", "CON", "a:bad", "x.mp4 "])
def test_invalid_destination_never_admitted(tmp_path, name):
    _, item = item_at(tmp_path)
    item.output.filename = name
    with pytest.raises(ExportError):
        capture(item)


def test_existing_source_and_output_rejected(tmp_path):
    _, item = item_at(tmp_path)
    item.output.folder = str(tmp_path)
    item.output.filename = item.metadata.source_name
    with pytest.raises(ExportError, match="never overwritten"):
        capture(item)
    item.output.filename = "occupied.mp4"
    (tmp_path / item.output.filename).write_bytes(b"keep")
    with pytest.raises(ExportError):
        capture(item)
    assert (tmp_path / item.output.filename).read_bytes() == b"keep"


@pytest.mark.parametrize('profile',['main','high'])
def test_lossless_profile_incompatibility_is_specific(tmp_path,profile):
    _,item=item_at(tmp_path);item.output.codec='h264';item.output.h264_profile=profile;item.output.h264_quality=0
    assert not summary(item)['valid'] and 'lossless' in summary(item)['message']
    with pytest.raises(ExportError,match='Main/High'): capture(item)


def test_disabled_bad_values_have_no_effect(tmp_path):
    _, item = item_at(tmp_path)
    item.resize.scale = "NaN"
    item.resize.pixel_aspect = "invalid"
    item.crop.width = 1
    item.trim.out_ms = -1
    assert capture(item).exact_copy


def test_missing_folder_invalid_quality_and_source_codec_are_explicit(tmp_path):
    _, item = item_at(tmp_path)
    item.output.folder = str(tmp_path / "missing")
    with pytest.raises(ExportError, match="existing absolute"):
        capture(item)
    item.output.folder = str(tmp_path)
    item.output.codec = "h264"
    item.output.h264_quality = 52
    with pytest.raises(ExportError, match="CRF"):
        capture(item)
    item.output.codec = "source"
    item.metadata = replace(item.metadata,video_codec="hevc")
    item.crop.enabled = True
    with pytest.raises(ExportError,match="H.264 or ProRes"):
        capture(item)


def test_dotted_recording_basename_appends_effective_extension(tmp_path):
    _, item = item_at(tmp_path)
    item.output.filename = "Counter Strike 2026.10.12 - 00.00.01_export"
    assert capture(item).destination.name == "Counter Strike 2026.10.12 - 00.00.01_export.mp4"


def test_filename_follows_format_and_source_changes_without_replacing(tmp_path):
    session,item=item_at(tmp_path)
    assert item.output.filename=='исходное видео'
    destination=Path(item.output.folder)
    item.output.folder=str(tmp_path)
    with pytest.raises(ExportError,match='Loaded sources'):
        capture(item)
    item.output.folder=str(destination)
    assert session.set_output('filename','Монтаж.mov')
    assert capture(item).destination.name=='Монтаж.mp4'
    assert session.set_output('kind','frame')
    assert session.set_output('kind','media')
    assert item.output.filename=='Монтаж.mov'
    second=tmp_path/'Другой источник.mov'
    second.write_bytes(b'other')
    added,_=session.add(metadata(second,1024,768))
    assert added.output.filename=='Другой источник'
