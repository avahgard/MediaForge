"""Keep copy and explicit encoding decisions independent and conservative."""
from dataclasses import replace
from fractions import Fraction
import pytest
from tests.unit.test_dub_contracts import source
from mediaforge.features.dub.contracts import DubError, OutputMode, Refusal, Reason
from mediaforge.features.dub.planning import output_plan


@pytest.mark.parametrize('profile', ['High', 'Main', 'Baseline', 'Constrained Baseline'])
def test_downloaded_h264_profiles_have_an_explicit_copy_route(profile):
    original=source()
    value=replace(original,video=replace(original.video,profile=profile))
    assert output_plan(value,OutputMode.SAME_AS_SOURCE).route.video_codec=='copy'


def test_unqualified_copy_can_offer_encoded_video_without_inventing_packet_evidence():
    original=source()
    value=replace(original,video=replace(original.video,codec='vp9',profile='Profile 0',packet_clock=None))
    with pytest.raises(DubError):output_plan(value,OutputMode.SAME_AS_SOURCE)
    plan=output_plan(value,OutputMode.COMPATIBLE_MP4)
    assert plan.route.video_codec=='libx264' and plan.route.container=='mp4'
    assert plan.duration==original.video.clock.end-original.video.clock.first


@pytest.mark.parametrize('changes,reason', [
    ({'has_side_data':True}, 'side data/orientation'),
    ({'field_order':'tt'}, 'interlaced'),
    ({'sar':Fraction(4,3)}, 'pixel aspect'),
    ({'width':4096}, 'raster'),
    ({'color':('tv','bt2020nc','smpte2084','bt2020')}, 'color'),
])
def test_copy_and_encoding_refuse_unqualified_picture_classes_but_keep_audio(changes,reason):
    original=source();value=replace(original,video=replace(original.video,**changes))
    for mode in (OutputMode.SAME_AS_SOURCE,OutputMode.COMPATIBLE_MP4):
        with pytest.raises(DubError,match=reason):output_plan(value,mode)
    assert output_plan(value,OutputMode.VIDEO_AUDIO_ONLY).sample_count>0


def test_encoding_cannot_drop_unqualified_ancillary_content():
    value=replace(source(),copy_refusal=Refusal(Reason.EXTRA_STREAMS,'Ancillary streams require preservation qualification.'))
    with pytest.raises(DubError,match='Ancillary'):output_plan(value,OutputMode.COMPATIBLE_MP4)
