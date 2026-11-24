"""Session-local presentation timing, independent of Qt and nominal frame rate."""
from array import array
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from pathlib import Path
import os
from typing import Protocol

from mediaforge.application.contracts.frame_step import LocalProbeControl as TimelineBuildControl

TIMELINE_BYTES_LIMIT = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SourceSignature:
    canonical_path: str
    size: int
    mtime_ns: int

    @classmethod
    def capture(cls, source):
        path = Path(source).resolve()
        stat = path.stat()
        if not path.is_file():
            raise ValueError('Source is not a local file')
        return cls(os.path.normcase(str(path)), stat.st_size, stat.st_mtime_ns)


@dataclass(frozen=True, slots=True)
class SourcePresentationTimeline:
    """Immutable packed int64 microseconds; no mutable buffer is exposed."""
    _storage: bytes
    last_duration_us: int

    @classmethod
    def from_timestamps(cls, timestamps: array, last_duration_us: int):
        if timestamps.typecode != 'q' or timestamps.itemsize != 8:
            raise ValueError('Timeline requires int64 storage')
        if not timestamps or len(timestamps)*8 > TIMELINE_BYTES_LIMIT:
            raise ValueError('Timeline storage unavailable')
        if any(timestamps[index]<=timestamps[index-1] for index in range(1,len(timestamps))):
            raise ValueError('Ambiguous presentation timestamps')
        if last_duration_us <= 0:
            raise ValueError('Last presentation interval unavailable')
        return cls(timestamps.tobytes(), last_duration_us)

    @property
    def timestamps(self):
        return memoryview(self._storage).cast('q')

    @property
    def count(self):
        return len(self._storage)//8

    @property
    def storage_bytes(self):
        return len(self._storage)

    def map_timestamp(self, timestamp_us):
        values = self.timestamps
        position = bisect_left(values, timestamp_us)
        matches = []
        for index in (position-1,position):
            if not 0 <= index < len(values):
                continue
            value = values[index]
            if value == timestamp_us:
                matches.append(index)
                continue
            gaps = []
            if index > 0: gaps.append(value-values[index-1])
            if index+1 < len(values): gaps.append(values[index+1]-value)
            if gaps and abs(timestamp_us-value) <= min(5000,min(gaps)/4):
                matches.append(index)
        return matches[0] if len(matches)==1 else None

    def index_at_time(self, timestamp_us):
        """Requested interval, distinct from decoded PTS admission (map_timestamp).

        VFR gaps retain the preceding presentation ordinal. Clamp both endpoints;
        never infer picture identity from a nominal or rounded frame rate.
        """
        return max(0, min(self.count-1, bisect_right(self.timestamps, timestamp_us)-1))

    def seek_ms(self, index, offset_us=0):
        values = self.timestamps
        start = values[index]+offset_us
        end = values[index+1]+offset_us if index+1<len(values) else start+self.last_duration_us
        target = (start+end)//2000
        if not start <= target*1000 < end:
            raise ValueError('Qt millisecond seek cannot address this picture')
        return target


@dataclass(frozen=True, slots=True)
class TimelineRequest:
    item_id: str
    source: SourceSignature
    generation: int


@dataclass(frozen=True, slots=True)
class TimelineResult:
    request: TimelineRequest
    timeline: SourcePresentationTimeline | None
    error: str = ''


class TimelineIndexer(Protocol):
    def build(self, request: TimelineRequest, control: TimelineBuildControl) -> TimelineResult: ...
