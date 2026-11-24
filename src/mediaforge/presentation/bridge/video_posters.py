"""Source-locked native posters for presentation, never decoder truth."""
from collections import OrderedDict
from pathlib import Path
import os
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat
from mediaforge.application.contracts.source_timeline import SourceSignature


class VideoPosters:
    limit = 2
    byte_limit = 128 * 1024 * 1024

    def __init__(self, open_source, admit_readback=None):
        self.open_source = open_source
        self.admit_readback = admit_readback
        self.last_capture = None
        self.entries = OrderedDict()
        self.active = None

    @property
    def reserved(self):
        return sum(v['bytes'] for v in self.entries.values())

    def activate(self, path):
        # A refused candidate must not detach the still-active source's poster
        # updates. Commit active identity only after locking/admission succeeds.
        signature = SourceSignature.capture(path)
        # A hit still owns a deny-write/delete handle and its parent path locks.
        if signature not in self.entries:
            guard = self.open_source(path)
            try:
                if SourceSignature.capture(path) != signature:
                    raise ValueError('Video source changed during poster admission.')
                while len(self.entries) >= self.limit:
                    self._drop(next(iter(self.entries)))
                self.entries[signature] = dict(guard=guard, frame=QVideoFrame(),
                    index=None, timeline=None, bytes=0, detached=False)
            except BaseException:
                guard.close()
                raise
        if self.active != signature:
            self._detach_active_picture()
        self.entries.move_to_end(signature)
        self.active = signature

    def _detach_active_picture(self):
        entry = self.entries.get(self.active)
        if entry is None or entry['detached'] or not entry['frame'].isValid():
            return
        # A shallow QVideoFrame can hold the old decoder's allocation even
        # with NoHandle. Retain only an independently allocated native poster.
        frame = entry['frame']
        entry.update(frame=QVideoFrame(), bytes=0)
        self.remember(self.active, frame, entry['index'], entry['timeline'], entry['offset'], capture_gpu=True)

    def drop_decoder_backing(self):
        entry = self.entries.get(self.active)
        if entry is not None and not entry['detached']:
            entry.update(frame=QVideoFrame(), bytes=0, index=None, timeline=None)

    def detached_picture(self, path, position_ms):
        entry = self.entries.get(SourceSignature.capture(path))
        return self.picture(path, position_ms) if entry is not None and entry['detached'] else None

    @staticmethod
    def _copy_layout(frame):
        fmt = QVideoFrameFormat.PixelFormat
        width, height = frame.width(), frame.height()
        chroma_width, chroma_height = (width+1)//2, (height+1)//2
        if frame.pixelFormat() == fmt.Format_NV12:
            return [(height, width), (chroma_height, 2*chroma_width)]
        if frame.handleType() != QVideoFrame.NoHandle:
            return []  # Only NV12 hardware readback is qualified.
        if frame.pixelFormat() in (fmt.Format_YUV420P, fmt.Format_YV12):
            return [(height, width), (chroma_height, chroma_width), (chroma_height, chroma_width)]
        image_format = QVideoFrameFormat.imageFormatFromPixelFormat(frame.pixelFormat())
        if image_format == QImage.Format_Invalid:
            return []
        depth = QImage(1, 1, image_format).depth()
        return [(height, (width*depth+7)//8)] if depth else []

    def remember(self, signature, frame, index, timeline, offset=0, *, capture_gpu=False):
        if signature != self.active or signature not in self.entries or index is None or timeline is None:
            return
        entry_owner=self.entries[signature]
        if (not frame.isValid()
                or not 0 < frame.width() <= 3840 or not 0 < frame.height() <= 2160):
            return
        if capture_gpu:
            if self.admit_readback is None:return
            layout=self._copy_layout(frame)
            if not layout:return
            import time
            started=time.perf_counter();pixels=frame.width()*frame.height()
            # Existing four display/pending V slots cover two CPU NV12 posters,
            # one mapped transfer and one destination. Pressure still gates the
            # full conservative temporary overlap before map/allocation.
            try:self.admit_readback(16*pixels+2*1024**2)
            except ValueError as error:
                self.last_capture=dict(refused=str(error));return
            if self.active!=signature or self.entries.get(signature) is not entry_owner:return
            original=QVideoFrame(frame)
            if not original.map(QVideoFrame.ReadOnly):return
            retained=None
            try:
                if original.planeCount()!=len(layout) or sum(original.mappedBytes(i) for i in range(len(layout)))>self.byte_limit//2:return
                retained=QVideoFrame(frame.surfaceFormat())
                if not retained.map(QVideoFrame.WriteOnly):return
                try:
                    if retained.planeCount()!=len(layout) or sum(retained.mappedBytes(i) for i in range(len(layout)))>self.byte_limit//2:return
                    for i,(rows,row_bytes) in enumerate(layout):
                        source=original.bits(i);dest=retained.bits(i);ss=original.bytesPerLine(i);ds=retained.bytesPerLine(i)
                        if min(ss,ds)<row_bytes or len(source)<ss*rows or len(dest)<ds*rows:return
                        if ss==ds:dest[:ds*rows]=source[:ss*rows]
                        else:
                            for y in range(rows):dest[y*ds:y*ds+row_bytes]=source[y*ss:y*ss+row_bytes]
                finally:retained.unmap()
            finally:original.unmap()
            retained.setStartTime(frame.startTime());retained.setEndTime(frame.endTime())
            retained.setRotation(frame.rotation());retained.setMirrored(frame.mirrored())
            self.last_capture=dict(milliseconds=(time.perf_counter()-started)*1000,width=frame.width(),height=frame.height(),format=frame.pixelFormat().name,peak=16*pixels+2*1024**2)
            frame=retained
        elif frame.handleType() != QVideoFrame.NoHandle:
            return
        # Read-only native mapping measures the real CPU planes/strides without
        # reading back a GPU texture or creating an additional pixel raster.
        retained=QVideoFrame(frame)
        if not retained.map(QVideoFrame.ReadOnly):return
        try:size=sum(retained.mappedBytes(i) for i in range(retained.planeCount()))
        finally:retained.unmap()
        if not 0 < size <= self.byte_limit//2:return
        if self.active!=signature or self.entries.get(signature) is not entry_owner:return
        entry = entry_owner
        entry.update(frame=retained, index=index, timeline=timeline, bytes=size, offset=offset, detached=capture_gpu)

    def picture(self, path, position_ms):
        signature = SourceSignature.capture(path)
        entry = self.entries.get(signature)
        if entry is None or not entry['frame'].isValid():
            return None
        timeline = entry['timeline']
        index = timeline.index_at_time(position_ms*1000-entry['offset'])
        if index != entry['index']:
            return None  # A pending seek must not relabel a different poster.
        self.entries.move_to_end(signature)
        return QVideoFrame(entry['frame'])

    def request_offset(self, path, position_ms):
        if self.picture(path,position_ms) is None:
            return None
        return self.entries[SourceSignature.capture(path)]['offset']

    def _drop(self, signature):
        entry = self.entries[signature]
        # Keep the owner reachable if native lock retirement fails.
        entry['guard'].close()
        entry['frame'] = QVideoFrame()
        del self.entries[signature]

    def forget(self,path):
        key=os.path.normcase(str(Path(path).resolve()))
        for signature in list(self.entries):
            if signature.canonical_path==key:
                if self.active==signature:self.active=None
                self._drop(signature)

    def close(self):
        self.active = None
        for signature in list(self.entries):
            self._drop(signature)
