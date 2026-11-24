"""Logical Qt geometry mapped to physical native screenshot pixels."""
import math


def bright_pixels(image, bounds, dpr):
    assert dpr > 0
    x, y, w, h = bounds
    return [((px + .5) / dpr, (py + .5) / dpr)
            for px in range(max(0, math.floor(x*dpr)), min(image.width(), math.ceil((x+w)*dpr)))
            for py in range(max(0, math.floor(y*dpr)), min(image.height(), math.ceil((y+h)*dpr)))
            if min(image.pixelColor(px, py).red(), image.pixelColor(px, py).green(),
                   image.pixelColor(px, py).blue()) > 170]


def assert_capture_size(window, image):
    dpr = window.devicePixelRatio()
    assert abs(image.width() - window.width()*dpr) <= 1
    assert abs(image.height() - window.height()*dpr) <= 1
    return dpr
