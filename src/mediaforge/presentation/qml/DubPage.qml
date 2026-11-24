import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import "."

Rectangle {
    id: page
    objectName: "dubPage"
    required property var model
    required property var workspace
    signal settingsRequested()
    radius: 24
    color: Theme.popupSurface
    border.color: Theme.border
    clip: true

    readonly property real inset: 22
    readonly property real rightInset: 22 + inspectorBar.implicitWidth
    readonly property bool inspecting: model.busy && model.operation.stage === "inspect"
    readonly property bool terminal: ["completed", "failed", "cancelled"].includes(model.state)
    readonly property bool completed: model.state === "completed"
    readonly property bool jobResult: terminal && model.operation.stage !== "inspect"
    property bool editingResult: false
    readonly property string sourcePath: model.sourceInfo.path || ""
    onJobResultChanged: editingResult = false
    onSourcePathChanged: {
        closeChoices()
        if (filename) {
            filename.text = model.filename
            filename.focus = false
        }
    }
    onVisibleChanged: if (!visible) {
        closeChoices()
        model.cancelSpanConsent()
    }

    function closeChoices() {
        if (track) track.popup.close()
        if (outputMode) outputMode.popup.close()
        if (audioFormat) audioFormat.popup.close()
    }

    FolderDialog {
        id: outputDialog
        title: "Choose Dub Output Folder"
        onAccepted: page.model.chooseFolder(selectedFolder)
    }
    ForgeDialog {
        id: spanDialog
        objectName: "dubSpanConsent"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(420, parent.width - 32)
        closePolicy: Popup.CloseOnEscape
        title: "Audio extends beyond the video"
        confirmText: "Continue"
        contentItem: Label {
            text: page.model.spanConsentText
            color: Theme.textPrimary
            wrapMode: Text.Wrap
        }
        onRejected: page.model.cancelSpanConsent()
        onAccepted: page.model.confirmSpanConsent()
    }
    Connections {
        target: page.model
        function onChanged() {
            if (page.model.spanConsentText && page.visible) {
                if (!spanDialog.visible) spanDialog.open()
            } else if (spanDialog.visible) {
                spanDialog.close()
            }
        }
    }
    Connections {
        target: page.Window.window
        function onWidthChanged() { page.closeChoices() }
        function onHeightChanged() { page.closeChoices() }
        function onActiveFocusItemChanged() {
            var item = page.Window.window.activeFocusItem
            if (!item) return
            var node = item
            while (node && node !== form) node = node.parent
            if (!node) return
            var point = item.mapToItem(form, 0, 0)
            var flick = body.contentItem
            if (point.y < flick.contentY)
                flick.contentY = Math.max(0, point.y)
            else if (point.y + item.height > flick.contentY + body.availableHeight)
                flick.contentY = Math.max(0, point.y + item.height - body.availableHeight)
        }
    }

    component Caption: Label {
        Layout.fillWidth: true
        color: Theme.textMuted
        font.pixelSize: 11
    }
    component Detail: Label {
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        color: Theme.textPrimary
        font.pixelSize: 12
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }

    Column {
        x: page.inset; y: 18; width: parent.width - x - page.rightInset
        Item {
            height: 36
            width: parent.width
            Label {
                objectName: "dubInspectorHeading"
                text: "Automatic dubbing"
                anchors.verticalCenter: parent.verticalCenter
                font.pixelSize: 18
                font.weight: Font.DemiBold
                color: Theme.textPrimary
            }
        }
        Label { text: "English → Russian"; font.pixelSize: 12; color: Theme.textSecondary }
    }

    Basic.ScrollView {
        id: body
        objectName: "dubInspectorScroll"
        x: page.inset; y: 78; width: page.width - x - 10
        height: Math.max(0, footer.y - y - 12)
        rightPadding: inspectorBar.implicitWidth
        clip: true
        contentWidth: availableWidth
        contentHeight: form.implicitHeight
        Basic.ScrollBar.horizontal.policy: Basic.ScrollBar.AlwaysOff
        Basic.ScrollBar.vertical: Basic.ScrollBar {
            id: inspectorBar
            parent: body; x: body.width - width; y: body.topPadding
            width: implicitWidth; height: body.availableHeight
            policy: Basic.ScrollBar.AsNeeded
        }
        Connections {
            target: body.contentItem
            function onContentYChanged() { page.closeChoices() }
        }

        ColumnLayout {
            id: form
            width: body.availableWidth - 12
            spacing: 8

            ColumnLayout {
                Layout.fillWidth: true
                visible: !page.model.busy && (!page.jobResult || page.editingResult)
                spacing: page.height < 500 ? 5 : 10
                ForgeChoice {
                    id: track
                    objectName: "dubAudioTrack"
                    visible: page.model.tracks.length > 1
                    model: page.model.tracks
                    textRole: "name"
                    currentIndex: page.model.selectedTrackIndex
                    displayText: currentIndex < 0 ? "Select an audio track…" : currentText
                    Accessible.name: "Source audio track"
                    onActivated: if (model[currentIndex].eligible) page.model.selectTrack(model[currentIndex].id)
                }
                Detail {
                    objectName: "dubAudioTrackRefusals"
                    visible: text.length > 0
                    text: page.model.tracks.length > 1 ? page.model.tracks.filter(track => track.reason)
                        .map(track => "Track " + track.id + " · " + track.reason).join("\n") : ""
                }
                Caption { text: "Voice" }
                Detail { objectName: "dubVoice"; text: page.model.voiceLabel }
                Caption { text: "Export as" }
                ForgeChoice {
                    id: outputMode
                    objectName: "dubOutputMode"
                    visible: !!page.model.sourceInfo.video
                    model: ["Video — Same as Source", "Compatible MP4 — H.264 + AAC", "Dubbed Audio Only"]
                    currentIndex: page.model.outputMode === "compatible_mp4" ? 1 : page.model.outputMode === "video_audio_only" ? 2 : 0
                    displayText: currentIndex === 0 && page.model.outputRouteSummary ? page.model.outputRouteSummary : currentText
                    Accessible.name: "Dub output route"
                    onActivated: page.model.setMode(["same_as_source", "compatible_mp4", "video_audio_only"][currentIndex])
                }
                ForgeChoice {
                    id: audioFormat
                    objectName: "dubAudioFormat"
                    visible: page.model.outputMode !== "same_as_source" && page.model.outputMode !== "compatible_mp4"
                    model: ["MP3 · 192 kb/s", "WAV · PCM16"]
                    currentIndex: page.model.outputFormat === "wav" ? 1 : 0
                    Accessible.name: "Dub audio format"
                    onActivated: page.model.setFormat(currentIndex === 0 ? "mp3" : "wav")
                }
                Caption { text: "Filename" }
                Basic.TextField {
                    id: filename
                    objectName: "dubFilename"
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    property string value: page.model.filename
                    text: value
                    onValueChanged: if (!activeFocus) text = value
                    placeholderText: "Filename"
                    color: Theme.textPrimary
                    font.pixelSize: 12
                    padding: 12
                    selectByMouse: true
                    Accessible.name: "Filename"
                    Accessible.description: page.model.filename
                    background: Rectangle {
                        radius: 12; color: Theme.surface
                        border.color: filename.activeFocus ? Theme.accent : Theme.border
                    }
                    function commit() {
                        if (text !== value) page.model.setFilename(text)
                        text = value
                    }
                    onEditingFinished: commit()
                }
                Caption { text: "Destination Folder" }
                Basic.TextField {
                    objectName: "dubFullDestination"
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    text: page.model.folder
                    placeholderText: "No folder selected"
                    readOnly: true
                    selectByMouse: true
                    padding: 12
                    font.pixelSize: 12
                    color: Theme.textPrimary
                    Accessible.name: "Destination Folder"
                    Accessible.description: text
                    ToolTip.visible: hovered && text.length > 0
                    ToolTip.text: text
                    background: Rectangle {
                        radius: 12; color: Theme.surface
                        border.color: parent.activeFocus ? Theme.accent : Theme.border
                    }
                }
                Basic.Button {
                    objectName: "dubChooseFolder"
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    text: "Choose folder…"
                    contentItem: Label {
                        text: parent.text; color: parent.enabled ? Theme.textPrimary : Theme.textDisabled
                        padding: 10; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 12
                    }
                    background: Rectangle {
                        radius: 12
                        color: parent.down ? Theme.selectionStrong : Theme.surface
                        border.color: parent.activeFocus ? Theme.accent : Theme.border
                    }
                    onClicked: {
                        outputDialog.currentFolder = page.model.folderUrl
                        outputDialog.open()
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                visible: page.model.busy && !page.inspecting
                spacing: 8
                Detail { text: page.model.stage || "Processing…"; font.weight: Font.DemiBold }
                Detail { text: "Elapsed " + page.model.elapsedText }
                ForgeProgress {
                    objectName: "dubProgress"
                    Layout.fillWidth: true
                    indeterminate: page.model.operation.total <= 0
                    value: page.model.operation.total > 0 ? Math.min(1, page.model.operation.completed / page.model.operation.total) : 0
                }
                Detail {
                    visible: page.model.operation.total > 0
                    text: page.model.operation.completed + " / " + page.model.operation.total
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                visible: page.jobResult && !page.model.busy && !page.editingResult
                spacing: 8
                Detail {
                    text: page.completed ? "Completed" : page.model.state === "cancelled" ? "Cancelled" : "Failed"
                    color: page.completed ? Theme.success : Theme.error
                    font.weight: Font.DemiBold
                }
                Detail { objectName: "dubResultMessage"; text: page.model.message; wrapMode: Text.WrapAnywhere }
                Detail { visible: page.completed; text: page.model.operation.destination || ""; wrapMode: Text.WrapAnywhere }
                Detail { text: page.model.warningsText; visible: text.length > 0 }
                ForgeButton {
                    objectName: "dubEditSetup"
                    visible: !page.completed
                    text: "Edit setup"
                    onClicked: {
                        page.editingResult = true
                        body.contentItem.contentY = 0
                        outputMode.forceActiveFocus()
                    }
                }
            }
        }
    }

    // Failed and completed jobs retain their existing actions. The output
    // form stays available when editing the last job's configuration.
    Item {
        id: footer
        objectName: "dubActionFooter"
        x: page.inset; width: page.width - x - page.rightInset
        anchors.bottom: parent.bottom; anchors.bottomMargin: 22
        height: action.implicitHeight + (status.visible ? status.height + 6 : 0)
        Label {
            id: status
            objectName: "dubFooterStatus"
            readonly property bool opensModels: text === "Models unavailable. Open Settings → Dub Models."
            width: parent.width
            text: page.inspecting ? "Preparing…" : page.model.busy || (page.jobResult && !page.editingResult) ? ""
                  : page.model.blocker || page.model.actionError
            visible: text.length > 0
            wrapMode: Text.WrapAnywhere
            font.pixelSize: 12
            color: opensModels && activeFocus ? Theme.accentBright : page.inspecting ? Theme.textSecondary : Theme.warning
            textFormat: Text.PlainText
            activeFocusOnTab: opensModels
            Accessible.role: opensModels ? Accessible.Button : Accessible.StaticText
            Accessible.onPressAction: if (opensModels) page.settingsRequested()
            Keys.onReturnPressed: if (opensModels) page.settingsRequested()
            Keys.onSpacePressed: if (opensModels) page.settingsRequested()
            TapHandler { enabled: status.opensModels; onTapped: page.settingsRequested() }
        }
        ForgeButton {
            id: action
            objectName: "dubPrimary"
            anchors.bottom: parent.bottom
            width: parent.width; implicitHeight: 44; primary: true
            font.family: Theme.fontFamily
            contentItem: Text {
                text: action.text; font: action.font
                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                color: action.enabled ? Theme.primaryLabel : Theme.primaryDisabledLabel
            }
            background: PrimaryButtonBackground { button: action; inspector: true }
            text: page.model.busy ? (page.model.state === "cancelling" ? "Cancelling…" : page.model.canCancel ? "Cancel" : "Finalizing…")
                  : page.completed ? "Open folder" : page.model.canRetrySource ? "Check source again"
                  : page.model.canRetry ? "Retry" : "Start Dub"
            enabled: page.model.busy ? page.model.canCancel
                     : page.completed || page.model.canRetrySource || page.model.canStart
            onClicked: {
                if (page.model.busy) page.model.cancel()
                else if (page.completed) page.model.openFolder()
                else if (page.model.canRetrySource) page.model.retrySource()
                else {
                    if (filename.visible) filename.commit()
                    if (page.model.canRetry) page.model.retry()
                    else page.model.start()
                }
            }
        }
    }
}
