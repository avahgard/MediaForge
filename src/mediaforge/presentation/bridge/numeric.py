"""Exact text/caret arithmetic for the small Inspector numeric fields."""
from decimal import Decimal, localcontext
import re

_DECIMAL = re.compile(r'[+-]?\d+(?:\.\d*)?\Z')


def scrub(text, cursor, start, end, direction, place=''):
    if not isinstance(text, str) or len(text) > 64 or direction not in (-1, 1):
        return {}
    left, right = sorted((start, end))
    position = left if left != right else cursor
    offset = 0
    part = text
    if '/' in text:
        pieces = text.split('/')
        if len(pieces) != 2 or not all(p.isascii() and p.isdigit() and int(p)>0 for p in pieces):
            return {}
        if position > len(pieces[0]):
            offset = len(pieces[0]) + 1
            part = pieces[1]
        else:
            part = pieces[0]
    if not _DECIMAL.fullmatch(part) or not part.isascii():
        return {}
    pos = max(0, min(len(part), position - offset))
    point = part.find('.')
    if place == '':
        if pos == len(part):
            exponent = 0 if point < 0 else point - len(part)
        else:
            while pos < len(part) and not part[pos].isdigit():
                pos += 1
            exponent = (len(part) - pos - 1) if point < 0 else (point - pos - (1 if pos < point else 0))
    else:
        try:
            exponent = int(place)
        except ValueError:
            return {}
    if not -18 <= exponent <= 15:
        return {}
    with localcontext() as context:
        context.prec = 96
        value = Decimal(part) + direction * Decimal(10) ** exponent
        precision = max(len(part) - point - 1 if point >= 0 else 0, -exponent)
        result = format(value, f'.{precision}f')
    replacement = result
    if '/' in text:
        if Decimal(result) <= 0 or '.' in result:
            return {}
        result = text[:offset] + result + text[offset+len(part):]
    old_anchor = point if point >= 0 else len(part)
    new_point = replacement.find('.')
    new_anchor = new_point if new_point >= 0 else len(replacement)
    # Both integer AND fractional positions follow the decimal anchor. Added
    # fractional precision does not move a caret off its chosen place.
    shift = new_anchor - old_anchor
    def remap(index):
        if index < offset:
            return index
        if index > offset + len(part):
            return index + len(replacement) - len(part)
        return offset + max(0, min(len(replacement), index - offset + shift))
    return dict(text=result, cursor=remap(cursor), start=remap(left), end=remap(right), place=str(exponent))
