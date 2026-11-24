import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import "."

FocusScope {
    id: root
    required property var workspace
    property bool compact: false
    property var navigation: null
    readonly property bool canMutate: navigation ? navigation.canImport : workspace.canOpen
    readonly property bool mediaMode: !navigation || navigation.page === "Media"
    readonly property bool minimal: height < 100
    function selectItem(id) { return navigation ? navigation.selectSource(id) : workspace.selectMedia(id) }
    function removeItem(id) { return navigation ? navigation.removeSource(id) : workspace.removeMedia(id) }
    activeFocusOnTab: true
    function activeIndex() { return workspace.mediaItems.findIndex(function(item) { return item.id === workspace.activeMediaId }) }
    property string revealId: ""
    // Keep keyed delegates alive across selection notifications. A fresh QVariantList
    // is a snapshot, not a reason to reset Flickable's geometry/content position.
    ListModel { id: entries }
    function syncEntries() {
        var rows = workspace.mediaItems
        var offset = trayList.contentX
        var structural = false
        for (var i = 0; i < rows.length; ++i) {
            var found = i
            while (found < entries.count && entries.get(found).itemId !== rows[i].id) ++found
            var row = {itemId: rows[i].id, itemName: rows[i].name,
                       itemDuration: rows[i].duration, itemResolution: rows[i].resolution, itemActive: rows[i].active}
            if (found === entries.count) { entries.insert(i, row); structural = true }
            else {
                if (found !== i) { entries.move(found, i, 1); structural = true }
                var old = entries.get(i)
                if (old.itemName !== row.itemName || old.itemDuration !== row.itemDuration ||
                    old.itemResolution !== row.itemResolution || old.itemActive !== row.itemActive)
                    entries.set(i, row)
            }
        }
        if (entries.count > rows.length) { entries.remove(rows.length, entries.count - rows.length); structural = true }
        if (structural) { trayList.cancelFlick(); trayList.forceLayout() }
        trayList.currentIndex = activeIndex()
        var retained = Math.max(0, Math.min(offset, Math.max(0, trayList.contentWidth - trayList.width)))
        if (Math.abs(trayList.contentX - retained) > .01) trayList.contentX = retained
        if (revealId && workspace.activeMediaId === revealId) {
            trayList.positionViewAtIndex(trayList.currentIndex, ListView.Contain)
            revealId = ""
        }
    }
    Connections { target: root.workspace; function onWorkspaceChanged() { root.syncEntries() } }
    Component.onCompleted: syncEntries()
    function navigate(delta) {
        if (!workspace.mediaItems.length) { revealId = ""; return }
        var intent = workspace.mediaItems.findIndex(function(item) { return item.id === root.revealId })
        var target = Math.max(0, Math.min(workspace.mediaItems.length - 1, (intent >= 0 ? intent : activeIndex()) + delta))
        if (target >= 0) {
            revealId = workspace.mediaItems[target].id
            if (!root.selectItem(revealId)) revealId = ""
        }
    }
    Keys.onLeftPressed: navigate(-1)
    Keys.onRightPressed: navigate(1)
    Keys.onReturnPressed: root.selectItem(workspace.activeMediaId)
    Keys.onMenuPressed: openActiveContext()
    signal addRequested()
    signal replaceRequested(string itemId)
    property string contextId: ""
    property int contextIndex: -1
    Keys.onDeletePressed: function(event) {
        event.accepted = root.removeItem(workspace.activeMediaId)
    }
    function openContextAt(index, card, localX) {
        if (!card || index < 0 || index >= workspace.mediaItems.length || !canMutate) return
        contextIndex = index
        contextId = workspace.mediaItems[index].id
        var overlay = Overlay.overlay
        var top = card.mapToItem(overlay, localX, 0)
        var bottom = card.mapToItem(overlay, localX, card.height)
        var above = top.y - contextMenu.height - 4
        contextMenu.x = Math.max(8, Math.min(top.x, overlay.width - contextMenu.width - 8))
        contextMenu.y = Math.max(8, Math.min(above >= 8 ? above : bottom.y + 4,
                                             overlay.height - contextMenu.height - 8))
        contextMenu.open()
    }
    function openActiveContext() {
        var index = workspace.mediaItems.findIndex(function(item) { return item.id === workspace.activeMediaId })
        if (index < 0) return
        trayList.positionViewAtIndex(index, ListView.Contain)
        openContextAt(index, trayList.currentItem, 0)
    }

    Label {
        id: title
        visible: !root.minimal
        text: "Media"
        color: Theme.textDisabled
        font.pixelSize: 12
        font.weight: Font.DemiBold
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.topMargin: 0
    }
    Rectangle {
        objectName: "mediaTraySurface"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.topMargin: root.minimal ? 0 : 26
        anchors.bottom: parent.bottom
        radius: 22
        color: Qt.rgba(247/255,249/255,252/255,0.025)
        border.color: Theme.border
        ListView {
            id: trayList
            objectName: "mediaTrayList"
            anchors.left: parent.left
            anchors.right: root.compact ? trayNavigation.left : addButton.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.margins: 10
            orientation: ListView.Horizontal
            spacing: 10
            clip: true
            onMovementStarted: root.revealId = ""
            model: entries
            highlightFollowsCurrentItem: false
            highlightMoveDuration: 0
            highlightResizeDuration: 0
            delegate: Rectangle {
                id: trayItem
                objectName: "trayItem" + index
                activeFocusOnTab: false
                required property string itemId
                required property string itemName
                required property string itemDuration
                required property string itemResolution
                required property bool itemActive
                required property int index
                width: Math.min(238, trayList.width)
                height: trayList.height
                radius: 16
                color: itemActive ? Qt.rgba(90/255,169/255,1,0.085) : Qt.rgba(247/255,249/255,252/255,0.022)
                border.color: itemActive ? (root.activeFocus ? Theme.accent : Theme.accentBorder) : Theme.border
                Behavior on color { ColorAnimation { duration: Theme.motionNormal } }
                Rectangle {
                    id: thumbnail
                    x: 8; y: 8; width: root.minimal ? 42 : 64; height: parent.height - 16
                    radius: 12; color: "#202632"
                }
                Column {
                    x: thumbnail.x + thumbnail.width + 12; y: root.minimal || root.compact ? 10 : 18
                    width: parent.width - x - 28
                    spacing: root.minimal ? 4 : 8
                    Label {
                        width: parent.width
                        text: itemName
                        elide: Text.ElideMiddle
                        color: Theme.textPrimary
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                    }
                    Label {
                        width: parent.width
                        text: itemDuration + " · " + itemResolution
                        elide: Text.ElideRight
                        color: Theme.textMuted
                        font.pixelSize: 11
                    }
                }
                Rectangle {
                    visible: itemActive
                    x: parent.width - 20; y: 14
                    width: 8; height: 8; radius: 4
                    color: Theme.accent
                }
                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    enabled: root.canMutate
                    onClicked: function(mouse) {
                        root.revealId = ""
                        root.forceActiveFocus()
                        if (mouse.button === Qt.RightButton) {
                            root.openContextAt(index, trayItem, mouse.x)
                        } else root.selectItem(itemId)
                    }
                }
            }
        }
        Item {
            id: trayNavigation; visible: root.compact; width: visible ? 66 : 0
            anchors.right: addButton.left; anchors.top: parent.top; anchors.bottom: parent.bottom
            Label { y: 8; width: parent.width; horizontalAlignment: Text.AlignHCenter; text: (root.activeIndex()+1)+" / "+entries.count; color: Theme.textSecondary; font.pixelSize: 11 }
            IconButton { objectName: "trayPrevious"; x: 0; y: parent.height-36; width: 32; height: 32; glyphName: "previous"; accessibleText: "Previous source"; enabled: root.canMutate && root.activeIndex()>0; onClicked: root.navigate(-1) }
            IconButton { objectName: "trayNext"; x: 32; y: parent.height-36; width: 32; height: 32; glyphName: "next"; accessibleText: "Next source"; enabled: root.canMutate && root.activeIndex()<entries.count-1; onClicked: root.navigate(1) }
        }
        IconButton {
            id: addButton
            objectName: "mediaTrayAdd"
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.margins: 10
            width: root.minimal ? 48 : 68
            glyphName: "add"
            accessibleText: "Add media"
            enabled: root.canMutate
            hoverEnabled: true
            padding: 0
            onClicked: root.addRequested()
            Keys.onReturnPressed: clicked()
            Keys.onEnterPressed: clicked()
            background: Rectangle {
                radius: 16
                color: addButton.down ? Theme.selectionStrong : addButton.hovered ? Theme.selection
                       : Qt.rgba(247/255,249/255,252/255,0.02)
                border.color: addButton.visualFocus ? Theme.accent
                              : addButton.hovered ? Theme.borderStrong : Theme.border
            }
        }
    }
    Basic.Menu {
        id: contextMenu
        objectName: "mediaContextMenu"
        parent: Overlay.overlay
        width: 176
        height: root.mediaMode ? 172 : 76
        padding: 3
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onClosed: if (root.visible) root.forceActiveFocus()
        background: Rectangle {
            radius: 16
            color: Theme.popupSurface
            border.color: Theme.border
        }
        Repeater {
                model: root.mediaMode ? ["Replace source…", "Remove", "Duplicate", "Move left", "Move right"] : ["Replace source…", "Remove"]
                Basic.MenuItem {
                    id: menuAction
                    objectName: "context" + modelData.replace(/ /g, "")
                    required property string modelData
                    readonly property bool possible: modelData === "Move left" ? root.contextIndex > 0 :
                                                     modelData === "Move right" ? root.contextIndex < root.workspace.mediaItems.length - 1 : true
                    enabled: possible && root.canMutate
                    implicitHeight: 32
                    text: modelData
                    background: Rectangle {
                        radius: 8
                        color: menuAction.highlighted ? Theme.selectionStrong : "transparent"
                    }
                    contentItem: Label {
                        leftPadding: 12
                        verticalAlignment: Text.AlignVCenter
                        text: modelData
                        color: Theme.textPrimary
                        opacity: possible ? 0.72 : 0.28
                        font.pixelSize: 12
                    }
                    onTriggered: {
                            if (modelData === "Replace source…") root.replaceRequested(root.contextId)
                            else if (modelData === "Remove") root.removeItem(root.contextId)
                            else if (modelData === "Duplicate") root.workspace.duplicateMedia(root.contextId)
                            else if (modelData === "Move left") root.workspace.moveMedia(root.contextId, -1)
                            else root.workspace.moveMedia(root.contextId, 1)
                        contextMenu.close()
                    }
                }
            }
    }
}
