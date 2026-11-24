import QtQuick
import QtQuick.Controls.Basic as Basic
import "."

Rectangle {
    id: selector
    required property var labels
    required property int selected
    property bool tabs: false
    property int focusIndex: selected
    signal chosen(int index)

    implicitWidth: buttons.implicitWidth + 8
    implicitHeight: 44
    radius: Theme.radiusControl
    color: Theme.viewerFrame
    border.color: Theme.border
    Accessible.role: tabs ? Accessible.PageTabList : Accessible.Grouping
    Accessible.name: tabs ? "Settings tabs" : "Theme"
    onSelectedChanged: focusIndex = selected

    function focusSelection() {
        focusIndex = selected
        entries.itemAt(selected).forceActiveFocus(Qt.TabFocusReason)
    }

    function navigate(index) {
        focusIndex = (index + labels.length) % labels.length
        entries.itemAt(focusIndex).forceActiveFocus(Qt.TabFocusReason)
        if (!tabs)
            chosen(focusIndex)
    }

    Row {
        id: buttons
        anchors.centerIn: parent
        spacing: 4
        Repeater {
            id: entries
            model: selector.labels
            Basic.Button {
                id: choice
                required property int index
                required property string modelData
                readonly property bool selected: index === selector.selected
                objectName: (selector.tabs ? "settingsTab" : "settingsTheme") + modelData.replace(" ", "")
                width: selector.tabs ? Math.max(74, choiceLabel.implicitWidth + 40) : 98
                height: 36
                focusPolicy: index === selector.focusIndex ? Qt.StrongFocus : Qt.ClickFocus
                Accessible.role: selector.tabs ? Accessible.PageTab : Accessible.RadioButton
                Accessible.name: modelData
                Accessible.selected: selector.tabs && selected
                Accessible.checkable: !selector.tabs
                Accessible.checked: !selector.tabs && selected
                onClicked: selector.chosen(index)
                Keys.onLeftPressed: selector.navigate(index - 1)
                Keys.onRightPressed: selector.navigate(index + 1)
                Keys.onPressed: event => {
                    if (event.key === Qt.Key_Home || event.key === Qt.Key_End) {
                        selector.navigate(event.key === Qt.Key_Home ? 0 : selector.labels.length - 1)
                        event.accepted = true
                    }
                }
                Keys.onReturnPressed: selector.chosen(index)
                Keys.onEnterPressed: selector.chosen(index)
                contentItem: Text {
                    id: choiceLabel
                    text: choice.modelData
                    color: choice.selected ? Theme.textPrimary : Theme.textSecondary
                    font.family: Theme.fontFamily
                    font.pixelSize: 13
                    font.weight: choice.selected ? Font.DemiBold : Font.Normal
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    radius: 12
                    color: choice.down ? Theme.selectionStrong : choice.selected || choice.hovered ? Theme.selection : "transparent"
                    border.color: choice.visualFocus ? Theme.accentBright : choice.selected ? Theme.border : "transparent"
                    border.width: choice.visualFocus ? 2 : 1
                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: 4
                        width: 16; height: 2; radius: 1
                        visible: choice.selected
                        color: Theme.accentBright
                    }
                }
            }
        }
    }
}
