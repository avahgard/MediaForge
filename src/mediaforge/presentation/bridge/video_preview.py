"""One cancellable preview job; activation leaves source/export identity unchanged."""
from concurrent.futures import ThreadPoolExecutor
import logging
from PySide6.QtCore import QObject,Signal,Slot,Qt


class VideoPreviewController(QObject):
    finished=Signal(object,object,object)
    changed=Signal()

    def __init__(self,service,model):
        super().__init__(model);self.service=service;self.model=model
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='video-preview')
        self.control=None;self.future=None;self.current=None;self.pending=None;self.serial=0;self.closed=False
        self.restore=None;self.quality='Auto';self.active_quality='Auto';self.status=''
        self.finished.connect(self._done,Qt.QueuedConnection)
        model.playback.changed.connect(self._ready)

    def cancel(self):
        self.serial+=1
        self.pending=None
        if self.control:self.control.cancel()
        self.status=''
        self.changed.emit()

    def source_changed(self):
        self.cancel();self.restore=None
        if self.current is not None:
            self.service.retire(self.current);self.current=None
        self.quality=self.active_quality='Auto';self.status='';self.changed.emit()

    def request(self,quality):
        item=self.model._workspace.active
        if quality not in ('Auto','1:1','1:2','1:4') or item is None or item.metadata.audio_only:return False
        if self.current is not None and quality==f'1:{self.current.divisor}':
            self.cancel();self.quality=self.active_quality=quality;self.status='';self.changed.emit();return True
        self.cancel();self.quality=quality
        if quality in ('Auto','1:1'):
            if self.current is not None:self._activate(item,None)
            self.active_quality=quality;self.changed.emit();return True
        self.pending=(self.serial,item,quality)
        self.status='Preparing video preview…';self.changed.emit()
        self._start();return True

    def _start(self):
        if self.future is not None or self.pending is None:return
        serial,item,quality=self.pending;self.pending=None
        self.control=self.service.new_control()
        future=self.pool.submit(self.service.prepare,item.metadata,int(quality[-1]),self.control);self.future=future
        def done(job):
            try:value,error=job.result(),None
            except Exception as failure:value,error=None,str(failure);failure.__traceback__=None
            self.finished.emit((serial,item,quality),value,error)
        future.add_done_callback(done)

    @Slot(object,object,object)
    def _done(self,request,value,error):
        self.future=None;self.control=None
        serial,item,quality=request
        if self.closed or serial!=self.serial or self.model._workspace.active is not item:
            if value:self.service.discard(value)
        elif value is None:
            self._refused(quality,error)
        elif not self.model.canOpen:
            self.service.discard(value)
            self._refused(quality,'Media or source controls changed while preparing the preview. Choose a tier again in Media.')
        else:
            try:
                self._activate(item,value)
                self.active_quality=quality
                if not self.service.gate.failure:self.status=''
            except Exception as failure:
                self.status=str(failure)
                from pathlib import Path
                if self.model.playback._source==Path(value.path):
                    self.current=value
                    self.service.gate.quarantine(self.service,'Video preview activation/retirement ownership unresolved.')
                else:
                    self.service.discard(value)
                    self._refused(quality,str(failure))
        self.changed.emit();self._start()

    def _refused(self, quality, error):
        logging.getLogger(__name__).warning("Reduced preview %s refused: %s", quality, error)
        messages = {
            'start_failed': 'The managed preview worker could not start.',
            'process_failed': 'The managed preview worker could not process this file.',
            'timeout': 'The managed preview worker timed out.',
            'lifecycle_failed': 'Preview worker retirement is unresolved.',
        }
        reason = messages.get(error, error or 'No preview picture was produced.')
        self.quality = self.active_quality
        self.status = f'{reason} Using {self.active_quality}.'

    def _activate(self,item,value):
        playback=self.model.playback;old=self.current
        target=playback.position;playing=playback.playing
        request_offset=(playback._timeline_offset if playback._origin_established else playback._requested_offset_hint)
        poster = None
        if playback.posters is not None:
            poster = playback.posters.detached_picture(item.metadata.source_path, target)
            playback.posters.drop_decoder_backing()
        playback.set_source(value.path if value else item.metadata.source_path, poster=poster,
                            logical_source=item.metadata.source_path)
        playback.configure_timeline(item.id,item.metadata.source_path)
        self.model._sync_trim_loop();self.current=value
        playback.restore_session(target,playing=playing,request_offset=request_offset)
        self.restore=None
        if old:
            try:self.service.retire(old)
            except Exception as failure:self.status='Video preview retirement failed. '+str(failure)
        self._ready()

    @Slot()
    def _ready(self):
        if (self.restore is None or not self.model.playback.canPlay
                or not self.model.playback.timelineAvailable):return
        target,playing=self.restore;self.restore=None
        self.model.playback.seek(target)
        if playing:self.model.playback.togglePlayback()

    def shutdown(self):
        self.closed=True;self.pending=None;self.cancel()
        if self.future:
            value=self.future.result(timeout=10) if not self.future.exception(timeout=10) else None
            if value and value is not self.current:self.service.discard(value)
        self.pool.shutdown(wait=True,cancel_futures=True)
        self.service.close()
