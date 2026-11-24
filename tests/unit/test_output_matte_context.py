"""The Output form can resolve a matte refusal without an invisible control."""
import pytest
from tests.unit.test_image_export import make_sequence,make_item
from mediaforge.application.services.image_export import describe,capture
from mediaforge.features.media.export import ExportError


@pytest.mark.parametrize('container,codec',[('mp4','h264'),('mov','h264'),('mov','prores')])
def test_missing_matte_keeps_actionable_metadata_and_never_freezes(tmp_path,container,codec):
    item=make_sequence(tmp_path,alpha=True);item.output.container=container;item.output.codec=codec
    info=describe(item);assert not info['valid'] and info['hasAlpha'] and info['matteRequired']
    with pytest.raises(ExportError,match='matte'):capture(item)
    assert item.output.matte==''
    for matte in ['black','white']:
        item.output.matte=matte;info=describe(item)
        assert info['valid'] and info['matteRequired'] and capture(item).matte==matte
        assert item.sequence.rate==capture(item).rate


@pytest.mark.parametrize('container',['png','tiff','jpeg'])
def test_still_alpha_preservation_and_opaque_destinations(tmp_path,container):
    item=make_item(tmp_path);item.output.container=container;info=describe(item)
    assert info['hasAlpha'] and info['matteRequired']==(container=='jpeg')
    assert info['valid']==(container!='jpeg') and item.output.matte==''
    item.output.matte='white';assert describe(item)['valid'] and capture(item).matte=='white'


def test_rgb_needs_no_matte_but_fit_padding_does(tmp_path):
    item=make_sequence(tmp_path,alpha=False);item.output.container='mp4';item.output.codec='h264'
    assert describe(item)['valid'] and not describe(item)['hasAlpha'] and not describe(item)['matteRequired']
    item.resize.enabled=True;item.resize.mode='format';item.resize.preset='Custom';item.resize.width=192;item.resize.height=192;item.resize.placement='fit'
    info=describe(item);assert not info['valid'] and info['hasAlpha'] and info['matteRequired']
    item.output.matte='black';assert describe(item)['valid'] and capture(item).matte=='black'
