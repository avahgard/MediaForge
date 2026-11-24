import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import "."

// Match the accepted Media Output Inspector choice presentation.
Basic.ComboBox {
    id: choice
    Layout.fillWidth: true
    Layout.minimumWidth: 0
    padding: 12
    rightPadding: 28

    contentItem: Label {
        text: choice.displayText
        color: Theme.textPrimary
        font.pixelSize: 12
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 12
        color: Theme.surface
        border.color: choice.activeFocus ? Theme.accent : Theme.border
    }
    indicator: Item {
        x: choice.width - 24
        anchors.verticalCenter: parent.verticalCenter
        width: 12; height: 8
        Rectangle { x: 1; y: 2; width: 7; height: 1.5; rotation: 45; color: Theme.textSecondary }
        Rectangle { x: 5; y: 2; width: 7; height: 1.5; rotation: -45; color: Theme.textSecondary }
    }
    delegate: Basic.ItemDelegate {
        required property int index
        enabled: !choice.textRole || choice.model[index].eligible !== false
        width: choice.width
        text: choice.textRole ? choice.model[index][choice.textRole] : choice.model[index]
        contentItem: Label { text: parent.text; color: Theme.textPrimary; font.pixelSize: 12 }
        background: Rectangle { color: parent.highlighted ? Theme.selection : Theme.popupSurface }
        highlighted: choice.highlightedIndex === index
    }
    popup: Basic.Popup {
        objectName: choice.objectName + "Popup"
        parent: choice
        margins: 8
        width: choice.width
        height: Math.min(240, contentItem.implicitHeight + 12)
        padding: 6
        y: choice.height + 4
        function anchorToField() {
            var overlay = choice.Overlay.overlay
            if (!overlay) return
            var origin = choice.mapToItem(overlay, 0, 0)
            var below = overlay.height - origin.y - choice.height
            y = below >= height + 12 || below >= origin.y ? choice.height + 4 : -height - 4
        }
        onAboutToShow: anchorToField()
        onHeightChanged: if (visible) anchorToField()
        onClosed: if (choice.visible) choice.forceActiveFocus()
        background: Rectangle { radius: 12; color: Theme.popupSurface; border.color: Theme.border }
        contentItem: ListView {
            objectName: choice.objectName + "Choices"
            implicitHeight: contentHeight
            model: choice.popup.visible ? choice.delegateModel : null
            currentIndex: choice.highlightedIndex
            clip: true
        }
    }
    onVisibleChanged: if (!visible) popup.close()
}
