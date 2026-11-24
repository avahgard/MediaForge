import QtQuick
import QtQuick.Controls.Basic as Basic
import "."

Basic.ProgressBar {
    id: control
    implicitHeight: 6
    property bool animateIndeterminate: true
    padding: 0
    background: Rectangle { implicitHeight: 6; color: Theme.sliderTrack; radius: 3 }
    contentItem: Item {
        clip: true
        Rectangle {
            id: fill
            width: control.indeterminate ? parent.width * 0.25 : parent.width * control.position
            height: parent.height; radius: 3; color: Theme.accent
            x: control.indeterminate && control.animateIndeterminate
               ? (parent.width + width) * control.phase - width : 0
        }
    }
    property real phase: 0
    NumberAnimation on phase {
        from: 0; to: 1; duration: 1400; loops: Animation.Infinite
        running: control.visible && control.indeterminate && control.animateIndeterminate
    }
}
