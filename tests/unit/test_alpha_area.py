"""Independent rational integration oracle, including both historical edge geometries."""
from fractions import Fraction as F
import threading
import pytest
from PySide6.QtGui import QImage,QColor
from mediaforge.infrastructure.images.resample import alpha_area


def rounded(x):return (2*x.numerator+x.denominator)//(2*x.denominator)


@pytest.mark.parametrize('size',[(16,8),(16,16),(7,5)])
def test_hidden_green_edge_and_asymmetric_area(size):
    image=QImage(32,16,QImage.Format_RGBA8888)
    for y in range(16):
        for x in range(32):image.setPixelColor(x,y,QColor(255,0,0,255) if x%2==0 else QColor(0,255,0,0))
    out=alpha_area(image,*size,threading.Event())
    for dy in range(size[1]):
        for dx in range(size[0]):
            left,right=F(dx*32,size[0]),F((dx+1)*32,size[0]);top,bottom=F(dy*16,size[1]),F((dy+1)*16,size[1])
            red=F(0)
            for sy in range(16):
                for sx in range(0,32,2):
                    red+=max(F(0),min(right,sx+1)-max(left,sx))*max(F(0),min(bottom,sy+1)-max(top,sy))
            expected_alpha=rounded(255*red/((right-left)*(bottom-top)))
            pixel=out.pixelColor(dx,dy)
            assert pixel.alpha()==expected_alpha
            assert pixel.red()==255 and pixel.green()==pixel.blue()==0


@pytest.mark.parametrize('alpha',[0,1,64,128,254,255])
def test_constant_alpha_exact_and_source_unchanged(alpha):
    image=QImage(19,13,QImage.Format_RGBA8888);image.fill(QColor(255,0,0,alpha))
    before=bytes(image.constBits());out=alpha_area(image,10,7,threading.Event())
    assert bytes(image.constBits())==before
    for y in range(7):
        for x in range(10):assert out.pixelColor(x,y).alpha()==alpha


def test_alpha_area_asymmetric_colored_layout():
    image=QImage(3,2,QImage.Format_RGBA8888)
    values=[(200,40,100,128),(255,0,0,255),(0,255,0,0),(10,60,230,64),(90,180,20,254),(0,0,255,1)]
    for n,c in enumerate(values):image.setPixelColor(n%3,n//3,QColor(*c))
    out=alpha_area(image,1,1,threading.Event()).pixelColor(0,0)
    assert out.alpha()==rounded(F(sum(c[3] for c in values),6))
    # Premultiplied display values are the independently integrated source.
    raw=alpha_area(image,1,1,threading.Event()).constBits().tobytes()
    assert raw[:3]==bytes(rounded(F(sum(c[i]*c[3] for c in values),6*255)) for i in (2,1,0))
