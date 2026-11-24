import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

Rectangle {
    id: root
    property bool importEnabled: true
    property var dropValidator
    property var dropHandler
    signal browseRequested()
    implicitHeight: 520
    color: Theme.homeSurface
    radius: Theme.radiusContent
    border.color: drop.containsDrag ? Theme.accent : Qt.rgba(1, 1, 1, 0.07)
    DropArea {
        id: drop; objectName: root.objectName === "homeOpenMediaSurface" ? "homeDropArea" : "dubDropArea"; anchors.fill: parent
        onEntered: function(drag) { drag.accepted = root.importEnabled && drag.hasUrls && root.dropValidator && root.dropValidator(drag.urls) }
        onDropped: function(event) { if (root.importEnabled && event.hasUrls && root.dropHandler && root.dropHandler(event.urls)) event.acceptProposedAction() }
    }
    MouseArea { anchors.fill: parent; enabled: root.importEnabled; onClicked: root.browseRequested() }
    Item {
        anchors.centerIn: parent
        anchors.verticalCenterOffset: -36
        width: Math.min(parent.width - Theme.spaceSection * 2, 420)
        height: 232
        Rectangle {
            objectName: "homePlusIcon"
            anchors.horizontalCenter: parent.horizontalCenter
            width: 84; height: 84; radius: 42
            color: Theme.accentSoft; border.color: Theme.accentBorder
            Rectangle { anchors.centerIn: parent; width: 2.4; height: 36; radius: 1.2; color: Theme.accentBright; opacity: 0.9; antialiasing: true }
            Rectangle { anchors.centerIn: parent; width: 36; height: 2.4; radius: 1.2; color: Theme.accentBright; opacity: 0.9; antialiasing: true }
        }
        Label {
            y: 126 - baselineOffset; width: parent.width; height: 32
            horizontalAlignment: Text.AlignHCenter
            text: "Drop media here"; color: Theme.homeText
            font.pixelSize: 24; font.weight: Font.DemiBold
        }
        Label {
            y: 156 - baselineOffset; width: parent.width; height: 18
            horizontalAlignment: Text.AlignHCenter
            text: "or click to browse"; color: Theme.homeSecondary; font.pixelSize: 13
        }
        ForgeButton {
            id: chooseFile
            objectName: "openMediaButton"
            anchors.horizontalCenter: parent.horizontalCenter
            y: 186; width: 150; height: 46
            text: "Choose file"; primary: true; enabled: root.importEnabled
            Accessible.name: "Choose file"
            background: Rectangle {
                radius: 16; color: chooseFile.down ? Qt.darker(Theme.accent, 1.12) : chooseFile.hovered ? Qt.lighter(Theme.accent, 1.08) : Theme.accent
                opacity: chooseFile.enabled ? 1 : Theme.opacityDisabled
                border.color: chooseFile.activeFocus ? Theme.homeText : "transparent"
            }
            contentItem: Text { text: parent.text; color: "white"; font: parent.font; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            onClicked: root.browseRequested()
        }
    }
}
