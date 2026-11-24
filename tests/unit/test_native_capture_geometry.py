import sys
from pathlib import Path
import pytest
from PySide6.QtGui import QImage, QColor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_capture_geometry import bright_pixels, assert_capture_size


@pytest.mark.parametrize('dpr', [1.,1.5,2.])
def test_physical_pixels_normalize_to_logical_icon_geometry(dpr):
    image=QImage(round(100*dpr),round(60*dpr),QImage.Format_RGB32)
    image.fill(QColor('black'))
    for x in range(round(24*dpr),round(28*dpr)):
        for y in range(round(20*dpr),round(40*dpr)):
            image.setPixelColor(x,y,QColor('white'))
    pixels=bright_pixels(image,[2,6,48,48],dpr)
    assert pixels
    assert sum(x for x,y in pixels)/len(pixels)==pytest.approx(26)
    assert sum(y for x,y in pixels)/len(pixels)==pytest.approx(30)
    class Window:
        def devicePixelRatio(self):return dpr
        def width(self):return 100
        def height(self):return 60
    assert assert_capture_size(Window(),image)==dpr
