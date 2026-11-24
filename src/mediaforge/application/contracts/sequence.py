"""Immutable numbered sources and exact ordinal time; no decoder/UI dependencies."""
from dataclasses import dataclass
from fractions import Fraction
import re
from decimal import Decimal

FPS_PRESETS={'23.976':Fraction(24000,1001),'29.97':Fraction(30000,1001),
             '59.94':Fraction(60000,1001)}


def friendly_rate(rate):
    rate=Fraction(rate)
    for label,value in FPS_PRESETS.items():
        if rate==value:return label
    if rate.denominator==1:return str(rate.numerator)
    # Terminating custom decimals are represented exactly, never float-rounded.
    denominator=rate.denominator
    for divisor in (2,5):
        while denominator%divisor==0:denominator//=divisor
    return format(Decimal(rate.numerator)/Decimal(rate.denominator),'f').rstrip('0').rstrip('.') if denominator==1 else str(rate)


def sequence_rate(text: str) -> Fraction:
    if isinstance(text,str) and re.fullmatch(r'[0-9]{1,3}(?:\.[0-9]{1,6})?',text):
        rate=Fraction(text)  # Manual decimals have exact base-10 semantics; presets send ratios.
        if not 1<=rate<=120:raise ValueError('FPS must be between 1 and 120.')
        return rate
    if not isinstance(text, str) or not re.fullmatch(r'[0-9]{1,9}(?:/[0-9]{1,7})?', text):
        raise ValueError('Enter FPS between 1 and 120 (for example 24 or 23.9).')
    numerator,denominator=map(int,text.split('/') if '/' in text else (text,'1'))
    if denominator==0:raise ValueError('FPS denominator must be positive.')
    if not 1<=numerator<=120_000_000 or not 1<=denominator<=1_000_000:
        raise ValueError('FPS ratio exceeds the bounded precision (denominator up to 1,000,000).')
    try:
        rate = Fraction(text)
    except ZeroDivisionError as error:
        raise ValueError('FPS denominator must be positive.') from error
    if not 1 <= rate <= 120:
        raise ValueError('FPS must be between 1 and 120.')
    return rate


def numbered_name(name, allow_unsupported=False):
    # Last digit run immediately before the extension. Other digit runs in the
    # literal prefix are preserved, never interpreted as FPS or an ordinal.
    match = re.fullmatch(r'(.*?)([0-9]+)([^0-9]*)(\.[^.]+)', name)
    if not match or (match[1] and match[1][-1].isdigit()):
        raise ValueError('Select a member with an unambiguous 1–9 digit frame number.')
    prefix, digits, suffix, extension = match.groups()
    if not allow_unsupported and len(digits)>9: raise ValueError('Frame numbers must have 1–9 digits.')
    if not allow_unsupported and extension.lower() not in ('.png', '.jpg', '.jpeg', '.tif', '.tiff'):
        raise ValueError('Sequences support PNG, JPEG and TIFF only.')
    return prefix, int(digits), len(digits), suffix, extension


@dataclass(frozen=True, slots=True, weakref_slot=True)
class SequenceManifest:
    members: tuple  # immutable StillSource identities in numeric order
    numbers: tuple[int, ...]
    prefix: str
    suffix: str
    padding: int
    extension: str
    identity: str

    @property
    def pattern(self):
        return f'{self.prefix}%0{self.padding}d{self.suffix}{self.extension}'

    @property
    def count(self): return len(self.members)

    def time(self, slot, rate):
        if not 0 <= slot <= self.count: raise ValueError('Slot outside sequence.')
        return Fraction(slot, 1) / rate

    def slot_at(self, seconds, rate):
        return max(0, min(self.count-1, int(seconds * rate)))
