"""Fixed local first-video packet proof using the authenticated Bootstrap runtime.

At most 512 packets, four seconds of process lifetime, no whole-file index/cache.
Seeking may land on a preceding keyframe. Insufficient local observations fail
closed; the window is never enlarged or retried to compensate for a long GOP.
"""
from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess
import time
from mediaforge.application.contracts.frame_step import AdjacentFrameResult

LOCAL_PACKET_LIMIT = 512
LOCAL_TIMEOUT_SECONDS = 4.0


class FfprobeLocalAdjacency:
    def __init__(self, executable: Path | None, *, process_factory=subprocess.Popen):
        self._executable = executable
        self._factory = process_factory

    def resolve(self, request, control):
        if self._executable is None or request.direction not in (-1, 0, 1):
            return None
        rate, time_base = Fraction(request.rate_ratio), Fraction(request.time_base)
        if not 0 < rate <= 1000 or time_base <= 0:
            return None
        period = 1 / rate
        anchor = max(Fraction(0), Fraction(request.current_start_us, 1_000_000) - 2 * period)
        command = [str(self._executable), '-v', 'error', '-select_streams', 'v:0',
                   '-show_packets', '-show_entries', 'packet=pts,duration',
                   '-read_intervals', f'{float(anchor):.9f}%+#{LOCAL_PACKET_LIMIT}',
                   '-of', 'json', request.source_path]
        process = control.spawn(self._factory, command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', errors='replace', shell=False)
        try:
            deadline = time.monotonic() + LOCAL_TIMEOUT_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if control.cancelled.is_set() or remaining <= 0:
                    control.stop(process)
                    return None
                try:
                    stdout, _stderr = process.communicate(timeout=min(.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    # Bounded pipe wait, not a readiness delay or a new probe.
                    continue
            if control.cancelled.is_set() or process.returncode != 0:
                return None
            packets = json.loads(stdout).get('packets')
            return self.prove(request, packets)
        finally:
            if process.poll() is None:
                control.stop(process)
            control.release(process)

    @staticmethod
    def prove(request, packets):
        try:
            if request.direction not in (-1, 0, 1):
                return None
            if not isinstance(packets, list) or not 2 <= len(packets) <= LOCAL_PACKET_LIMIT:
                return None
            rate, time_base = Fraction(request.rate_ratio), Fraction(request.time_base)
            if not 0 < rate <= 1000 or time_base <= 0:
                return None
            period = 1 / rate
            if any(type(p.get('pts')) is not int or type(p.get('duration')) is not int for p in packets):
                return None
            pts = sorted(p['pts'] for p in packets)
            current = min(range(len(pts)), key=lambda i: abs(pts[i] * time_base * 1_000_000 - request.current_start_us))
            if abs(pts[current] * time_base * 1_000_000 - request.current_start_us) > 2:
                return None
            adjacent = current + request.direction
            if not 0 <= adjacent < len(pts):
                return None
            # Prove the neighboring triple (or boundary pair), not average FPS.
            lo, hi = max(0, min(current, adjacent)-1), min(len(pts)-1, max(current, adjacent)+1)
            if any((pts[i+1]-pts[i])*time_base != period for i in range(lo, hi)):
                return None
            durations = {p['pts']: p['duration'] for p in packets}
            if any(durations[pts[i]]*time_base != period for i in range(lo, hi+1)):
                return None
            start, end = pts[adjacent]*time_base, pts[adjacent]*time_base + period
            seek = math.floor((start+end)*500)
            if not start*1000 < seek < end*1000:
                return None
            # Missing neighbors are clamps only at independently known source
            # timing boundaries. A packet-window edge is never a source edge.
            if adjacent == 0 and start != 0:
                return None
            if adjacent == len(pts)-1 and (request.source_end_us is None or
                    abs(end*1_000_000-request.source_end_us) > 1000):
                return None
            return AdjacentFrameResult(request.direction, round(start*1_000_000),
                round(end*1_000_000), seek, len(packets),
                round(pts[adjacent-1]*time_base*1_000_000) if adjacent > 0 else None,
                round(pts[adjacent+1]*time_base*1_000_000) if adjacent+1 < len(pts) else None)
        except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
            return None
