"""Follow fresh Qt system defaults using the one existing logical audio output."""
from PySide6.QtCore import QObject, Slot
from PySide6.QtMultimedia import QMediaDevices


class DefaultAudioDeviceFollower(QObject):
    def __init__(self, audio_output, logger, parent=None, *, devices=None):
        super().__init__(parent)
        self._audio = audio_output
        self._logger = logger
        self._devices = devices if devices is not None else QMediaDevices(self)
        self._applied_id = None
        self.pending = False
        self._closed = False
        self._devices.audioOutputsChanged.connect(self.refresh)
        self.refresh()

    @Slot()
    def refresh(self):
        if self._closed: return
        try:
            device = self._devices.defaultAudioOutput()
            if device.isNull() or not device.id():
                self.pending = True
                return
            identity = bytes(device.id())
            if identity != self._applied_id or self.pending:
                self._audio.setDevice(device)
                self._applied_id = identity
            self.pending = False
        except Exception:
            self.pending = True
            self._logger.warning('System default audio output could not be applied.')

    def shutdown(self):
        if self._closed: return
        self._closed = True
        try:
            self._devices.audioOutputsChanged.disconnect(self.refresh)
        except Exception:
            self._logger.warning('Default audio observer was already disconnected.')
