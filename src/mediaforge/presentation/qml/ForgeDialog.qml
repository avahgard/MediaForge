import QtQuick
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import "."

Basic.Dialog {
    id: control
    property string confirmText: "OK"
    modal: true
    padding: 20
    background: Rectangle { radius: Theme.radiusPanel; color: Theme.popupSurface; border.color: Theme.borderStrong }
    header: Basic.Label {
        text: control.title; color: Theme.textPrimary; font.pixelSize: 18; font.weight: Font.DemiBold
        padding: 20; wrapMode: Text.Wrap
    }
    footer: RowLayout {
        spacing: 12
        Item { Layout.fillWidth: true }
        ForgeButton { text: "Cancel"; onClicked: control.reject() }
        ForgeButton { text: control.confirmText; primary: true; onClicked: control.accept() }
        Item { implicitWidth: 8 }
    }
}
