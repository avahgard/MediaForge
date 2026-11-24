import QtQuick
import QtQuick.Controls.Basic as Basic
import "."

Basic.Button {
    id: control
    property bool primary: false
    property color surroundingSurface: Theme.surface
    Accessible.name: text
    implicitHeight: 44
    leftPadding: 18; rightPadding: 18
    font.family: Theme.fontFamily
    font.pixelSize: 13; font.weight: Font.DemiBold
    contentItem: Text {
        text: control.text; font: control.font
        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        color: control.primary ? (!control.enabled && Theme.light ? Theme.primaryDisabledLabel : Theme.primaryLabel)
                               : Theme.textPrimary
        opacity: control.enabled || control.primary && Theme.light ? 1 : Theme.opacityDisabled
    }
    background: Item {
        PrimaryButtonBackground {
            visible: control.primary
            anchors.fill: parent
            button: control
            surround: control.surroundingSurface
        }
        Rectangle {
            anchors.fill: parent
            visible: !control.primary
            radius: Theme.radiusControl
            color: control.down ? Theme.selectionStrong : Theme.selection
            opacity: control.enabled ? 1 : Theme.opacityDisabled
            border.color: control.activeFocus ? Theme.accentBright : Theme.border
            border.width: control.activeFocus ? 2 : 1
        }
    }
}
