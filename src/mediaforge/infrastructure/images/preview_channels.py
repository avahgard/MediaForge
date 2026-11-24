"""Native, display-only sampling. Original/export pixels never enter this path."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QColor, QPainter


def display_pixels(image, width, height, channel='RGBA'):
    if channel not in ('RGBA', 'RGB', 'Alpha'):
        raise ValueError('Unknown preview channel.')
    if channel == 'RGB':
        # Convert straight stored channels before any premultiplication. In
        # particular, hidden RGB is not reconstructed from a premultiplied image.
        work = image.convertToFormat(QImage.Format_RGB32)
    elif channel == 'Alpha':
        work = QImage(image.size(), QImage.Format_ARGB32_Premultiplied)
        work.fill(QColor('white'))
        if image.hasAlphaChannel():
            painter = QPainter(work)
            painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            painter.drawImage(0, 0, image)
            painter.end()
            # On black, premultiplied white RGB equals alpha. RGB32 conversion
            # would unpremultiply, so copy through a native SourceOver paint.
            gray = QImage(image.size(), QImage.Format_RGB32)
            gray.fill(QColor('black'))
            painter = QPainter(gray); painter.drawImage(0, 0, work); painter.end()
            work = gray
    else:
        # 16-bit premultiplication avoids early 8-bit edge quantization. Qt's
        # native smooth reduction samples premultiplied color and alpha together.
        work = image.convertToFormat(QImage.Format_RGBA64_Premultiplied)
    if (work.width(), work.height()) != (width, height):
        work = work.scaled(width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return work.convertToFormat(QImage.Format_ARGB32_Premultiplied)


def source_display(image,facts,channel='RGBA'):
    if facts.color_class!='BT.709' or channel=='Alpha':return image
    import numpy as np
    # Fixed 65536-entry display LUT; source/raw backing remains untouched.
    lut=_bt709_lut()
    rgba=image.convertToFormat(QImage.Format_RGBA64)
    a=np.frombuffer(rgba.constBits(),np.uint16).reshape(rgba.height(),rgba.bytesPerLine()//2)[:,:rgba.width()*4].reshape(rgba.height(),rgba.width(),4)
    out=a.copy()
    for k in range(3):out[:,:,k]=lut[a[:,:,k]]
    return QImage(out.data,rgba.width(),rgba.height(),out.strides[0],QImage.Format_RGBA64).copy()


from functools import lru_cache

@lru_cache(maxsize=1)
def _bt709_lut():
    import numpy as np
    e=np.arange(65536,dtype=np.float64)/65535
    linear=np.where(e<.081,e/4.5,((e+.099)/1.099)**(1/.45))
    encoded=np.where(linear<=.0031308,12.92*linear,1.055*linear**(1/2.4)-.055)
    lut=np.floor(encoded*65535+.5).clip(0,65535).astype(np.uint16);lut.flags.writeable=False
    return lut
