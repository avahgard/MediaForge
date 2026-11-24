import QtQuick
import "."

Item {
    id: root
    objectName: "cropOverlay"
    required property var workspace
    required property var videoOutput
    required property var contentHost
    readonly property var settings: workspace.editState
    property rect displayed: Qt.rect(contentHost.x + videoOutput.x + videoOutput.contentRect.x,
                                               contentHost.y + videoOutput.y + videoOutput.contentRect.y,
                                               videoOutput.contentRect.width,
                                               videoOutput.contentRect.height)
    readonly property real scaleX: displayed.width / Math.max(1, settings.sourceWidth || 1)
    readonly property real scaleY: displayed.height / Math.max(1, settings.sourceHeight || 1)
    readonly property bool editAllowed: workspace.cropEditAllowed
    visible: editAllowed && displayed.width > 0 && displayed.height > 0
    enabled: visible
    property real startPointerX: 0
    property real startPointerY: 0
    property var startRect: ({})
    property string dragMode: "move"
    property var handleHost: root

    readonly property var handleModels: [
        { ax: 0, ay: 0, mode: "left-top" }, { ax: 1, ay: 0, mode: "right-top" },
        { ax: 0, ay: 1, mode: "left-bottom" }, { ax: 1, ay: 1, mode: "right-bottom" },
        { ax: 0, ay: .5, mode: "left" }, { ax: 1, ay: .5, mode: "right" },
        { ax: .5, ay: 0, mode: "top" }, { ax: .5, ay: 1, mode: "bottom" }
    ]
    function beginDrag(point, mode, fromHandle) {
        if (!editAllowed || !enabled) return false
        // A boundary handle's generous hit area straddles the source edge.
        // Body drags still reject letterbox clicks.
        if (!fromHandle && (point.x < displayed.x || point.x > displayed.x + displayed.width
                || point.y < displayed.y || point.y > displayed.y + displayed.height)) return false
        forceActiveFocus()
        startPointerX = point.x; startPointerY = point.y
        startRect = { x: settings.cropX, y: settings.cropY,
                      width: settings.cropWidth, height: settings.cropHeight }
        dragMode = mode
        return true
    }
    function updateDrag(point) {
        if (!editAllowed || !enabled) return
        workspace.dragCrop(dragMode, startRect.x, startRect.y, startRect.width, startRect.height,
                           (point.x - startPointerX) / scaleX, (point.y - startPointerY) / scaleY)
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onPressed: root.forceActiveFocus()
    }

    Rectangle {
        x: root.displayed.x; y: root.displayed.y
        width: root.displayed.width
        height: Math.max(0, cropRect.y - y)
        color: "#000000"; opacity: 0.44
    }
    Rectangle {
        x: root.displayed.x; y: cropRect.y
        width: Math.max(0, cropRect.x - x)
        height: cropRect.height
        color: "#000000"; opacity: 0.44
    }
    Rectangle {
        x: cropRect.x + cropRect.width; y: cropRect.y
        width: Math.max(0, root.displayed.x + root.displayed.width - x)
        height: cropRect.height
        color: "#000000"; opacity: 0.44
    }
    Rectangle {
        x: root.displayed.x; y: cropRect.y + cropRect.height
        width: root.displayed.width
        height: Math.max(0, root.displayed.y + root.displayed.height - y)
        color: "#000000"; opacity: 0.44
    }

    Rectangle {
        id: cropRect
        objectName: "cropOverlayRect"
        x: root.displayed.x + (root.settings.cropX || 0) * root.scaleX
        y: root.displayed.y + (root.settings.cropY || 0) * root.scaleY
        width: (root.settings.cropWidth || 0) * root.scaleX
        height: (root.settings.cropHeight || 0) * root.scaleY
        color: "transparent"
        radius: 8
        border.color: "#A85BFF"
        border.width: 2
        MouseArea {
            anchors.fill: parent
            anchors.margins: -5
            hoverEnabled: true
            cursorShape: Qt.SizeAllCursor
            function mode(x, y) {
                var left = x < 14, right = x > cropRect.width - 14
                var top = y < 14, bottom = y > cropRect.height - 14
                if (left && top) return "left-top"
                if (right && top) return "right-top"
                if (left && bottom) return "left-bottom"
                if (right && bottom) return "right-bottom"
                if (left) return "left"
                if (right) return "right"
                if (top) return "top"
                if (bottom) return "bottom"
                return "move"
            }
            onPressed: function(mouse) {
                var point = mapToItem(root, mouse.x, mouse.y)
                mouse.accepted = root.beginDrag(point, mode(mouse.x, mouse.y))
            }
            onPositionChanged: function(mouse) {
                if (!pressed) return
                var point = mapToItem(root, mouse.x, mouse.y)
                root.updateDrag(point)
            }
        }
    }
    Repeater {
        model: root.handleModels
        Item {
            id: handle
            required property var modelData
            required property int index
            objectName: "cropHandle-" + modelData.mode
            parent: root.handleHost
            width: 24; height: 24
            // Keep the visible circle and hit area centered on the actual anchor.
            // A separate Viewer layer lets edge markers extend past video clipping.
            readonly property point anchorPoint: root.mapToItem(root.handleHost,
                cropRect.x + modelData.ax * cropRect.width,
                cropRect.y + modelData.ay * cropRect.height)
            x: anchorPoint.x - width / 2; y: anchorPoint.y - height / 2
            visible: root.visible && anchorPoint.x >= 5 && anchorPoint.y >= 5
                     && anchorPoint.x <= root.handleHost.width - 5
                     && anchorPoint.y <= root.handleHost.height - 5
            // Reparented handles do not inherit the overlay's enabled state.
            enabled: root.enabled && visible
            Rectangle {
                objectName: "cropHandleCircle-" + handle.modelData.mode
                anchors.centerIn: parent
                width: 10; height: 10; radius: 5
                color: "#A85BFF"
            }
            MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton
                cursorShape: modelData.mode === "left" || modelData.mode === "right" ? Qt.SizeHorCursor
                             : modelData.mode === "top" || modelData.mode === "bottom" ? Qt.SizeVerCursor
                             : modelData.mode === "left-top" || modelData.mode === "right-bottom" ? Qt.SizeFDiagCursor : Qt.SizeBDiagCursor
                onPressed: function(mouse) { mouse.accepted = root.beginDrag(mapToItem(root, mouse.x, mouse.y), handle.modelData.mode, true) }
                onPositionChanged: function(mouse) { if (pressed) root.updateDrag(mapToItem(root, mouse.x, mouse.y)) }
            }
        }
    }
}
