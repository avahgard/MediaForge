"""Qt bindings for portable appearance, storage and package identity."""
from threading import Thread

from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl
from PySide6.QtGui import QDesktopServices

from mediaforge.infrastructure.filesystem.profile import local_path
from mediaforge.infrastructure.settings.storage import measure_storage
from mediaforge.presentation.bridge.models import size_text


class SettingsViewModel(QObject):
    themeChanged = Signal()
    storageChanged = Signal()
    messageChanged = Signal()
    _measurementReady = Signal(object)

    def __init__(self, paths, appearance, identity, version, *, portable=False, parent=None):
        super().__init__(parent)
        self._paths = paths
        self._data = paths.root.parent if portable else paths.root
        self._appearance = appearance
        self._identity = identity
        self._version = version
        self._theme = 'System'
        self._message = ''
        self._storage = {}
        self._measuring = False
        self._measurementReady.connect(self._finish_measurement)
        try:
            self._theme = appearance.load()['theme']
        except (OSError, ValueError, RuntimeError) as error:
            self._message = f'Appearance preference could not be read. {error}'

    @Property(str, notify=themeChanged)
    def theme(self):
        return self._theme

    @Property(str, notify=messageChanged)
    def message(self):
        return self._message

    @Slot(str)
    def setTheme(self, name):
        if name not in ('System', 'Dark', 'Light') or name == self._theme:
            return
        self._theme = name
        self.themeChanged.emit()
        try:
            self._appearance.save({'theme': name})
        except Exception as error:
            self._message = f'Theme changed for this session. The preference could not be saved. {error}'
        else:
            self._message = ''
        self.messageChanged.emit()

    @Property(str, constant=True)
    def dataPath(self):
        return str(self._data)

    @Property(str, constant=True)
    def logPath(self):
        return str(self._paths.logs)

    @Property('QVariantMap', constant=True)
    def about(self):
        info = self._identity
        build = (info.build if info.release else f'{info.build} development build') if info.build else (
            'Authenticated development package' if info.manifest else 'Development checkout')
        return dict(version=info.version or self._version, build=build,
            manifest=info.manifest, project=bool(info.project_url), notices=bool(info.notices))

    @Property('QVariantMap', notify=storageChanged)
    def storage(self):
        return self._storage

    @Slot()
    def refreshStorage(self):
        if self._measuring:
            return
        self._measuring = True
        def measure():
            self._measurementReady.emit(measure_storage(self._paths))
        Thread(target=measure, name='MediaForge storage measurement', daemon=True).start()

    @Slot(object)
    def _finish_measurement(self, snapshot):
        def describe(value):
            if value.state == 'unavailable':
                return 'Unavailable'
            return ('At least ' if value.state == 'partial' else '') + size_text(value.bytes)

        states = {snapshot.models.state, snapshot.cache.state, snapshot.available.state}
        status = ('Logical sizes' if states == {'complete'} else 'Measurement unavailable'
                  if states == {'unavailable'} else 'Some folders or values could not be measured')
        self._storage = dict(models=describe(snapshot.models), cache=describe(snapshot.cache),
                             available=describe(snapshot.available),
                             sampled=f'{status} · Measured at {snapshot.measured_at:%H:%M:%S %Z}')
        self._measuring = False
        self.storageChanged.emit()

    @Slot()
    def openData(self):
        try:
            folder = local_path(self._data)
            if folder == self._data and folder.is_dir() and QDesktopServices.openUrl(
                    QUrl.fromLocalFile(str(folder))):
                return
        except (OSError, ValueError):
            pass
        self._message = 'The portable Data folder could not be opened.'
        self.messageChanged.emit()

    @Slot()
    def openProject(self):
        if self._identity.project_url:
            QDesktopServices.openUrl(QUrl(self._identity.project_url))

    @Slot()
    def openNotices(self):
        if self._identity.notices:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._identity.notices)))
