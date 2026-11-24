import QtQuick
import QtQuick.Controls.Basic as Basic
import "."

Basic.TextField {
    id: field
    required property var workspace
    property bool numericField: true
    property string wheelPlace: ""
    property bool wheelApplying: false
    signal scrubbed()
    onCursorPositionChanged: if (!wheelApplying) wheelPlace = ""
    onSelectionStartChanged: if (!wheelApplying) wheelPlace = ""
    onSelectionEndChanged: if (!wheelApplying) wheelPlace = ""
    onActiveFocusChanged: wheelPlace = ""
    selectByMouse: true
    Accessible.name: objectName
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.NoButton
        hoverEnabled: true
        onContainsMouseChanged: field.wheelPlace = ""
        onWheel: function(wheel) {
            wheel.accepted = false
            if (!field.numericField || !field.activeFocus || field.readOnly || !field.enabled || !containsMouse) return
            var delta = wheel.angleDelta.y > 0 ? 1 : wheel.angleDelta.y < 0 ? -1 : 0
            var result = workspace.scrubNumber(field.text, field.cursorPosition, field.selectionStart, field.selectionEnd, delta, field.wheelPlace)
            if (!result.text) return
            field.wheelApplying = true
            field.text = result.text
            if (result.start !== result.end) field.select(result.start, result.end)
            else field.cursorPosition = result.cursor
            field.wheelPlace = result.place
            field.scrubbed()
            field.wheelApplying = false
            wheel.accepted = true
        }
    }
}
