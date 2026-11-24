import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import "."

Item {
    id: root
    required property var workspace
    property bool compact: false
    readonly property var state: workspace.editState
    readonly property bool showHeading: height >= 80

    Label {
        visible: root.showHeading
        text: "Operations"
        color: Theme.textDisabled
        font.pixelSize: 12
        font.weight: Font.DemiBold
    }
    Label {
        visible: root.showHeading
        anchors.right: parent.right
        text: root.state.activeCount + " active"
        color: Theme.textMuted
        font.pixelSize: 11
    }

    Rectangle {
        id: surface
        objectName: "operationSurface"
        y: root.showHeading ? (root.compact ? 24 : 30) : 0
        width: parent.width
        height: root.compact ? 56 : 62
        radius: 20
        color: Qt.rgba(247/255, 249/255, 252/255, 0.025)
        border.color: Theme.border

        Row {
            id: operationButtons
            objectName: "operationsViewport"
            readonly property int inset: root.compact ? 6 : 8
            readonly property real columnWidth: (surface.width - 2 * inset - 3 * spacing) / 4
            x: inset
            y: inset
            height: surface.height - 2*inset
            spacing: root.compact ? 6 : 8

            Repeater {
                model: ["Trim", "Crop", "Reformat", "Output"]
                Rectangle {
                    id: pill
                    required property string modelData
                    objectName: "operation" + modelData
                    readonly property string key: modelData === "Reformat" ? "resize" : modelData.toLowerCase()
                    readonly property bool selected: root.workspace.selectedOperation === key
                    readonly property bool enabledOperation: !!root.state[key + "Enabled"]
                    readonly property bool toggleAllowed: key !== "output" && root.workspace.canOpen
                            && (root.workspace.sourceKind === "video"
                                || (key === "trim" ? ["audio", "sequence"].includes(root.workspace.sourceKind)
                                                   : root.workspace.sourceKind !== "audio"))
                    width: operationButtons.columnWidth * (!root.compact ? 1
                           : key === "resize" ? 1.16 : key === "output" ? 0.84 : 1)
                    height: operationButtons.height
                    radius: 15
                    color: selected ? Theme.accentSoft : pillHover.hovered ? Theme.selection : Qt.rgba(247/255, 249/255, 252/255, 0.025)
                    border.color: toggleButton.visualFocus || selectButton.visualFocus ? Theme.accent
                                  : selected ? Theme.accentBorder : Theme.border
                    HoverHandler { id: pillHover }

                    function toggleEnabled() {
                        if (toggleAllowed)
                            root.workspace.setOperationEnabled(key, !enabledOperation)
                    }

                    Basic.Button {
                        id: toggleButton
                        objectName: "enable" + modelData
                        visible: pill.key !== "output"
                        enabled: pill.toggleAllowed
                        x: 7
                        anchors.verticalCenter: parent.verticalCenter
                        width: 26
                        height: 26
                        z: 1
                        padding: 0
                        Accessible.name: "Enable " + modelData
                        Accessible.checkable: true
                        Accessible.checked: pill.enabledOperation
                        background: Item {}
                        contentItem: Item {
                            Rectangle {
                                anchors.centerIn: parent
                                width: 16
                                height: 16
                                radius: 8
                                color: pill.enabledOperation ? Theme.accentSoft : "transparent"
                                border.color: Theme.accent
                                opacity: pill.toggleAllowed ? 1 : 0.45
                                Label {
                                    anchors.centerIn: parent
                                    visible: pill.enabledOperation
                                    text: "✓"
                                    color: Theme.accentBright
                                    font.pixelSize: 11
                                }
                            }
                        }
                        onClicked: pill.toggleEnabled()
                        Keys.onReturnPressed: pill.toggleEnabled()
                        Keys.onEnterPressed: pill.toggleEnabled()
                    }

                    Basic.Button {
                        id: selectButton
                        objectName: "select" + modelData
                        anchors.fill: parent
                        leftPadding: pill.key === "output" ? 12 : 36
                        rightPadding: 5
                        Accessible.name: "Edit " + modelData
                        background: Item {}
                        contentItem: Label {
                            text: modelData
                            color: Theme.textPrimary
                            opacity: 0.85
                            font.pixelSize: 13
                            font.weight: pill.selected ? Font.DemiBold : Font.Medium
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                        }
                        onClicked: root.workspace.selectOperation(pill.key)
                        Keys.onReturnPressed: root.workspace.selectOperation(pill.key)
                        Keys.onEnterPressed: root.workspace.selectOperation(pill.key)
                    }

                    MouseArea {
                        anchors.fill: parent
                        z: 2
                        acceptedButtons: Qt.RightButton
                        onClicked: pill.toggleEnabled()
                    }
                }
            }
        }
    }
}
