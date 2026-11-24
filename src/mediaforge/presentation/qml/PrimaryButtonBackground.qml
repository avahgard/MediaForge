import QtQuick
import "."

Rectangle {
    required property var button
    property bool inspector: false
    property color surround: Theme.surface
    radius: Theme.radiusControl
    color: Theme.light ? !button.enabled ? Theme.primaryDisabledFill : button.down ? Theme.primaryPressedFill
                         : button.hovered ? Theme.primaryHoverFill : Theme.primaryFill
                       : !inspector ? Theme.accent : button.down ? Theme.primaryPressedFill
                         : button.hovered ? Theme.primaryHoverFill : Theme.accent
    opacity: Theme.light ? 1 : button.enabled ? (inspector ? Theme.opacityPrimary : 1) : Theme.opacityDisabled
    border.width: Theme.light ? 0 : inspector ? 1 : button.activeFocus ? 2 : 1
    border.color: button.activeFocus ? (inspector ? Theme.textPrimary : Theme.accentBright)
                                    : inspector ? "transparent" : Theme.border
    Rectangle {
        anchors.fill: parent
        anchors.margins: -4
        radius: parent.radius + 4
        color: "transparent"
        border.width: 2
        border.color: Theme.primaryFocusRing
        visible: Theme.light && parent.button.enabled && parent.button.visualFocus
        Rectangle {
            anchors.fill: parent
            anchors.margins: 2
            radius: parent.radius - 2
            color: "transparent"
            border.width: 2
            border.color: parent.parent.surround
        }
    }
}
