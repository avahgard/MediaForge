"""Shared processing admission for an injected disposable preview adapter."""
from mediaforge.features.media.processing_control import ProcessingControl


class VideoPreviewService:
    def __init__(self,adapter,gate):
        self.adapter,self.gate=adapter,gate

    @property
    def last_metrics(self):return self.adapter.last_metrics

    def new_control(self):return ProcessingControl(lambda:RuntimeError('Video preview canceled.'))

    def prepare(self,metadata,divisor,control):
        owner=self.gate.acquire()
        if owner is None:raise ValueError('Another media operation is running. Try quality again after it completes.')
        try:return self.adapter.prepare(metadata,divisor,control)
        finally:
            if self.adapter.retained:self.gate.poison(owner,'Video preview child/cleanup ownership unresolved.')
            else:self.gate.release(owner)

    def discard(self,value):
        try:self.adapter.discard(value)
        except BaseException:
            self.gate.quarantine(self.adapter,'Video preview retirement failed; files/handles retained.')
            raise

    def retire(self,value):
        try:self.adapter.release(value)
        except BaseException:
            self.gate.quarantine(self.adapter,'Video preview cache retirement failed; ownership retained.')
            raise

    def close(self):
        for value in tuple(self.adapter.entries):self.discard(value)
        self.adapter.close()
