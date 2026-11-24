import QtQuick

Rectangle {
    id: well
    required property string value
    required property string label
    signal focused()
    implicitHeight: Math.max(58, path.contentHeight + 20)
    radius: 12
    color: Theme.viewerFrame
    border.color: path.activeFocus ? Theme.accentBright : Theme.border
    border.width: path.activeFocus ? 2 : 1
    TextEdit {
        id: path
        objectName: "settingsPathText"
        anchors.fill: parent
        anchors.margins: 12
        text: well.value
        textFormat: TextEdit.PlainText
        wrapMode: TextEdit.Wrap
        readOnly: true
        selectByMouse: true
        activeFocusOnTab: true
        onActiveFocusChanged: if (activeFocus) well.focused()
        color: Theme.textPrimary
        selectionColor: Theme.primaryFill
        selectedTextColor: Theme.primaryLabel
        font.family: Theme.fontFamily
        font.pixelSize: 13
        Accessible.name: well.label
        Accessible.description: well.value
    }
}
