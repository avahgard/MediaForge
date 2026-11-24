"""Image-only integer geometry; accepted video rounding/validators stay intact."""
from dataclasses import dataclass
from decimal import Decimal,InvalidOperation
from fractions import Fraction
import math
from mediaforge.features.media.reformat import GeometryError,ScaleRaster,ReformatPlan,sample_aspect


@dataclass(frozen=True)
class ImageDimensions:
    width: int
    height: int


@dataclass(frozen=True)
class ImageRect:
    x: int
    y: int
    width: int
    height: int


def dimensions(width,height):
    if any(type(v)is not int or not 1<=v<=2_147_483_647 for v in (width,height)):
        raise GeometryError('Image dimensions require positive integer pixels within the native integer range.')
    return ImageDimensions(width,height)


def scale_raster(width,height,text):
    try:
        if not isinstance(text,str) or len(text)>32:raise ValueError
        value=Decimal(text)
        if not value.is_finite() or not Decimal('0.000001')<=value<=Decimal('480'):raise ValueError
        factor=Fraction(value)
    except (ValueError,InvalidOperation,OverflowError):raise GeometryError('Enter a finite positive decimal multiplier (up to 480x).') from None
    w,h=width*factor,height*factor
    def integer(v):return (2*v.numerator+v.denominator)//(2*v.denominator)
    return ScaleRaster(dimensions(integer(w),integer(h)),w,h)


def scaled_dimensions(width,height,text):return scale_raster(width,height,text).output


def locked_dimension(value,input_axis,other_axis):
    if type(value)is not int or input_axis<=0 or other_axis<=0:raise GeometryError('Enter an integer dimension.')
    other=Fraction(value*other_axis,input_axis)
    if other.denominator!=1:raise GeometryError('Aspect lock derives a fractional dimension; choose an exact size or unlock.')
    dimensions(value,int(other));return int(other)


def plan(width,height,output,placement='stretch',sar='1:1'):
    dimensions(width,height);dimensions(output.width,output.height);ratio=sample_aspect(sar)
    if placement=='stretch':pw,ph=Fraction(output.width),Fraction(output.height)
    elif placement in ('fit','fill'):
        factors=(Fraction(output.width,width)/ratio,Fraction(output.height,height))
        factor=min(factors) if placement=='fit' else max(factors);pw,ph=width*ratio*factor,height*factor
    else:raise GeometryError('Choose Fit, Fill or Stretch.')
    return ReformatPlan(width,height,output,pw,ph,(output.width-pw)/2,(output.height-ph)/2,placement,ratio)


def drag(rect,mode,dx,dy,source_width,source_height):
    # Return exact source pixels; the caller validates/commits without video snap.
    if not all(math.isfinite(v) for v in (dx,dy)):raise GeometryError('Invalid pointer delta.')
    x,y,w,h=rect.x,rect.y,rect.width,rect.height;right,bottom=x+w,y+h
    ix,iy=math.floor(dx+.5),math.floor(dy+.5)
    if mode=='move':return ImageRect(min(max(0,x+ix),source_width-w),min(max(0,y+iy),source_height-h),w,h)
    if 'left' in mode:x=min(max(0,x+ix),right-1)
    if 'right' in mode:right=min(max(x+1,right+ix),source_width)
    if 'top' in mode:y=min(max(0,y+iy),bottom-1)
    if 'bottom' in mode:bottom=min(max(y+1,bottom+iy),source_height)
    return ImageRect(x,y,right-x,bottom-y)
