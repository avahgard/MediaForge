"""Exact code-value area integration for display only; source RGBA is read-only."""
from PySide6.QtGui import QImage
from math import gcd


def _axis(source, target):
    result=[]
    unit=gcd(source,target)
    for output in range(target):
        left,right=output*source,(output+1)*source
        result.append(tuple((i,(min(right,(i+1)*target)-max(left,i*target))//unit)
                            for i in range(left//target,(right+target-1)//target)))
    return result


def alpha_area(image, width, height, cancel):
    """Integer exact overlap weights, half-up alpha and premultiplied channels.

    Unlike scaling straight RGB, hidden colors contribute zero. No intermediate
    eight-bit premultiplication loses precision before integration. Odd sizes use
    equal source-area footprints, not irregular last-pixel bins. Color is ordinary
    sRGB code values, not a claimed linear-light/ICC processing pipeline.
    """
    source=image.convertToFormat(QImage.Format_RGBA8888)
    output=QImage(width,height,QImage.Format_ARGB32_Premultiplied)
    if source.isNull() or output.isNull(): raise ValueError('Preview allocation failed.')
    pixels=source.constBits().cast('I');out=output.bits().cast('I')
    stride=source.bytesPerLine()//4;outstride=output.bytesPerLine()//4
    xs,ys=_axis(source.width(),width),_axis(source.height(),height)
    # Divide both overlap weights and the denominator by their exact common
    # factors. This preserves every rounding decision while avoiding large
    # Python integers for ordinary half/quarter reductions.
    area=(source.width()//gcd(source.width(),width))*(source.height()//gcd(source.height(),height));denom=area*255
    for y,rows in enumerate(ys):
        if cancel.is_set(): raise ValueError('Image preparation cancelled.')
        for x,columns in enumerate(xs):
            a=r=g=b=0
            for sy,wy in rows:
                row=sy*stride
                for sx,wx in columns:
                    value=pixels[row+sx]
                    if not value>>24:continue  # Hidden RGB has exactly zero weight.
                    weight=wx*wy;alpha=(value>>24)*weight
                    a+=alpha;r+=(value&255)*alpha;g+=((value>>8)&255)*alpha;b+=((value>>16)&255)*alpha
            aa=(2*a+area)//(2*area)
            rr=(2*r+denom)//(2*denom);gg=(2*g+denom)//(2*denom);bb=(2*b+denom)//(2*denom)
            out[y*outstride+x]=(aa<<24)|(rr<<16)|(gg<<8)|bb
    return output
