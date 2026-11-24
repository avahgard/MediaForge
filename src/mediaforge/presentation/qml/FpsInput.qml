import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import "."

FocusScope {
    id: root
    implicitHeight: 40
    implicitWidth: 180
    property alias editText: input.text
    property alias text: input.text
    readonly property Item contentItem: input
    property string value: "24"
    property string mediaId: ""
    signal accepted()
    signal activated(int index)
    // Friendly text is presentation, never the authority for an unchanged rate.
    property string exactValue: "24"
    property string baselineText: "24"
    property string baselineExact: "24"
    property bool edited: false
    readonly property string submission: edited || input.text !== baselineText ? input.text : baselineExact
    function reset(display, exact) {
        baselineText = display; baselineExact = exact; edited = false; input.text = display
    }
    onValueChanged: if (!input.activeFocus) reset(value, exactValue)
    onExactValueChanged: reset(value, exactValue)
    onMediaIdChanged: { input.focus = false; reset(value, exactValue) }
    Rectangle { anchors.fill: parent; radius: 12; color: Theme.surface; border.color: root.activeFocus ? Theme.accent : Theme.borderStrong }
    Basic.TextField {
        id: input
        anchors.fill: parent
        rightPadding: 40; leftPadding: 12
        text: root.value
        font.pixelSize: 12; color: Theme.textPrimary
        selectByMouse: true; selectionColor: Theme.accent
        verticalAlignment: Text.AlignVCenter
        background: null
        onTextEdited: root.edited = true
        onAccepted: root.accepted()
        Accessible.name: "Frames per second; decimal or exact ratio"
    }
    Basic.ComboBox {
        id: presets
        objectName: "fpsPresetMenu"
        anchors.right: parent.right; height: parent.height; width: 34
        model: ["23.976", "24", "25", "29.97", "30", "50", "59.94", "60"]
        onActivated: {
            var exact = ["24000/1001", "24", "25", "30000/1001", "30", "50", "60000/1001", "60"]
            root.reset(currentText, exact[currentIndex]); root.activated(currentIndex)
        }
        contentItem: Item {}
        background: null
        indicator: Canvas {
            width: 12; height: 12; anchors.centerIn: parent
            readonly property color stroke: Theme.textSecondary
            onStrokeChanged: requestPaint()
            onPaint: { var c = getContext("2d"); c.reset(); c.strokeStyle = stroke; c.lineWidth = 1.5; c.beginPath(); c.moveTo(2,4); c.lineTo(6,8); c.lineTo(10,4); c.stroke() }
        }
        delegate: Basic.ItemDelegate {
            width: presets.popup.width; highlighted: presets.highlightedIndex === index
            contentItem: Label { text: modelData; color: Theme.textPrimary; font.pixelSize: 12 }
            background: Rectangle { radius: 8; color: parent.highlighted ? Theme.selection : Theme.popupSurface }
        }
        popup: Basic.Popup {
            y: root.height + 4; x: -root.width + presets.width
            width: root.width; padding: 6
            background: Rectangle { radius: 12; color: Theme.popupSurface; border.color: Theme.border }
            contentItem: ListView { implicitHeight: contentHeight; model: presets.popup.visible ? presets.delegateModel : null; currentIndex: presets.highlightedIndex; clip: true }
        }
        Accessible.name: "FPS presets"
    }
}
