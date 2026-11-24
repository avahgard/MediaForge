import QtQuick
import QtQuick.Controls.Basic as Basic

Flickable {
    id: viewport
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    flickableDirection: Flickable.VerticalFlick
    contentWidth: width
    readonly property real end: Math.max(0, contentHeight - height)
    property bool scrollbar: true
    property bool focusScrollbar: false
    property real savedPosition: 0
    property bool restoring: false

    onContentYChanged: if (visible && !restoring && end > 0) savedPosition = contentY
    onVisibleChanged: {
        if (visible) {
            restoring = true
            Qt.callLater(restorePosition)
        } else {
            cancelFlick()
        }
    }

    function restorePosition() {
        moveTo(savedPosition)
        restoring = false
    }

    function moveTo(position) {
        contentY = Math.max(0, Math.min(end, position))
    }

    function reveal(item) {
        const top = item.mapToItem(contentItem, 0, 0).y
        if (top < contentY)
            moveTo(top)
        else if (top + item.height > contentY + height)
            moveTo(Math.min(top, top + item.height - height))
    }

    Keys.onUpPressed: moveTo(contentY - 44)
    Keys.onDownPressed: moveTo(contentY + 44)
    Keys.onPressed: event => {
        switch (event.key) {
        case Qt.Key_PageUp: moveTo(contentY - height); break
        case Qt.Key_PageDown: moveTo(contentY + height); break
        case Qt.Key_Home: moveTo(0); break
        case Qt.Key_End: moveTo(end); break
        default: return
        }
        event.accepted = true
    }
    Basic.ScrollBar.vertical: Basic.ScrollBar {
        id: scrollBar
        width: 16
        leftPadding: 6; rightPadding: 6
        visible: viewport.scrollbar && viewport.end > 0
        policy: viewport.scrollbar ? Basic.ScrollBar.AsNeeded : Basic.ScrollBar.AlwaysOff
        focusPolicy: viewport.focusScrollbar && viewport.end > 0 ? Qt.StrongFocus : Qt.NoFocus
        Accessible.name: "Vertical scroll bar"
        background: Rectangle {
            radius: 5
            color: "transparent"
            border.width: scrollBar.visualFocus ? 2 : 0
            border.color: Theme.accentBright
        }
        contentItem: Rectangle {
            implicitWidth: 4
            implicitHeight: 20
            radius: 2
            color: Theme.light ? Theme.fieldBorder : Theme.textSecondary
        }
    }
}
