import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import "."

Basic.Button {
    id: control
    property string glyphName: "play"
    property bool darkOverlay: false
    property real cornerRadius: glyphName === "play" || glyphName === "pause" || glyphName === "info" ? width / 2 : 12
    implicitWidth: 42
    implicitHeight: 42
    Accessible.name: accessibleText
    property string accessibleText: ""
    background: Rectangle {
        radius: control.cornerRadius
        color: control.darkOverlay ? (control.down ? Theme.fullscreenControlPressed : control.hovered ? Theme.fullscreenControlHover : Theme.fullscreenControl) : control.down ? Theme.selectionStrong : control.hovered ? Theme.selectionStrong : control.glyphName === "play" || control.glyphName === "pause" || control.glyphName === "info" || control.glyphName === "fullscreen" || control.glyphName === "exit" || control.glyphName === "folderOpen" ? Theme.selection : "transparent"
        border.color: control.darkOverlay ? (control.visualFocus ? Theme.fullscreenAccent : Theme.fullscreenBorder) : control.activeFocus ? Theme.accent : "transparent"
        border.width: control.activeFocus ? 2 : 1
        Behavior on color { ColorAnimation { duration: Theme.motionFast } }
    }
    // Controls sizes its contentItem to the hit area. Keep the vector canvas
    // independently sized inside that item so 22px coordinates stay centered.
    contentItem: Item {
        objectName: "iconBox"
        Canvas {
        id: glyph
        objectName: "iconGlyph"
        width: 22
        height: 22
        anchors.centerIn: parent
        readonly property color glyphColor: control.darkOverlay ? (control.enabled ? Theme.fullscreenText : Theme.fullscreenDisabled) : Theme.light
            ? (control.enabled ? Theme.textPrimary : Theme.textDisabled)
            : (control.enabled ? "#E6EBF4" : "#77808D")
        onGlyphColorChanged: requestPaint()
        onPaint: {
            var c = getContext("2d")
            c.clearRect(0, 0, width, height)
            c.save()
            c.strokeStyle = glyphColor
            c.fillStyle = c.strokeStyle
            c.lineWidth = 1.8
            c.lineCap = "round"
            c.lineJoin = "round"
            if (control.glyphName === "play") {
                c.beginPath(); c.moveTo(7.5, 4); c.lineTo(18.5, 11); c.lineTo(7.5, 18); c.closePath(); c.fill()
            } else if (control.glyphName === "pause") {
                c.fillRect(6, 4, 3, 14); c.fillRect(13, 4, 3, 14)
            } else if (control.glyphName === "mute" || control.glyphName === "volume") {
                c.translate(control.glyphName === "mute" ? -0.5 : 1, 0)
                c.beginPath(); c.moveTo(3, 9); c.lineTo(7, 9); c.lineTo(12, 5)
                c.lineTo(12, 17); c.lineTo(7, 13); c.lineTo(3, 13); c.closePath(); c.stroke()
                if (control.glyphName === "mute") {
                    c.beginPath(); c.moveTo(15, 7); c.lineTo(20, 16); c.moveTo(20, 7); c.lineTo(15, 16); c.stroke()
                } else {
                    c.beginPath(); c.arc(12, 11, 5, -0.7, 0.7); c.stroke()
                }
            } else if (control.glyphName === "fullscreen" || control.glyphName === "exit") {
                var inward = control.glyphName === "exit"
                c.beginPath()
                if (inward) {
                    // State 12's project-owned exit geometry, in a 22px glyph.
                    c.moveTo(1, 1); c.lineTo(11, 1); c.lineTo(11, 11)
                    c.moveTo(21, 1); c.lineTo(11, 1); c.lineTo(11, 11)
                    c.moveTo(1, 21); c.lineTo(11, 21); c.lineTo(11, 11)
                    c.moveTo(21, 21); c.lineTo(11, 21); c.lineTo(11, 11)
                } else {
                    c.moveTo(9, 3); c.lineTo(3, 3); c.lineTo(3, 9)
                    c.moveTo(13, 3); c.lineTo(19, 3); c.lineTo(19, 9)
                    c.moveTo(3, 13); c.lineTo(3, 19); c.lineTo(9, 19)
                    c.moveTo(19, 13); c.lineTo(19, 19); c.lineTo(13, 19)
                }
                c.stroke()
            } else if (control.glyphName === "info") {
                c.beginPath(); c.moveTo(11, 10); c.lineTo(11, 15); c.stroke()
                c.beginPath(); c.arc(11, 7, 1, 0, Math.PI * 2); c.fill()
            } else if (control.glyphName === "folderOpen") {
                c.beginPath(); c.moveTo(3, 17); c.lineTo(3, 5); c.lineTo(8, 5)
                c.lineTo(10, 7); c.lineTo(18, 7); c.lineTo(18, 9); c.stroke()
                c.beginPath(); c.moveTo(3, 17); c.lineTo(6, 10); c.lineTo(20, 10)
                c.lineTo(17, 17); c.closePath(); c.stroke()
            } else if (control.glyphName === "previous" || control.glyphName === "next") {
                var direction = control.glyphName === "previous" ? -1 : 1
                c.beginPath(); c.moveTo(11-direction*3,5); c.lineTo(11+direction*3,11); c.lineTo(11-direction*3,17); c.stroke()
            } else if (control.glyphName === "add") {
                c.beginPath(); c.moveTo(11, 4); c.lineTo(11, 18)
                c.moveTo(4, 11); c.lineTo(18, 11); c.stroke()
            }
            c.restore()
        }
        }
        Connections {
            target: control
            function onGlyphNameChanged() { glyph.requestPaint() }
        }
    }
}
