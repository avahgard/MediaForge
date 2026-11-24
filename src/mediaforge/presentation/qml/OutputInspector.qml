import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import "."

ColumnLayout {
    id: root
    required property var workspace
    readonly property var output: workspace.output
    readonly property var settings: output.settings
    readonly property var summary: output.summary
    property bool advanced: false
    readonly property bool audioOnly: settings.container === "audio"
    readonly property bool audioAvailable: audioOnly && summary.code !== "unsupported_audio_source"
    readonly property bool pictureOutput: settings.kind !== "media"
    spacing: 10
    // Individual settings are disabled while processing; Cancel stays enabled.

    component Caption: Label { Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.textMuted; font.pixelSize: 11 }
    component Entry: Basic.TextField {
        Layout.minimumWidth: 0
        enabled: !root.output.busy
        property string field: ""
        property string value: ""
        property string mediaId: root.workspace.activeMediaId
        Layout.fillWidth: true
        text: value
        onValueChanged: if (!activeFocus) text = value
        onMediaIdChanged: { focus = false; text = value }
        selectByMouse: true
        color: Theme.textPrimary; font.pixelSize: 12; padding: 12
        background: Rectangle { radius: 12; color: Theme.surface; border.color: parent.activeFocus ? Theme.accent : Theme.border }
        onEditingFinished: if (!root.output.setSetting(field, text)) text = value
    }
    component Choice: Basic.ComboBox {
        Layout.minimumWidth: 0
        enabled: !root.output.busy
        id: choice
        property string field: ""
        property var values: []
        property string value: ""
        Layout.fillWidth: true
        currentIndex: values.indexOf(value)
        padding: 12
        rightPadding: 28
        onActivated: root.output.setSetting(field, values[currentIndex])
        contentItem: Label { text: choice.displayText; color: Theme.textPrimary; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
        background: Rectangle { radius: 12; color: Theme.surface; border.color: choice.activeFocus ? Theme.accent : Theme.border }
        indicator: Item {
            x: choice.width-24; anchors.verticalCenter: parent.verticalCenter; width: 12; height: 8
            Rectangle { x: 1; y: 2; width: 7; height: 1.5; rotation: 45; color: Theme.textSecondary }
            Rectangle { x: 5; y: 2; width: 7; height: 1.5; rotation: -45; color: Theme.textSecondary }
        }
        delegate: Basic.ItemDelegate {
            objectName: "exportChoice_" + choice.field + "_" + index
            width: choice.width
            contentItem: Label { text: modelData; color: Theme.textPrimary; font.pixelSize: 12 }
            background: Rectangle { color: parent.highlighted ? Theme.selection : Theme.popupSurface }
            highlighted: choice.highlightedIndex === index
        }
        popup: Basic.Popup {
            objectName: choice.objectName + "Popup"
            parent: choice
            margins: 8
            y: choice.height + 4
            width: choice.width; height: Math.min(240,contentItem.implicitHeight+12); padding: 6
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
            Connections {
                target: choice.Window.window
                function onHeightChanged() { if (choice.popup.visible) choice.popup.anchorToField() }
            }
            background: Rectangle { radius: 12; color: Theme.popupSurface; border.color: Theme.border }
            contentItem: ListView {
                objectName: choice.objectName + "Choices"
                implicitHeight: contentHeight
                model: choice.popup.visible ? choice.delegateModel : null
                currentIndex: choice.highlightedIndex
                clip: true
            }
        }
    }
    component Action: Basic.Button {
        Layout.minimumWidth: 0
        Layout.fillWidth: true
        contentItem: Label { text: parent.text; color: parent.enabled ? Theme.textPrimary : Theme.textDisabled; padding: 10; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 12 }
        background: Rectangle { radius: 12; color: parent.down ? Theme.selectionStrong : Theme.surface; border.color: parent.activeFocus ? Theme.accent : Theme.border }
    }

    Item { Layout.preferredHeight: 6 }
    Caption { text: "Export as" }
    Choice {
        objectName: "exportKind"
        enabled: !root.output.busy && root.summary.code !== "unsupported_audio_source"
        model: root.workspace.sourceKind === "audio" ? ["Audio"] : root.workspace.sourceKind === "still" ? ["Still image"] : root.workspace.sourceKind === "sequence" ? ["Video", "Current image", "Image sequence"] : ["Video / Audio", "Current frame", "Image sequence"]
        values: root.workspace.sourceKind === "audio" ? ["media"] : root.workspace.sourceKind === "still" ? ["still"] : ["media","frame","sequence"]
        value: root.settings.kind || "media"; field: "kind"
    }
    Caption { text: "File Type" }
    Choice { objectName: "exportContainer"; enabled: !root.output.busy && root.summary.code !== "unsupported_audio_source"; model: root.workspace.sourceKind === "audio" ? ["Audio"] : root.pictureOutput ? (root.settings.kind === "sequence" ? ["PNG","JPEG","TIFF"] : ["PNG","JPEG","TIFF","WebP"]) : root.workspace.sourceKind === "sequence" ? ["MOV","MP4"] : ["Same as Source", "MOV", "MP4", "Audio Only"]; values: root.workspace.sourceKind === "audio" ? ["audio"] : root.pictureOutput ? (root.settings.kind === "sequence" ? ["png","jpeg","tiff"] : ["png","jpeg","tiff","webp"]) : root.workspace.sourceKind === "sequence" ? ["mov","mp4"] : ["source", "mov", "mp4", "audio"]; value: root.settings.container || "source"; field: "container" }
    Caption { objectName: "exportMatteCaption"; visible: !!root.summary.hasAlpha; text: root.summary.matteRequired ? "Background / Matte" : "Alpha handling" }
    Choice { objectName: "exportMatte"; visible: !!root.summary.hasAlpha; model: root.summary.matteRequired ? ["Choose background…","Black","White"] : ["Preserve Alpha","Flatten on Black","Flatten on White"]; values: ["","black","white"]; value: root.settings.matte || ""; field: "matte"; Accessible.name: root.summary.matteRequired ? "Background / Matte" : "Alpha handling" }
    Caption { objectName: "exportMatteExplanation"; visible: !!root.summary.matteRequired; text: "This output cannot keep transparency. Choose Black or White for transparent areas and Fit padding." }
    Caption { visible: !root.audioOnly && !root.pictureOutput; text: "Video codec" }
    Choice {
        objectName: "exportCodec"; visible: !root.audioOnly && !root.pictureOutput
        model: root.settings.container === "mov" ? ["Apple ProRes", "H.264"] : root.settings.container === "mp4" ? ["H.264"] : ["Same as Source", "H.264", "Apple ProRes (MOV only)"]
        values: root.settings.container === "mov" ? ["prores", "h264"] : root.settings.container === "mp4" ? ["h264"] : ["source", "h264", "prores"]
        value: root.settings.codec === "source" && root.settings.container !== "source" ? root.summary.actualCodec || "" : root.settings.codec || "source"; field: "codec"
    }
    Basic.CheckBox {
        visible: root.settings.container === "webp" && !!root.summary.hasAlpha
        text: "Allow invisible RGB to change"
        checked: !!root.settings.webp_ack
        onToggled: root.output.setSetting("webp_ack",checked ? "true" : "false")
    }
    Caption { visible: root.settings.container === "jpeg"; text: "Quality · 0.00–1.00" }
    Entry { objectName: "exportJpegQuality"; visible: root.settings.container === "jpeg"; field: "jpeg_quality_normalized"; value: root.settings.jpeg_quality_normalized || "0.90" }
    Caption { visible: root.settings.container === "jpeg"; text: "Chroma subsampling" }
    Choice { objectName: "exportJpegSubsampling"; visible: root.settings.container === "jpeg"; model: ["4:1:1","4:2:2","4:4:4"]; values: ["411","422","444"]; value: root.settings.jpeg_subsampling || "444"; field: "jpeg_subsampling" }
    Caption { visible: root.settings.container === "png" || root.settings.container === "tiff"; text: "Data Type" }
    Choice { objectName: "exportImageDepth"; visible: root.settings.container === "png" || root.settings.container === "tiff"; model: root.settings.container === "tiff" ? ["8-bit","16-bit","32-bit float"] : ["8-bit","16-bit"]; values: root.settings.container === "tiff" ? ["8","16","float"] : ["8","16"]; value: root.settings.image_depth || "8"; field: "image_depth" }
    Caption { visible: root.settings.container === "tiff"; text: "Compression" }
    Choice { objectName: "exportTiffCompression"; visible: root.settings.container === "tiff"; model: ["None","PackBits","LZW","Deflate"]; values: ["raw","packbits","lzw","deflate"]; value: root.settings.tiff_compression || "deflate"; field: "tiff_compression" }
    Caption { visible: root.settings.container === "webp"; text: "Encoding" }
    Choice { objectName: "exportWebpMode"; visible: root.settings.container === "webp"; model: ["Lossless","Lossy"]; values: ["true","false"]; value: root.settings.webp_lossless ? "true" : "false"; field: "webp_lossless" }
    Caption { visible: root.settings.container === "webp" && !root.settings.webp_lossless; text: "Quality · 0.00–1.00" }
    Entry { objectName: "exportWebpQuality"; visible: root.settings.container === "webp" && !root.settings.webp_lossless; field: "webp_quality_normalized"; value: (Number(root.settings.webp_quality === undefined ? 90 : root.settings.webp_quality)/100).toFixed(2) }
    Caption { visible: root.audioAvailable; text: "Audio format" }
    Choice { objectName: "exportAudioFormat"; visible: root.audioAvailable; model: ["WAV · PCM 16-bit", "MP3"]; values: ["wav","mp3"]; value: root.settings.audio_format || "wav"; field: "audio_format" }
    Caption { visible: root.audioAvailable; text: "Source audio stream" }
    Choice { objectName: "exportAudioStream"; visible: root.audioAvailable; model: root.settings.audio_streams || []; values: Array.from({length:model.length},function(_,i){return String(i)}); value: String(root.settings.audio_stream || 0); field: "audio_stream" }
    Caption { visible: root.audioAvailable && root.settings.audio_format === "mp3"; text: "MP3 bitrate" }
    Choice { objectName: "exportAudioBitrate"; visible: root.audioAvailable && root.settings.audio_format === "mp3"; model: ["128 kb/s","192 kb/s","256 kb/s","320 kb/s"]; values: ["128","192","256","320"]; value: String(root.settings.audio_bitrate || 192); field: "audio_bitrate" }
    Caption { visible: root.summary.codec === "h264"; text: "Quality" }
    Choice { objectName: "exportQualityPreset"; visible: root.summary.codec === "h264"; model: ["High Quality", "Balanced", "Smaller File", "Custom (Advanced)"]; values: ["high","balanced","small","custom"]; value: root.settings.quality_preset || "high"; field: "quality_preset"; onActivated: { root.output.setSetting(field,values[currentIndex]); if (values[currentIndex] === "custom") root.advanced = true } }
    Caption { visible: root.summary.codec === "prores"; text: "ProRes profile" }
    Choice { objectName: "exportProresQuality"; visible: root.summary.codec === "prores"; model: ["Proxy", "422 LT", "422 Standard", "422 HQ", "4444", "4444 XQ"]; values: ["0","1","2","3","4","5"]; value: String(root.settings.prores_quality === undefined ? 2 : root.settings.prores_quality); field: "prores_quality" }
    Caption { visible: root.summary.codec === "h264"; text: "Encoder speed" }
    Choice { objectName: "exportSpeed"; visible: root.summary.codec === "h264"; model: ["Fast", "Balanced", "Quality"]; values: ["fast","balanced","quality"]; value: root.settings.speed || "balanced"; field: "speed" }
    Label { visible: root.summary.codec === "h264"; Layout.fillWidth: true; wrapMode: Text.Wrap; text: "Faster uses less encoder effort; Quality uses more time for compression. Visual quality is controlled separately."; color: Theme.textMuted; font.pixelSize: 11 }
    Action { objectName: "exportAdvanced"; visible: root.summary.codec === "h264"; text: (root.advanced ? "⌄" : "›") + " Advanced"; onClicked: root.advanced = !root.advanced }
    ColumnLayout {
        visible: root.advanced && root.summary.codec === "h264"
        Layout.fillWidth: true
        Caption { text: "Exact CRF (0–51; lower = higher quality)" }
        Entry { objectName: "exportH264Quality"; field: "h264_quality"; value: String(root.settings.h264_quality === undefined ? 18 : root.settings.h264_quality) }
        Caption { text: "H.264 profile" }
        Choice { objectName: "exportH264Profile"; model: ["Auto","High","Main"]; values: ["auto","high","main"]; value: root.settings.h264_profile || "auto"; field: "h264_profile" }
    }
    Label {
        objectName: "exportEffectiveSummary"
        Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: 12
        color: root.summary.valid ? Theme.textPrimary : Theme.error
        text: root.audioOnly && root.summary.valid ? root.summary.extension + " · " + root.summary.actualCodec + " · audio only" : root.summary.valid ? "Coded " + root.summary.width + "×" + root.summary.height + "\nSAR " + root.summary.sar + " · DAR " + root.summary.dar + "\n" + root.summary.extension + " · " + root.summary.actualCodec + (root.summary.codec === "copy" ? " (exact copy)" : "") : root.summary.message || "Open a video first."
    }
    Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: root.summary.valid ? root.summary.message : ""; color: Theme.textMuted; font.pixelSize: 11 }
    Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: root.summary.qualityDescription || ""; visible: text.length > 0; color: Theme.textSecondary; font.pixelSize: 11 }
    Caption { text: "Filename" }
    Entry { objectName: "exportFilename"; field: "filename"; value: root.settings.filename || ""; Accessible.name: "Filename" }
    Caption { visible: root.settings.kind === "sequence"; text: "Start Number" }
    Entry {
        objectName: "exportStartNumber"; visible: root.settings.kind === "sequence"
        field: "sequence_start"; value: root.settings.sequence_start || "0001"
        Accessible.name: "Start Number"; inputMethodHints: Qt.ImhDigitsOnly
    }
    Caption { text: "Destination Folder" }
    Entry { objectName: "exportFolder"; field: "folder"; value: root.settings.folder || "" }
    Action { objectName: "exportBrowseFolder"; text: "Choose folder…"; enabled: !root.output.busy; onClicked: folderDialog.open() }
    FolderDialog { id: folderDialog; title: "Choose export folder"; onAccepted: root.output.chooseFolder(selectedFolder) }
    // Created once by InspectorHost in its reserved footer, outside the scroll viewport.
    property Component actions: ColumnLayout {
        spacing: 6
        Label {
            objectName: "exportActionError"; Layout.fillWidth: true
            visible: !!root.output.actionError && !root.output.busy
            text: root.output.actionError || ""; color: Theme.error
            wrapMode: Text.Wrap; font.pixelSize: 11
        }
        Basic.ProgressBar { objectName: "exportProgress"; Layout.fillWidth: true; visible: root.output.busy && root.output.stage !== "admission"; indeterminate: root.output.progress < 0; value: Math.max(0,root.output.progress) }
        Label { visible: root.output.busy; Layout.fillWidth: true; wrapMode: Text.Wrap; text: Math.floor(root.output.elapsed) + " s elapsed" + (root.output.speed ? " · " + root.output.speed + " speed" : "") + (root.output.fps ? " · " + root.output.fps + (root.audioOnly ? " packets/s" : " fps") : ""); color: Theme.textSecondary; font.pixelSize: 11 }
        RowLayout {
            Layout.fillWidth: true
            Action {
                objectName: "exportStart"; text: "Export"; enabled: root.output.canExport
                onClicked: root.output.startExport()
                implicitHeight: 44
                Accessible.name: "Export"
                id: exportAction
                contentItem: Label {
                    text: parent.text
                    color: parent.enabled ? Theme.primaryLabel : Theme.primaryDisabledLabel
                    font.family: Theme.fontFamily
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: PrimaryButtonBackground { button: exportAction; inspector: true }
            }
            Action { objectName: "exportCancel"; text: "Cancel"; visible: root.output.busy; enabled: root.output.canCancel; onClicked: root.output.cancelExport() }
        }
    }
    Label { objectName: "exportStatus"; Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; text: root.output.message + (root.output.outputPath ? "\n" + root.output.outputPath : ""); color: root.output.state === "Failed" ? Theme.error : Theme.textSecondary; font.pixelSize: 12 }
    Action { objectName: "exportReveal"; text: "Open folder"; visible: root.output.state === "Completed" && root.output.outputPath.length > 0; onClicked: root.output.revealOutput() }
    Item { Layout.preferredHeight: 8 }
}
