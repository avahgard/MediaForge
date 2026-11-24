import QtQuick
import QtQuick.Controls
import "."

// The approved Media SVG 00 Home, shared by both source tabs. No import owner.
Item {
    id: root
    property bool importEnabled: true
    property var dropValidator
    property var dropHandler
    property string statusText: ""
    signal browseRequested()
    objectName: "sharedSourceHome"
    Item {
        anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
        height: Math.min(parent.height, Theme.headerHeight + Theme.spacePanel + 520)
        Item {
            x: 0; y: 12; width: parent.width; height: Theme.headerHeight
            Label { y: 26 - baselineOffset; text: "MediaForge"; color: Theme.homeText; font.pixelSize: 24; font.weight: Font.DemiBold }
            Label { y: 52 - baselineOffset; text: "Fast media processing without project overhead."; color: Theme.homeSecondary; font.pixelSize: 13 }
        }
        SourceImport {
            objectName: "homeOpenMediaSurface"
            x: 0; y: 102; width: parent.width
            height: Math.min(520, Math.max(280, parent.height - y))
            importEnabled: root.importEnabled
            dropValidator: root.dropValidator; dropHandler: root.dropHandler
            onBrowseRequested: root.browseRequested()
        }
    }
    Label {
        objectName: "homeImportStatus"
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.bottomMargin: 8
        visible: text.length > 0; text: root.statusText
        color: Theme.textSecondary; font.pixelSize: 13; wrapMode: Text.Wrap
    }
}
