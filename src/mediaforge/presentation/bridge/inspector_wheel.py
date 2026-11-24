"""Route real non-scrubbing wheel input to the Inspector's Qt Flickable."""
from PySide6.QtCore import QObject, QEvent, QCoreApplication
from PySide6.QtGui import QWheelEvent
from PySide6.QtQuick import QQuickItem  # Register the pinned PySide QQuickItem* converter.
from mediaforge.presentation.bridge.numeric import scrub


class InspectorWheelRouting(QObject):
    def __init__(self, scroll, window, parent):
        super().__init__(parent)
        self._scroll, self._window = scroll, window
        scroll.destroyed.connect(lambda: setattr(self, '_scroll', None))
        window.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Wheel or watched is not self._window or self._scroll is None:
            return False
        view = self._scroll.property('contentItem')
        if not isinstance(view, QQuickItem) or not self._scroll.property('visible'):
            return False
        def descendants(item):
            yield item
            for child in item.childItems():
                yield from descendants(child)
        fields = [item for item in descendants(view)
                  if item.property('numericField') and item.property('visible')]
        hovered = next((item for item in fields
                        if item.contains(item.mapFromScene(event.position()))), None)
        if hovered is None:
            return False
        text = hovered.property('text')
        if (hovered.property('activeFocus') and not hovered.property('readOnly') and hovered.property('enabled')
                and scrub(text, hovered.property('cursorPosition'), hovered.property('selectionStart'),
                          hovered.property('selectionEnd'), 1)):
            return False  # The same focused+hovered field owns digit scrubbing.
        # Forward the actual wheel deltas/modifiers/phase to the normal scrolling
        # control. No content offset or scrollbar mutation substitutes for input.
        forwarded = QWheelEvent(view.mapFromScene(event.position()), event.globalPosition(),
            event.pixelDelta(), event.angleDelta(), event.buttons(), event.modifiers(),
            event.phase(), event.inverted(), event.source(), event.pointingDevice())
        forwarded.setTimestamp(event.timestamp())
        forwarded.setAccepted(False)
        QCoreApplication.sendEvent(view, forwarded)
        event.setAccepted(forwarded.isAccepted())
        return forwarded.isAccepted()
