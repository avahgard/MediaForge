from fractions import Fraction
import pytest
from mediaforge.features.media.image_geometry import dimensions,scale_raster,plan,drag,ImageRect
from mediaforge.features.media.reformat import GeometryError,scale_raster as video_scale


def test_image_scale_half_up_and_custom_odd_pixels_preserve_video_rule():
    image=scale_raster(129,97,'0.5');video=video_scale(129,97,'0.5')
    assert (image.output.width,image.output.height)==(65,49)
    assert (video.output.width,video.output.height)==(64,48)
    assert dimensions(1,1).width==1 and dimensions(17,19).height==19
    for invalid in ((0,1),(1,-1),(True,1),(2**31,1)):
        with pytest.raises(GeometryError):dimensions(*invalid)


def test_source_coordinate_drag_is_integer_not_video_even_snap():
    assert drag(ImageRect(1,3,17,19),'move',1,1,100,100)==ImageRect(2,4,17,19)
    assert drag(ImageRect(1,3,17,19),'right-bottom',-16,-18,100,100)==ImageRect(1,3,1,1)
    g=plan(31,17,dimensions(17,19),'fit')
    assert g.picture_width==17 and g.picture_height==Fraction(17*17,31)
    assert g.encoding_steps() and g.output.width==17
