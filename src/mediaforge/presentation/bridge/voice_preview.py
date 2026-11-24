"""Explicit small PCM listening through QAudioSink; no second media player."""
from PySide6.QtCore import QObject,Property,Signal,Slot,QBuffer,QByteArray,QIODevice
from PySide6.QtMultimedia import QAudioFormat,QAudioSink,QMediaDevices,QAudio


class VoicePreviewPlayer(QObject):
    changed=Signal()

    def __init__(self,parent=None,*,converter=None):
        super().__init__(parent)
        self._sink=None;self._buffer=None;self._message='';self._stopping=False
        self._converter=converter

    @Property(bool,notify=changed)
    def playing(self):
        return self._sink is not None and self._sink.state() in (QAudio.State.ActiveState,QAudio.State.IdleState)

    @Property(str,notify=changed)
    def message(self):return self._message

    def play(self,fragment,*,volume=1.):
        self.stop();self._message=''
        if (fragment is None or fragment.sample_rate!=24000 or not 0<len(fragment.pcm16)<=8*24000*2
                or len(fragment.pcm16)%2):
            self._message='Generate the selected voice sample first.';self.changed.emit();return False
        device=QMediaDevices.defaultAudioOutput()
        format_=QAudioFormat();format_.setSampleRate(24000);format_.setChannelCount(1)
        format_.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if device.isNull():
            self._message='No audio output device is available. Connect an output device and retry Play.'
            self.changed.emit();return False
        data=fragment.pcm16
        if not device.isFormatSupported(format_):
            format_=device.preferredFormat()
            if self._converter is None or not device.isFormatSupported(format_):
                self._message='This output device cannot play the preview. Dub processing remains available.'
                self.changed.emit();return False
            try:data=self._converter(data,24000,format_.sampleRate(),format_.channelCount(),format_.sampleFormat().name)
            except Exception as error:
                self._message=str(error);self.changed.emit();return False
        self._playback_format=(format_.sampleRate(),format_.channelCount(),format_.sampleFormat().name)
        self._buffer=QBuffer(self);self._buffer.setData(QByteArray(data))
        self._buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self._sink=QAudioSink(device,format_,self)
        self._sink.setBufferSize(32768);self._sink.setVolume(max(0.,min(1.,volume)))
        self._sink.stateChanged.connect(self._state_changed)
        self._sink.start(self._buffer)
        if self._sink is None or self._sink.error()!=QAudio.Error.NoError:
            self._message='Preview audio output could not start.';self.stop();return False
        self.changed.emit();return True

    @Slot()
    def stop(self):
        if self._stopping:return
        self._stopping=True
        try:
            sink=self._sink
            if sink is not None:
                sink.stop()
                if sink.state()!=QAudio.State.StoppedState:
                    raise RuntimeError('Voice preview audio device did not stop; retain its buffer.')
                self._sink=None;sink.deleteLater()
            buffer=self._buffer
            if buffer is not None:
                buffer.close();self._buffer=None;buffer.deleteLater()
        finally:
            self._stopping=False;self.changed.emit()

    @Slot(QAudio.State)
    def _state_changed(self,state):
        if self._stopping:return
        if self._sink is not None:
            error=self._sink.error()
            if error not in (QAudio.Error.NoError,QAudio.Error.UnderrunError):
                self._message='Preview audio device failed. The Dub result is unaffected.'
            if state in (QAudio.State.IdleState,QAudio.State.StoppedState):self.stop()
        self.changed.emit()

    def shutdown(self):self.stop()
