import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import "."

Label {
    id: root
    property bool progressActive: false
    property bool errorActive: false
    property string requestKey: ""
    property string details: ""
    property string inlineError: ""
    property bool delayed: false
    property string waitingKey: ""

    function resetWait() {
        delay.stop()
        delayed = false
        waitingKey = requestKey
        if (progressActive) delay.start()
    }
    onProgressActiveChanged: resetWait()
    onRequestKeyChanged: resetWait()
    Component.onCompleted: resetWait()
    Timer {
        id: delay
        interval: 750
        onTriggered: root.delayed = root.progressActive && root.waitingKey === root.requestKey
    }
    // Text is details only, never a progress/error classifier. One continuously
    // stalled request can show feedback; routine frame generations reset the wait.
    visible: errorActive || (progressActive && delayed)
    text: errorActive ? (inlineError || "Preview unavailable") : "Preparing…"
    width: errorActive && inlineError ? Math.min(implicitWidth, 440, parent.width - 44) : implicitWidth
    wrapMode: Text.Wrap
    maximumLineCount: 2
    elide: Text.ElideRight
    padding: 8
    color: Theme.textSecondary
    font.pixelSize: 11
    background: Rectangle { radius: 10; color: Theme.fullscreenTransport }
    Accessible.description: details
    Accessible.role: Accessible.Button
    Accessible.name: errorActive ? "Preview unavailable. Show details" : text
    Accessible.onPressAction: statusDetails.open()
    // Details are explicit interaction, never an unsolicited hover popup.
    MouseArea { anchors.fill: parent; onClicked: statusDetails.open() }
    Basic.Popup {
        id: statusDetails
        objectName: "previewStatusDetails"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(440, parent.width - 40)
        padding: 18
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { radius: 14; color: Theme.popupSurface; border.color: Theme.border }
        contentItem: Label { text: root.details; wrapMode: Text.Wrap; color: Theme.textPrimary; font.pixelSize: 12 }
    }
    onVisibleChanged: if (!visible) statusDetails.close()
}
