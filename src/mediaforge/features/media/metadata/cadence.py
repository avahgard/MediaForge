"""Operation-neutral stream timing eligibility; adjacency still needs local proof."""
from fractions import Fraction


def cfr_stream_basis(stream: dict) -> tuple[Fraction, Fraction, int] | None:
    try:
        rate = Fraction(stream['avg_frame_rate'])
        time_base = Fraction(stream['time_base'])
        count = int(stream['nb_frames'])
        duration = Fraction(str(stream['duration']))
        if (not 0 < rate <= 1000 or rate != Fraction(stream['r_frame_rate'])
                or time_base <= 0 or count < 2
                or Fraction(str(stream.get('start_time', '0'))) != 0
                or (1 / rate / time_base).denominator != 1
                or abs(duration - count / rate) > time_base):
            return None
        return rate, time_base, count
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
