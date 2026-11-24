"""Windows shell identity for the Qt window hosted by private CPython."""

import ctypes
from ctypes import wintypes
from uuid import UUID

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QPlatformSurfaceEvent


APP_ID = "Avahgard.MediaForge"


class PropertyKey(ctypes.Structure):
    _fields_ = [("format_id", ctypes.c_ubyte * 16), ("id", wintypes.DWORD)]


class PropertyValue(ctypes.Structure):
    # Native PROPVARIANT on Windows x64. The payload union is 16 bytes.
    _fields_ = [
        ("type", wintypes.WORD), ("reserved", wintypes.WORD * 3),
        ("string", wintypes.LPWSTR), ("unused", ctypes.c_void_p),
    ]


def set_shell_identity():
    shell = ctypes.windll.shell32
    shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
    shell.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def _write_shell_properties(window_id, properties, logger):
    shell = ctypes.windll.shell32
    iid = (ctypes.c_ubyte * 16).from_buffer_copy(UUID("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99").bytes_le)
    format_id = (ctypes.c_ubyte * 16).from_buffer_copy(UUID("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3").bytes_le)
    store = ctypes.c_void_p()
    get_store = shell.SHGetPropertyStoreForWindow
    get_store.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    result = get_store(window_id, ctypes.byref(iid), ctypes.byref(store))
    if result < 0:
        logger.warning("Windows relaunch metadata unavailable (%#010x)", result & 0xffffffff)
        return

    methods = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    release = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(methods[2])
    set_value = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PropertyKey), ctypes.POINTER(PropertyValue)
    )(methods[6])
    try:
        for property_id, text in properties:
            key = PropertyKey(format_id, property_id)
            value = PropertyValue(type=31, string=text) if text is not None else PropertyValue()
            result = set_value(store, ctypes.byref(key), ctypes.byref(value))
            if result < 0:
                logger.warning("Windows relaunch property %d unavailable (%#010x)", property_id, result & 0xffffffff)
                break
    finally:
        release(store)


class _RelaunchProperties(QObject):
    def __init__(self, window, properties, logger):
        super().__init__(window)
        self.window_id = window.winId()
        self.properties = properties
        self.logger = logger
        _write_shell_properties(self.window_id, properties, logger)
        window.installEventFilter(self)

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.PlatformSurface
                and event.surfaceEventType() == QPlatformSurfaceEvent.SurfaceEventType.SurfaceAboutToBeDestroyed):
            # Clear the native properties while the original HWND still exists.
            _write_shell_properties(self.window_id, ((key, None) for key, _ in reversed(self.properties)), self.logger)
            watched.removeEventFilter(self)
        return False


def set_shell_relaunch(window, executable, logger):
    """Give Windows the outer launcher as the Qt window's pin and relaunch target."""
    properties = (
        (2, f'"{executable}"'), (3, f'{executable},0'), (4, "MediaForge"), (5, APP_ID),
    )
    window._mediaforge_relaunch_properties = _RelaunchProperties(window, properties, logger)
