import QtQuick
import QtQuick.Window
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import QtMultimedia
import "."

ApplicationWindow {
    id: window
    objectName: "mediaforgeWindow"
    visible: true
    title: "MediaForge"
    width: Theme.windowWidth
    height: Theme.windowHeight
    minimumWidth: Theme.minimumWidth
    minimumHeight: Theme.minimumHeight
    // Bound the tested desktop extent, independently of the preview raster.
    maximumWidth: mediaViewModel.playback.imageMode ? Math.floor(3840 / Screen.devicePixelRatio) : 16777215
    maximumHeight: mediaViewModel.playback.imageMode ? Math.floor(2160 / Screen.devicePixelRatio) : 16777215
    color: Theme.background
    font.family: Theme.fontFamily
    palette.window: Theme.surface
    palette.windowText: Theme.textPrimary
    palette.base: Theme.viewerFrame
    palette.alternateBase: Theme.selection
    palette.text: Theme.textPrimary
    palette.button: Theme.selection
    palette.buttonText: Theme.textPrimary
    palette.placeholderText: Theme.textSecondary
    palette.highlight: Theme.primaryFill
    palette.highlightedText: Theme.primaryLabel
    Component.onCompleted: { mediaViewModel.windowPresentation.attach(window); mediaViewModel.attachImageWindow(window) }
    readonly property bool compactWorkspace: width < 1200
    readonly property string activePage: typeof appNavigation !== "undefined" ? appNavigation.page : "Media"
    property bool settingsVisited: false
    onActivePageChanged: if (activePage === "Settings") settingsVisited = true
    readonly property bool compactNavigation: false
    // One geometry owner for both modes, interpolating approved logical canvases.
    function blend(value, low, mid, high, a, b, c) {
        return value <= mid ? a + Math.max(0, Math.min(1, (value-low)/(mid-low))) * (b-a)
                            : b + Math.max(0, Math.min(1, (value-mid)/(high-mid))) * (c-b)
    }
    readonly property real sidebarSize: blend(width,800,1024,1440,76,140,190)
    readonly property real mainX: blend(width,800,1024,1440,112,180,242)
    readonly property real inspectorSize: blend(width,800,1024,1440,280,292,342)
    readonly property real columnGap: blend(width,800,1024,1440,16,18,24)
    readonly property real rightInset: blend(width,800,1024,1440,18,18,28)
    readonly property real mainWidth: width-mainX-inspectorSize-columnGap-rightInset
    readonly property real traySize: blend(height,560,768,900,78,134,126)
    readonly property real trayY: height-18-traySize
    readonly property real contextSize: blend(height,560,768,900,62,88,92)
    readonly property real contextY: trayY-blend(height,560,768,900,12,12,18)-contextSize
    readonly property real transportSize: blend(height,560,768,900,64,70,70)
    readonly property real transportY: contextY-blend(height,560,768,900,12,12,20)-transportSize
    readonly property var popupOverlay: contentItem.Overlay.overlay
    function openSettings(fromDub) {
        if (typeof appNavigation === "undefined")
            return
        if (fromDub && settingsLoader.item)
            settingsLoader.item.selectedTab = 1
        appNavigation.navigate("Settings")
    }
    Item { id: shortcutFocus; objectName: "workspaceKeyboardFocus"; focus: true }

    function editingText() {
        var focused = window.activeFocusItem
        return focused && (focused.cursorPosition !== undefined
                           || focused.objectName === "reformatScaleSlider"
                           || focused.objectName === "resetCropButton"
                           || focused.objectName === "revealSourceButton")
    }

    function viewerShortcutsAllowed(allowFocusedControl) {
        return (activePage === "Media" || activePage === "Dub") && mediaViewModel.state === "loaded" && !editingText()
               && (allowFocusedControl || !controlOwnsKeys())
               && (!popupOverlay || !popupOverlay.visible) && !mediaFileDialog.visible
    }

    function controlOwnsKeys() {
        var item = window.activeFocusItem
        return item && (item.down !== undefined || item.currentIndex !== undefined
                        || item.from !== undefined || item.objectName === "mediaTray")
    }

    function enterViewerFullscreen() {
        if (mediaViewModel.state !== "loaded" || mediaViewModel.playback.fullscreen)
            return
        metadataPopup.close()
        mediaViewModel.windowPresentation.enter(window)
        shortcutFocus.forceActiveFocus()
    }

    function exitViewerFullscreen() {
        mediaViewModel.windowPresentation.exit()
        shortcutFocus.forceActiveFocus()
    }

    component PrimaryButton: Basic.Button {
        id: control
        implicitHeight: Theme.navigationHeight
        leftPadding: Theme.spacePanel
        rightPadding: Theme.spacePanel
        font.pixelSize: Theme.fontBody
        font.weight: Theme.weightStrong
        contentItem: Text {
            text: control.text
            color: Theme.textPrimary
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            font: control.font
        }
        background: Rectangle {
            radius: Theme.radiusControl
            color: control.down ? Qt.darker(Theme.accent, 1.12)
                                : control.hovered ? Qt.lighter(Theme.accent, 1.08)
                                                  : Theme.accent
            opacity: control.enabled ? Theme.opacityPrimary : Theme.opacityDisabled
            border.color: control.activeFocus ? Theme.textPrimary : "transparent"
            border.width: Theme.strokeWidth
            Behavior on color { ColorAnimation { duration: Theme.motionFast } }
        }
    }

    component GhostButton: Basic.Button {
        id: control
        implicitHeight: 38
        leftPadding: Theme.spaceMedium
        rightPadding: Theme.spaceMedium
        font.pixelSize: Theme.fontBody
        font.weight: Theme.weightStrong
        contentItem: Text {
            text: control.text
            color: Theme.textPrimary
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            font: control.font
        }
        background: Rectangle {
            radius: Theme.radiusControl
            color: control.down ? Theme.selectionStrong
                                : control.hovered ? Theme.selection : "transparent"
            border.color: control.activeFocus ? Theme.accent : Theme.border
            border.width: Theme.strokeWidth
            Behavior on color { ColorAnimation { duration: Theme.motionFast } }
        }
    }

    FileDialog {
        id: mediaFileDialog
        objectName: "mediaFileDialog"
        property string replacementId: ""
        title: replacementId ? "Replace source" : qsTr("Open Media")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("Media files (*)")]
        onAccepted: {
            if (typeof appNavigation !== "undefined") {
                if (replacementId) appNavigation.replaceSource(replacementId,selectedFile)
                else appNavigation.openSource(selectedFile)
            } else mediaViewModel.openMedia(selectedFile)
            replacementId=""
        }
        onRejected: { replacementId=""; mediaViewModel.cancelSelection() }
    }

    Basic.Dialog {
        id: sequenceConfirm
        objectName: "sequenceConfirmationDialog"
        popupType: Popup.Item
        title: "Confirm Image Sequence"
        modal: true
        anchors.centerIn: parent
        width: Math.min(460, window.width - 40)
        padding: 24
        background: Rectangle { radius: 24; color: Theme.popupSurface; border.color: Theme.borderStrong }
        header: Label { text: "Image Sequence"; color: Theme.textPrimary; font.pixelSize: 20; font.weight: Font.DemiBold; padding: 24; bottomPadding: 8 }
        Basic.Overlay.modal: Rectangle { color: Qt.rgba(0.02,0.03,0.05,0.72) }
        footer: RowLayout {
            spacing: 12
            Item { Layout.fillWidth: true }
            Basic.Button { text: "Cancel"; onClicked: sequenceConfirm.reject(); background: Rectangle { radius: 12; color: Theme.selection } contentItem: Label { text: parent.text; padding: 12; color: Theme.textPrimary } }
            Basic.Button { objectName: "confirmSequenceButton"; text: "Confirm FPS"; onClicked: sequenceConfirm.accept(); background: Rectangle { radius: 12; color: Theme.accent } contentItem: Label { text: parent.text; padding: 12; color: Theme.background; font.weight: Font.DemiBold } }
            Item { Layout.preferredWidth: 12 }
        }
        property var facts: mediaViewModel.sequenceConfirmation
        onFactsChanged: if (facts.count && !visible) { sequenceFps.reset(facts.fps, facts.exactFps); open() }
        onAccepted: {
            if (!mediaViewModel.confirmSequence(sequenceFps.submission)) Qt.callLater(sequenceConfirm.open)
        }
        onRejected: mediaViewModel.cancelSequenceConfirmation()
        contentItem: ColumnLayout {
            Label { Layout.fillWidth: true; color: Theme.textSecondary; wrapMode: Text.WrapAnywhere; font.pixelSize: 12; text: "Folder: " + (sequenceConfirm.facts.directory || "") }
            Label { Layout.fillWidth: true; color: Theme.textSecondary; wrapMode: Text.Wrap; font.pixelSize: 12; text: sequenceConfirm.facts.pixelClass || "" }
            Label { Layout.fillWidth: true; color: Theme.textPrimary; wrapMode: Text.Wrap; text: (sequenceConfirm.facts.pattern || "") + "\nFirst / last: " + sequenceConfirm.facts.first + " / " + sequenceConfirm.facts.last + "\nCount: " + sequenceConfirm.facts.count + " · " + sequenceConfirm.facts.resolution + "\nGap Policy: " + sequenceConfirm.facts.gap }
            Label { Layout.fillWidth: true; color: Theme.textSecondary; wrapMode: Text.Wrap; text: sequenceConfirm.facts.authority || "" }
            FpsInput {
                id: sequenceFps
                objectName: "sequenceFpsConfirmation"
                Layout.fillWidth: true
                Accessible.name: "Frames per second (1–120)"
            }
            Label { text: "FPS · 1–120 · enter a value such as 24 or 23.9"; color: Theme.textSecondary; font.pixelSize: 12 }
            Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: sequenceConfirm.facts.error || ""; color: Theme.error }
        }
    }

    Shortcut {
        sequence: "Escape"
        enabled: mediaViewModel.playback.fullscreen && (!window.popupOverlay || !window.popupOverlay.visible) && !mediaFileDialog.visible
        onActivated: window.exitViewerFullscreen()
    }
    Shortcut { sequence: "Space"; enabled: window.viewerShortcutsAllowed(); onActivated: mediaViewModel.playback.togglePlayback() }
    Shortcut { sequence: "Left"; enabled: window.activePage === "Media" && mediaViewModel.state === "loaded" && !tray.activeFocus && !window.editingText() && !window.controlOwnsKeys(); onActivated: mediaViewModel.playback.seekRelative(-5000) }
    Shortcut { sequence: "Right"; enabled: window.activePage === "Media" && mediaViewModel.state === "loaded" && !tray.activeFocus && !window.editingText() && !window.controlOwnsKeys(); onActivated: mediaViewModel.playback.seekRelative(5000) }
    Shortcut { sequence: "Shift+Left"; enabled: (window.activePage === "Media" || window.activePage === "Dub") && mediaViewModel.state === "loaded" && !window.editingText() && !window.controlOwnsKeys(); onActivated: mediaViewModel.playback.stepFrame(-1) }
    Shortcut { sequence: "Shift+Right"; enabled: (window.activePage === "Media" || window.activePage === "Dub") && mediaViewModel.state === "loaded" && !window.editingText() && !window.controlOwnsKeys(); onActivated: mediaViewModel.playback.stepFrame(1) }
    Shortcut { sequence: "I"; enabled: window.activePage === "Media" && mediaViewModel.state === "loaded" && !window.editingText(); onActivated: mediaViewModel.setTrimInAtPlayhead(false) }
    Shortcut { sequence: "O"; enabled: window.activePage === "Media" && mediaViewModel.state === "loaded" && !window.editingText(); onActivated: mediaViewModel.setTrimOutAtPlayhead(false) }
    Shortcut { sequence: "Shift+I"; enabled: window.activePage === "Media" && mediaViewModel.state === "loaded" && !window.editingText(); onActivated: mediaViewModel.setTrimInAtPlayhead(true) }
    Shortcut { sequence: "Shift+O"; enabled: window.activePage === "Media" && mediaViewModel.state === "loaded" && !window.editingText(); onActivated: mediaViewModel.setTrimOutAtPlayhead(true) }
    Shortcut { sequence: "F"; enabled: window.viewerShortcutsAllowed(true); onActivated: mediaViewModel.viewerViewport.fit() }
    Shortcut { sequence: "Delete"; enabled: window.activePage === "Media" && mediaViewModel.state === "loaded" && tray.activeFocus && !window.editingText(); onActivated: mediaViewModel.removeMedia(mediaViewModel.activeMediaId) }

    Item {
        id: normalWorkspace
        objectName: "normalWorkspaceChrome"
        anchors.fill: parent
        visible: !mediaViewModel.playback.fullscreen
        Rectangle {
            objectName: "mediaSidebar"
            visible: true
            x: 18; y: 18; width: window.sidebarSize; height: window.height-36
            radius: Theme.radiusPanel
            color: Theme.sidebar
            border.color: Theme.border
            border.width: Theme.strokeWidth

            Label {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.margins: window.compactWorkspace ? 12 : Theme.spaceMedium
                text: window.width < 960 ? "MF" : "MediaForge"
                color: Theme.textPrimary
                font.pixelSize: window.compactWorkspace ? 16 : Theme.fontBrand
                font.weight: Theme.weightStrong
            }
            Column {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.leftMargin: Theme.spaceSmall
                anchors.rightMargin: Theme.spaceSmall
                anchors.topMargin: Theme.navigationTop
                spacing: Theme.spaceSmall
                Repeater {
                    model: ["Media", "Dub"]
                    Basic.Button {
                        required property string modelData
                        objectName: "navigation" + modelData
                        width: parent.width; height: Theme.navigationHeight
                        leftPadding: window.width < 960 ? 7 : 17; rightPadding: 7
                        text: modelData
                        Accessible.name: modelData
                        onClicked: if (typeof appNavigation !== "undefined") appNavigation.navigate(modelData)
                        background: Rectangle {
                            radius: Theme.radiusControl
                            color: parent.modelData === window.activePage ? Theme.selection : hoverFill
                            property color hoverFill: parent.hovered ? Theme.accentSoft : "transparent"
                            border.color: parent.visualFocus ? Theme.accentBright : "transparent"
                            Behavior on hoverFill { ColorAnimation { duration: Theme.motionFast } }
                        }
                        contentItem: RowLayout {
                            spacing: window.width < 960 ? 0 : 15
                            Rectangle { visible: window.width >= 960; implicitWidth: visible ? 14 : 0; implicitHeight: 14; radius: 7; color: modelData === window.activePage ? Theme.accent : Theme.textMuted }
                            Label { Layout.fillWidth: true; text: modelData; color: modelData === window.activePage ? Theme.textPrimary : Theme.textSecondary; font.pixelSize: window.width < 960 ? 12 : Theme.fontNavigation }
                        }
                    }
                }
            }
            Basic.Button {
                objectName: "navigationSettings"; x: 12; y: parent.height-82
                width: parent.width-24; height: 48; Accessible.name: "Settings"
                Accessible.selected: window.activePage === "Settings"
                ToolTip.text: "Settings"
                ToolTip.visible: hovered && window.width < 960
                background: Rectangle { radius: Theme.radiusControl; property color hoverFill: parent.hovered ? Theme.accentSoft : "transparent"; color: window.activePage === "Settings" ? Theme.selection : hoverFill; border.color: parent.visualFocus ? Theme.accentBright : "transparent"; Behavior on hoverFill { ColorAnimation { duration: Theme.motionFast } } }
                contentItem: Item {
                    Label {
                        x: 46; anchors.verticalCenter: parent.verticalCenter
                        text: window.width < 960 ? "" : "Settings"
                        color: window.activePage === "Settings" ? Theme.accent : Theme.textSecondary
                        font.pixelSize: window.width < 960 ? 12 : Theme.fontNavigation
                    }
                    Rectangle {
                        x: window.width < 960 ? (parent.width-16)/2 : 16
                        anchors.verticalCenter: parent.verticalCenter
                        width: 16; height: 16; radius: 8; color: "transparent"
                        border.color: window.activePage === "Settings" ? Theme.accent : Theme.textMuted
                        Rectangle { anchors.centerIn: parent; width: 4; height: 4; radius: 2; color: parent.border.color }
                    }
                }
                padding: 0
                onClicked: window.openSettings(false)
            }
        }


        SourceHome {
            objectName: "emptyMediaState"
            x: window.mainX; y: 18; width: window.width-x-window.rightInset; height: window.height-36
            visible: (window.activePage === "Media" || window.activePage === "Dub") && !mediaViewModel.hasSelectedSource
            importEnabled: typeof appNavigation !== "undefined" ? appNavigation.canImport : mediaViewModel.canOpen
            dropValidator: function(urls) { return typeof appNavigation !== "undefined" ? appNavigation.canDropSource(urls) : mediaViewModel.canDropMedia(urls) }
            dropHandler: function(urls) { return typeof appNavigation !== "undefined" ? appNavigation.dropSource(urls) : mediaViewModel.dropMedia(urls) }
            statusText: mediaViewModel.state === "loading" ? "Opening media…" : mediaViewModel.errorMessage
            onBrowseRequested: mediaFileDialog.open()
        }
        Loader {
            id: settingsLoader
            x: window.mainX; y: 18; width: window.width-x-window.rightInset; height: window.height-36
            active: window.settingsVisited && typeof settingsViewModel !== "undefined" && typeof modelsViewModel !== "undefined"
            visible: window.activePage === "Settings"
            sourceComponent: SettingsPage {
                settings: settingsViewModel
                models: modelsViewModel
                compact: window.width < 960
            }
        }
        Item {
            id: sharedWorkspace
            objectName: "loadedMediaState"
            anchors.fill: parent
            visible: (window.activePage === "Media" || window.activePage === "Dub") && mediaViewModel.hasSelectedSource
            Item {
                objectName: "compactMediaHeader"
                x: window.mainX; y: 24; width: window.mainWidth; height: 50
                Label {
                    id: sourceFilename
                    objectName: "sourceFilename"; width: Math.min(implicitWidth, parent.width-40)
                    text: mediaViewModel.fileName; elide: Text.ElideMiddle
                    Accessible.name: mediaViewModel.fileName; Accessible.description: mediaViewModel.localPath
                    color: Theme.textPrimary; font.pixelSize: 18; font.weight: Font.DemiBold
                }
                Label {
                    y: 30; width: parent.width-52
                    text: mediaViewModel.resolution + " · " + mediaViewModel.frameRate + " · " + mediaViewModel.duration
                    elide: Text.ElideRight; color: Theme.textSecondary; font.pixelSize: 12
                }
                IconButton {
                    id: infoButton; objectName: "metadataInfoButton"
                    x: sourceFilename.width + 8; y: (sourceFilename.height - height) / 2; width: 32; height: 32
                    glyphName: "info"; accessibleText: "Source Info"
                    onClicked: metadataPopup.visible ? metadataPopup.close() : metadataPopup.open()
                    Keys.onReturnPressed: clicked()
                    Keys.onEnterPressed: clicked()
                }
            }
            Rectangle {
                id: windowedVideoHost; objectName: "windowedViewerHost"
                x: window.mainX; y: 92; width: window.mainWidth; height: Math.max(160,window.transportY-104)
                radius: 24; color: Theme.viewerFrame; border.color: Theme.border
            }
            Rectangle {
                objectName: "loadedSourceError"
                z: 10
                x: windowedVideoHost.x + 20
                y: windowedVideoHost.y + 20
                width: windowedVideoHost.width - 40
                height: sourceError.implicitHeight + 20
                visible: mediaViewModel.errorMessage.length > 0 && !mediaViewModel.playback.fullscreen
                color: Theme.popupSurface; border.color: Theme.error; radius: 12
                Label {
                    id: sourceError
                    anchors.fill: parent; anchors.margins: 10
                    text: mediaViewModel.errorMessage
                    color: Theme.textPrimary; font.pixelSize: 12
                    wrapMode: Text.WrapAnywhere
                }
            }
            TransportBar {
                objectName: "windowedTransport"
                x: window.mainX; y: window.transportY; width: window.mainWidth; height: window.transportSize
                playback: mediaViewModel.playback; workspace: mediaViewModel
                originalOnly: window.activePage === "Dub"
                onFullscreenRequested: window.enterViewerFullscreen()
            }
            Item {
                objectName: "workspaceContext"
                x: window.mainX; y: window.contextY; width: window.mainWidth; height: window.contextSize
                OperationRow {
                    objectName: "operationRow"; anchors.fill: parent
                    visible: window.activePage === "Media"; compact: window.compactWorkspace
                    workspace: mediaViewModel
                }
                Column {
                    visible: window.activePage === "Dub"; x: 12; y: 12; spacing: 8
                    Label { text: "Original source"; color: Theme.textPrimary; font.pixelSize: 12; font.weight: Font.DemiBold }
                    Label { text: "Media edits stay in Media."; color: Theme.textSecondary; font.pixelSize: 12 }
                }
            }
            MediaTray {
                id: tray; objectName: "mediaTray"
                x: window.mainX; y: window.trayY; width: window.mainWidth; height: window.traySize
                compact: window.compactWorkspace; workspace: mediaViewModel
                navigation: typeof appNavigation !== "undefined" ? appNavigation : null
                onAddRequested: mediaFileDialog.open()
                onReplaceRequested: function(itemId) { mediaFileDialog.replacementId=itemId; mediaFileDialog.open() }
            }
            Item {
                objectName: "sharedInspector"
                x: window.mainX+window.mainWidth+window.columnGap; y: 92
                width: window.inspectorSize; height: window.height-110
                InspectorHost {
                    objectName: "mediaInspector"; anchors.fill: parent
                    visible: window.activePage === "Media"; workspace: mediaViewModel
                }
                Loader {
                    id: dubLoader; anchors.fill: parent
                    active: mediaViewModel.hasSelectedSource && typeof dubViewModel !== "undefined"
                    visible: window.activePage === "Dub"
                    sourceComponent: DubPage { model: dubViewModel; workspace: mediaViewModel; onSettingsRequested: window.openSettings(true) }
                }
            }
        }
    }

    DropArea {
        objectName: "loadedMediaDropArea"
        anchors.fill: parent
        visible: (window.activePage === "Media" || window.activePage === "Dub") && mediaViewModel.state === "loaded" && !mediaViewModel.playback.fullscreen
        z: 200
        onEntered: function(drag) { drag.accepted = drag.hasUrls && (typeof appNavigation !== "undefined" ? appNavigation.canDropSource(drag.urls) : mediaViewModel.canDropMedia(drag.urls)) }
        onDropped: function(drop) {
            if (drop.hasUrls && (typeof appNavigation !== "undefined" ? appNavigation.dropSource(drop.urls) : mediaViewModel.dropMedia(drop.urls))) drop.acceptProposedAction()
        }
        Rectangle {
            anchors.fill: parent
            anchors.margins: 18
            visible: parent.containsDrag
            radius: 24
            color: "#205AA9FF"
            border.color: Theme.accent
            border.width: 2
        }
    }

    Popup {
        id: metadataPopup
        objectName: "metadataInfoPopup"
        parent: infoButton
        focus: true
        onClosed: infoButton.forceActiveFocus()
        width: Math.min(420, window.width - Theme.spaceSection * 2)
        property real maximumHeight: window.height - Theme.spaceMedium * 2
        function positionInWindow() {
            var anchor = infoButton.mapToItem(window.contentItem, 0, 0)
            var margin = Theme.spaceMedium
            var below = window.height - anchor.y - infoButton.height - 2 * margin
            var above = anchor.y - 2 * margin
            var openBelow = metadataInfoContent.implicitHeight + 2 * padding <= below || below >= above
            maximumHeight = Math.max(0, openBelow ? below : above)
            x = Math.max(margin, Math.min(anchor.x, window.width - width - margin)) - anchor.x
            y = openBelow ? infoButton.height + margin : -height - margin
        }
        onAboutToShow: { positionInWindow(); Qt.callLater(positionInWindow) }
        onOpened: positionInWindow()
        Connections {
            target: window
            function onWidthChanged() { if (metadataPopup.visible) Qt.callLater(metadataPopup.positionInWindow) }
            function onHeightChanged() { if (metadataPopup.visible) Qt.callLater(metadataPopup.positionInWindow) }
        }
        Connections {
            target: infoButton
            function onXChanged() { if (metadataPopup.visible) Qt.callLater(metadataPopup.positionInWindow) }
        }
        height: Math.min(metadataInfoContent.implicitHeight + padding * 2, maximumHeight)
        padding: Theme.spacePanel
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
        background: Rectangle {
            radius: Theme.radiusPanel
            color: Theme.popupSurface
            border.color: Theme.borderStrong
            border.width: Theme.strokeWidth
        }
        enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.motionNormal } }
        exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.motionFast } }
        contentItem: Basic.ScrollView {
            id: metadataInfoScroll
            objectName: "metadataInfoScroll"
            clip: true
            contentWidth: availableWidth
            contentHeight: metadataInfoContent.implicitHeight
            readonly property real maximumScrollOffset: Math.max(0, contentHeight - availableHeight)
            readonly property real scrollOffset: contentItem.contentY
            function scrollToEnd() { contentItem.contentY = maximumScrollOffset }
            Basic.ScrollBar.horizontal.policy: Basic.ScrollBar.AlwaysOff
            Basic.ScrollBar.vertical.policy: Basic.ScrollBar.AsNeeded

            ColumnLayout {
                id: metadataInfoContent
                objectName: "metadataInfoContent"
                width: metadataInfoScroll.availableWidth
                spacing: Theme.spaceMedium
                Label {
                    text: qsTr("Media Info")
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontBrand
                    font.weight: Theme.weightStrong
                }
                Repeater {
                    id: metadataInfoFields
                    objectName: "metadataInfoFields"
                    model: mediaViewModel.mediaInfoRows
                    ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 2
                        Label {
                            text: modelData.label
                            color: modelData.section ? Theme.textPrimary : Theme.textMuted
                            font.pixelSize: modelData.section ? Theme.fontBody : Theme.fontSmall
                            font.weight: modelData.section ? Theme.weightStrong : Font.Normal
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: !modelData.section
                            text: modelData.value
                            wrapMode: Text.WrapAnywhere
                            color: Theme.textPrimary
                            font.pixelSize: Theme.fontBody
                        }
                        IconButton {
                            objectName: "revealSourceButton"
                            visible: modelData.label === "Path"
                            glyphName: "folderOpen"
                            cornerRadius: 10
                            implicitWidth: 36; implicitHeight: 36
                            Layout.topMargin: 6
                            focusPolicy: Qt.StrongFocus
                            accessibleText: qsTr("Reveal source in Explorer")
                            onClicked: mediaViewModel.revealCurrentSource()
                        }
                    }
                }
                Label {
                    Layout.fillWidth: true
                    visible: text.length > 0
                    text: mediaViewModel.revealMessage
                    wrapMode: Text.Wrap
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
            }
        }
    }

    Item {
        id: fullscreenLayer
        objectName: "viewerFullscreenLayer"
        anchors.fill: parent
        visible: mediaViewModel.playback.fullscreen
        z: 1000
        Rectangle { anchors.fill: parent; color: Theme.fullscreenBackground }
        Item { id: fullscreenVideoHost; anchors.fill: parent }
        HoverHandler {
            // Observe position across the whole Viewer, but reveal only in the
            // bottom zone. Coordinates remain meaningful after a child grab.
            onPointChanged: if (hovered) mediaViewModel.playback.setControlsZone(point.position.y >= fullscreenLayer.height - 140)
            onHoveredChanged: mediaViewModel.playback.setControlsZone(hovered && point.position.y >= fullscreenLayer.height - 140)
        }
        Item {
            objectName: "fullscreenActivationZone"
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            height: 140
        }
        TransportBar {
            id: viewerFullscreenTransport
            objectName: "fullscreenTransport"
            width: Math.min(parent.width - 168, 1272)
            height: 94
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: mediaViewModel.playback.controlsVisible ? 32 : 20
            Behavior on anchors.bottomMargin { NumberAnimation { duration: 160 } }
            playback: mediaViewModel.playback
            fullscreenMode: true
            originalOnly: window.activePage === "Dub"
            workspace: mediaViewModel
            opacity: mediaViewModel.playback.controlsVisible ? 1 : 0
            visible: opacity > 0
            onFullscreenRequested: window.exitViewerFullscreen()
            Behavior on opacity {
                NumberAnimation { duration: 160; easing.type: Theme.motionEasing }
            }
            // The complete surface is inside the 140px activation zone.
            // Only pressed controls own grabs; hover must not count as a grab.
        }
    }

    Item {
        id: viewerSurface
        objectName: "videoViewer"
        parent: mediaViewModel.playback.fullscreen ? fullscreenVideoHost : windowedVideoHost
        anchors.fill: parent
        anchors.bottomMargin: 0
        visible: mediaViewModel.state === "loaded" && (window.activePage === "Media" || window.activePage === "Dub")
        Rectangle {
            id: viewerFrame
            anchors.fill: parent
            radius: mediaViewModel.playback.fullscreen ? 0 : 24
            color: mediaViewModel.playback.fullscreen ? Theme.viewerBackground : Theme.viewerFrame
            clip: true
            Rectangle {
                x: 18; y: 18
                width: parent.width - 36; height: parent.height - 36
                visible: !mediaViewModel.playback.fullscreen
                radius: 18
                color: "#0D1016"
            }
            MouseArea {
                objectName: "viewerBackgroundInput"
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton
                onClicked: { shortcutFocus.forceActiveFocus(); viewerSingleClick.mediaId = mediaViewModel.activeMediaId; viewerSingleClick.restart() }
                onDoubleClicked: function(mouse) {
                    mouse.accepted = true
                    viewerSingleClick.stop()
                    if (mediaViewModel.playback.fullscreen) window.exitViewerFullscreen()
                    else window.enterViewerFullscreen()
                }
                Timer {
                    id: viewerSingleClick
                    property string mediaId: ""
                    interval: Qt.styleHints.mouseDoubleClickInterval
                    onTriggered: if (mediaId === mediaViewModel.activeMediaId) mediaViewModel.playback.togglePlayback()
                }
            }
            UnifiedPreview {
                id: livePreview
                anchors.fill: parent
                // Six logical pixels keep 10px Crop markers fully inside fullscreen.
                anchors.margins: mediaViewModel.playback.fullscreen ? (mediaViewModel.previewPlan.editing ? 6 : 0) : 18
                workspace: mediaViewModel
                cropHandleHost: cropHandleLayer
                onViewerNavigationRequested: shortcutFocus.forceActiveFocus()
                onWidthChanged: if (mediaViewModel.imagePreview) mediaViewModel.imagePreview.setViewport(width,height)
                onHeightChanged: if (mediaViewModel.imagePreview) mediaViewModel.imagePreview.setViewport(width,height)
            }
            Label {
                objectName: "audioOnlyPresentation"
                anchors.centerIn: parent
                visible: mediaViewModel.sourceKind === "audio"
                text: "Audio · " + mediaViewModel.fileName
                color: Theme.textSecondary; font.pixelSize: 16
            }
            Row {
                id: viewerZoomControls
                anchors.left: parent.left; anchors.top: parent.top
                anchors.margins: 30; spacing: 6
                Basic.Button {
                    id: fitButton
                    objectName: "viewerFitButton"; text: "Fit"
                    implicitWidth: 40; implicitHeight: 32
                    padding: 0
                    Accessible.name: "Fit preview"
                    onClicked: mediaViewModel.viewerViewport.fit()
                    background: Rectangle { radius: 8; color: fitButton.down ? Theme.viewerControlPressed : fitButton.hovered ? Theme.viewerControlHover : mediaViewModel.viewerViewport.mode === "fit" ? Theme.viewerControlSelected : Theme.viewerControl; border.color: fitButton.visualFocus || mediaViewModel.viewerViewport.mode === "fit" ? Theme.accentBright : Theme.fieldBorder; border.width: fitButton.visualFocus ? 2 : 1; Behavior on color { ColorAnimation { duration: Theme.motionFast } } }
                    contentItem: Label { text: parent.text; color: Theme.textPrimary; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                }
                Basic.Button {
                    id: zoomButton
                    objectName: "viewerZoomReadout"
                    text: mediaViewModel.viewerViewport.pixelZoomText
                    height: 32; implicitWidth: 72; padding: 0
                    Accessible.name: "Show source pixels at 100 percent"
                    onClicked: mediaViewModel.viewerViewport.pixels()
                    contentItem: Label { text: parent.text; color: Theme.textPrimary; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    background: Rectangle { radius: 8; color: zoomButton.down ? Theme.viewerControlPressed : zoomButton.hovered ? Theme.viewerControlHover : mediaViewModel.viewerViewport.mode === "pixels" ? Theme.viewerControlSelected : Theme.viewerControl; border.color: zoomButton.visualFocus || mediaViewModel.viewerViewport.mode === "pixels" ? Theme.accentBright : Theme.fieldBorder; border.width: zoomButton.visualFocus ? 2 : 1; Behavior on color { ColorAnimation { duration: Theme.motionFast } } }
                }

            }
            PreviewStatus {
                id: pendingPreviewStatus
                objectName: "imagePreviewStatus"
                anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: parent.bottom
                anchors.bottomMargin: 30
                readonly property bool imageProgress: !!(mediaViewModel.imagePreview && mediaViewModel.imagePreview.statusKind === "progress" && mediaViewModel.imagePreview.busy)
                errorActive: !!(mediaViewModel.imagePreview && mediaViewModel.imagePreview.statusKind === "error")
                progressActive: !errorActive && imageProgress
                requestKey: mediaViewModel.activeMediaId + ":" + (mediaViewModel.imagePreview ? mediaViewModel.imagePreview.requestGeneration : 0) + ":" + mediaViewModel.playback.playing
                details: mediaViewModel.imagePreview ? mediaViewModel.imagePreview.status : ""
            }
            IconButton {
                id: viewerFullscreen
                objectName: "fullscreenButton"
                visible: !mediaViewModel.playback.fullscreen
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 34
                implicitWidth: 32
                implicitHeight: 32
                glyphName: mediaViewModel.playback.fullscreen ? "exit" : "fullscreen"
                accessibleText: mediaViewModel.playback.fullscreen ? "Exit fullscreen" : "Enter fullscreen"
                onClicked: if (mediaViewModel.playback.fullscreen) window.exitViewerFullscreen(); else window.enterViewerFullscreen()
            }
            Basic.ComboBox {
                id: viewerChannel; objectName: "viewerChannel"
                anchors.top: parent.top; anchors.right: parent.right
                anchors.topMargin: 30; anchors.rightMargin: 30
                width: 92; height: 32; visible: mediaViewModel.playback.imageMode
                model: ["RGBA", "RGB", "Alpha"]
                currentIndex: mediaViewModel.imagePreview ? model.indexOf(mediaViewModel.imagePreview.channel) : 0
                onActivated: mediaViewModel.setImageChannel(currentText)
                Accessible.name: "Preview channels: RGBA checker, RGB ignore alpha, Alpha grayscale"
                background: Rectangle { radius: 12; color: Theme.fullscreenTransport; border.color: Theme.border }
                contentItem: Label { text: viewerChannel.currentText; leftPadding: 12; rightPadding: 22; verticalAlignment: Text.AlignVCenter; color: Theme.textPrimary; font.pixelSize: 12 }
                indicator: Canvas {
                    x: viewerChannel.width - 23; y: (viewerChannel.height - height) / 2; width: 12; height: 12
                    readonly property color stroke: Theme.textSecondary
                    onStrokeChanged: requestPaint()
                    onPaint: {
                        var c = getContext("2d")
                        c.clearRect(0, 0, width, height)
                        c.strokeStyle = stroke; c.lineWidth = 1.5
                        c.beginPath(); c.moveTo(2,4); c.lineTo(6,8); c.lineTo(10,4); c.stroke()
                    }
                }
                delegate: Basic.ItemDelegate { width: viewerChannel.width; highlighted: viewerChannel.highlightedIndex===index; contentItem: Label { text: modelData; color: Theme.textPrimary; font.pixelSize: 12 } background: Rectangle { color: parent.highlighted ? Theme.selection : Theme.popupSurface } }
                popup: Basic.Popup { y: viewerChannel.height+4; width: viewerChannel.width; padding: 6; background: Rectangle { radius: 12; color: Theme.popupSurface; border.color: Theme.border } contentItem: ListView { implicitHeight: contentHeight; model: viewerChannel.popup.visible ? viewerChannel.delegateModel : null; clip: true } }
            }
            Label {
                anchors.centerIn: parent
                visible: mediaViewModel.playback.state === "Error"
                text: mediaViewModel.playback.errorMessage
                color: Theme.textSecondary
                font.pixelSize: Theme.fontBody
            }
            Item {
                id: cropHandleLayer
                objectName: "cropHandleLayer"
                enabled: mediaViewModel.canOpen
                parent: mediaViewModel.playback.fullscreen ? fullscreenLayer : viewerFrame
                anchors.fill: parent
                z: 1
            }
        }
    }
}
