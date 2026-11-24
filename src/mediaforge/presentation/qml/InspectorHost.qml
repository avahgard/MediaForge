import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import "."

Rectangle {
    id: root
    required property var workspace
    readonly property var state: workspace.editState
    readonly property var trimInfo: workspace.trimReadout || ({})
    readonly property string operation: workspace.selectedOperation
    onOperationChanged: reveal.restart()
    radius: 24
    color: Theme.popupSurface
    border.color: Theme.border
    clip: true
    Component.onCompleted: workspace.registerInspectorWheel(inspectorScroll, root.Window.window)

    function time(ms, outBoundary) {
        if (root.workspace.sourceKind === "sequence") return String((ms || 0) + (outBoundary ? 0 : 1))
        return timeText(ms)
    }
    function timeText(ms) {
        var value = Math.max(0, Math.round(ms || 0))
        var minute = Math.floor(value / 60000)
        var second = Math.floor(value % 60000 / 1000)
        var milli = value % 1000
        return String(minute).padStart(2, "0") + ":" + String(second).padStart(2, "0")
               + "." + String(milli).padStart(3, "0")
    }
    function parseTime(value, outBoundary) {
        if (root.workspace.sourceKind === "sequence") {
            var ordinal = /^(\d+)(?: frames?)?$/.exec(value.trim())
            return ordinal && Number.isSafeInteger(Number(ordinal[1])) && Number(ordinal[1]) >= 1 ? Number(ordinal[1]) - (outBoundary ? 0 : 1) : -1
        }
        var match = /^(\d+):(\d{2})\.(\d{3})$/.exec(value.trim())
        var milliseconds = match && Number(match[2]) < 60 ?
               Number(match[1]) * 60000 + Number(match[2]) * 1000 + Number(match[3]) : -1
        return Number.isSafeInteger(milliseconds) && milliseconds <= Math.min(root.state.duration, 2147483647) ? milliseconds : -1
    }
    component Heading: Label {
        color: Theme.textPrimary
        font.pixelSize: 18
        font.weight: Font.DemiBold
    }
    component SectionLabel: Label {
        color: Theme.textMuted
        font.pixelSize: 11
    }
    component NumberBox: NumericField {
        workspace: root.workspace
        numericField: cropField
        onScrubbed: commitValue()
        id: numberField
        property bool invalid: false
        property bool compact: false
        property bool cropField: false
        property string displayedValue: ""
        Connections {
            target: root.workspace
            function onCropReset() {
                if (numberField.cropField) {
                    numberField.text = numberField.displayedValue
                    numberField.focus = false
                    numberField.invalid = false
                    numberField.wheelPlace = ""
                }
            }
        }
        property string mediaId: root.workspace.activeMediaId
        onMediaIdChanged: { invalid = false; focus = false; text = displayedValue }
        onReadOnlyChanged: if (readOnly) { invalid = false; text = displayedValue }
        text: displayedValue
        onDisplayedValueChanged: if (!activeFocus) text = displayedValue
        onActiveFocusChanged: { wheelPlace = ""; if (!activeFocus && !invalid) text = displayedValue }
        selectByMouse: true
        color: invalid ? Theme.error : Theme.textPrimary
        font.pixelSize: 12
        font.features: { "tnum": 1 }
        leftPadding: compact ? 0 : 14
        topPadding: 2
        bottomPadding: 2
        verticalAlignment: TextInput.AlignVCenter
        background: Rectangle {
            radius: parent.compact ? 6 : 12
            color: parent.compact ? "transparent" : Qt.rgba(247/255,249/255,252/255,0.025)
            border.color: parent.invalid ? Theme.error : parent.activeFocus ? Theme.accent : Theme.border
            border.width: parent.compact && !parent.invalid && !parent.activeFocus ? 0 : 1
        }
    }
    component ToggleRow: Basic.Switch {
        id: toggleControl
        property bool showOffLabel: false
        width: content.width
        height: 48
        leftPadding: 16
        rightPadding: showOffLabel ? 90 : 72
        Accessible.name: text
        contentItem: Label {
            text: toggleControl.text
            color: Theme.textPrimary
            opacity: toggleControl.enabled ? 0.64 : 0.34
            font.pixelSize: 12
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 15; color: Qt.rgba(247/255,249/255,252/255,0.03)
            border.color: toggleControl.activeFocus ? Theme.accent : Theme.border
        }
        indicator: Rectangle {
            x: toggleControl.width - 54; y: 12
            width: 38; height: 24; radius: 12
            color: toggleControl.checked ? Theme.accent : Theme.selectionStrong
            opacity: toggleControl.enabled ? 1 : 0.4
            Rectangle {
                x: toggleControl.checked ? 17 : 3; y: 3
                width: 18; height: 18; radius: 9
                color: toggleControl.enabled ? Theme.textPrimary : Theme.textMuted
                Behavior on x { NumberAnimation { duration: 120 } }
            }
        }
        Label {
            visible: toggleControl.showOffLabel
            x: parent.width - 82; anchors.verticalCenter: parent.verticalCenter
            text: "Off"; color: Theme.textMuted; font.pixelSize: 11
        }
    }
    component ResultSummary: Item {
        readonly property var settings: root.state
        Label {
            id: resultTitle
            text: "Result"
            color: Theme.textDisabled
            font.pixelSize: 12
            font.weight: Font.DemiBold
        }
        Rectangle {
            y: 28
            width: parent.width
            height: 210
            radius: 18
            color: Qt.rgba(247/255,249/255,252/255,0.022)
            border.color: Theme.border
            Column {
                x: 16; y: 16; spacing: 18; width: parent.width - 32
                Repeater {
                    model: [
                        { label: "Trim", value: settings.trimEnabled ? root.time(settings.trimIn) + " → " + root.time(settings.trimOut, true) : "Off" },
                        { label: "Crop", value: settings.cropEnabled ? settings.cropWidth + "×" + settings.cropHeight : "Off" },
                        { label: "Reformat", value: settings.resizeEnabled ? (settings.reformatValid ? settings.reformatOutputWidth + "×" + settings.reformatOutputHeight : "Invalid") : "Off" },
                        { label: "Output", value: root.workspace.output.summary.audioOnly ? "Audio only" : root.workspace.output.summary.valid ? root.workspace.output.summary.width + "×" + root.workspace.output.summary.height : "Invalid" }
                    ]
                    Row {
                        required property var modelData
                        width: parent.width
                        spacing: 16
                        Label { width: 72; text: modelData.label; color: Theme.textMuted; font.pixelSize: 11 }
                        Label { width: Math.max(10, parent.width - 88); elide: Text.ElideRight; text: modelData.value; color: Theme.textPrimary; font.pixelSize: 12 }
                    }
                }
            }
        }
    }

    Basic.ScrollView {
        id: inspectorScroll
        objectName: "inspectorScroll"
        anchors.fill: parent
        anchors.margins: 22
        anchors.rightMargin: 10
        // Real gutter outside the Flickable viewport, sized from the scrollbar.
        rightPadding: inspectorBar.implicitWidth
        anchors.topMargin: 18
        anchors.bottomMargin: exportActions.visible ? exportActions.height + 34 : 22
        contentWidth: availableWidth
        contentHeight: content.height
        Basic.ScrollBar.horizontal.policy: Basic.ScrollBar.AlwaysOff
        Basic.ScrollBar.vertical: Basic.ScrollBar {
            id: inspectorBar
            objectName: "inspectorVerticalBar"
            parent: inspectorScroll
            width: implicitWidth
            x: inspectorScroll.width - width
            y: inspectorScroll.topPadding
            height: inspectorScroll.availableHeight
            policy: Basic.ScrollBar.AsNeeded
        }
        clip: true
        opacity: 1
        Column {
            id: content
            objectName: "inspectorContent"
            width: inspectorScroll.availableWidth - 12
            spacing: 0
            enabled: root.workspace.canOpen
            Row {
                width: parent.width
                height: 36
                Heading { width: parent.width - 78; text: root.operation === "resize" ? "Reformat" : root.operation.charAt(0).toUpperCase() + root.operation.slice(1); anchors.verticalCenter: parent.verticalCenter }
                Basic.Button {
                    id: activeButton
                    enabled: root.workspace.sourceKind === 'audio' ? root.operation === 'trim' : root.workspace.sourceKind === 'video' || root.operation === 'crop' || root.operation === 'resize' || (root.workspace.sourceKind === 'sequence' && root.operation === 'trim')
                    visible: root.operation !== "output"
                    objectName: "activeOperationToggle"
                    width: 78; height: 32
                    Accessible.name: "Enable " + root.operation
                    background: Rectangle {
                        radius: 13
                        color: activeButton.down ? Theme.selectionStrong : root.state[root.operation + "Enabled"] ? Theme.accentSoft : Theme.selection
                        border.color: activeButton.activeFocus ? Theme.accent : root.state[root.operation + "Enabled"] ? Theme.accentBorder : Theme.border
                    }
                    contentItem: Label {
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        text: (root.state[root.operation + "Enabled"] ? "✓ " : "") + "Active"
                        color: Theme.textPrimary
                        font.pixelSize: 11
                    }
                    onClicked: root.workspace.setOperationEnabled(root.operation, !root.state[root.operation + "Enabled"])
                }
            }
            Column {
                visible: root.workspace.sourceKind === "sequence"
                width: parent.width
                height: visible ? 120 : 0
                spacing: 4
                Label { text: "Sequence FPS · " + (root.workspace.sequenceInfo.confirmed ? "confirmed" : "ASSUMED"); color: Theme.textMuted }
                FpsInput {
                    id: sequenceRate
                    enabled: root.workspace.canOpen
                    objectName: "sequenceFpsEditor"
                    width: parent.width
                    value: root.workspace.sequenceInfo.fps || "24"
                    exactValue: root.workspace.sequenceInfo.exactFps || "24"
                    mediaId: root.workspace.activeMediaId
                    onAccepted: root.workspace.setSequenceFps(submission)
                    onActivated: root.workspace.setSequenceFps(submission)
                    Accessible.name: "Sequence FPS; Enter to apply"
                }
                Label { text: "First and last frames are included."; color: Theme.textMuted; font.pixelSize: 10 }
                Label { text: root.workspace.sequenceInfo.error || ""; color: Theme.error; wrapMode: Text.Wrap; width: parent.width; font.pixelSize: 10 }
            }

            Item {
                width: parent.width; height: root.operation === "trim" ? 306 : 0
                visible: root.operation === "trim"
                opacity: root.state.trimEnabled ? 1 : 0.4
                SectionLabel { text: "Range"; y: 20 }
                Rectangle {
                    y: 48; width: parent.width; height: 90; radius: 16
                    color: Qt.rgba(247/255,249/255,252/255,0.03); border.color: Theme.border
                    Row { x: 16; y: 10; spacing: 12
                        SectionLabel { text: root.workspace.sourceKind === "sequence" ? "First" : "In"; width: 40 }
                        NumberBox {
                            id: trimIn
                            objectName: "trimInInput"
                            readOnly: !root.state.trimEnabled
                            width: Math.max(60, content.width - 84); height: 22
                            compact: true
                            font.pixelSize: 13
                            font.weight: Font.DemiBold
                            displayedValue: root.time(root.state.trimIn)
                            onEditingFinished: {
                                if (readOnly) return
                                var value = root.parseTime(text)
                                invalid = value < 0 || !root.workspace.setTrimBoundary(false, value)
                            }
                        }
                    }
                    SectionLabel {
                        objectName: "trimInFrame"; x: 68; y: 32
                        text: root.trimInfo.startMs !== undefined ? root.timeText(root.trimInfo.startMs) :
                              root.trimInfo.first ? "Frame " + root.trimInfo.first + " included" : root.trimInfo.pending || ""
                    }
                    Row { x: 16; y: 49; spacing: 12
                        SectionLabel { text: root.workspace.sourceKind === "sequence" ? "Last" : "Out"; width: 40 }
                        NumberBox {
                            id: trimOut
                            objectName: "trimOutInput"
                            readOnly: !root.state.trimEnabled
                            width: Math.max(60, content.width - 84); height: 22
                            compact: true
                            font.pixelSize: 13
                            font.weight: Font.DemiBold
                            displayedValue: root.time(root.state.trimOut, true)
                            onEditingFinished: {
                                if (readOnly) return
                                var value = root.parseTime(text, true)
                                invalid = value < 0 || !root.workspace.setTrimBoundary(true, value)
                            }
                        }
                    }
                    SectionLabel {
                        objectName: "trimOutFrame"; x: 68; y: 71
                        text: root.trimInfo.endMs !== undefined ? root.timeText(root.trimInfo.endMs) + " boundary" :
                              root.trimInfo.last ? "After frame " + root.trimInfo.last : ""
                    }
                }
                Row {
                    y: 154; spacing: 14
                    Basic.Button {
                        id: setIn
                        objectName: "setTrimInButton"
                        enabled: !!root.state.trimEnabled
                        width: (content.width - 14) / 2; height: 44
                        Accessible.name: "Set In"
                        onClicked: root.workspace.setTrimInAtPlayhead(false)
                        background: Rectangle {
                            radius: 14
                            color: setIn.down || setIn.hovered ? Theme.selectionStrong : Qt.rgba(247/255,249/255,252/255,0.035)
                            border.color: setIn.activeFocus ? Theme.accent : Theme.border
                        }
                        contentItem: Label { horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; text: "Set In"; color: Theme.textPrimary; font.pixelSize: 12; font.weight: Font.DemiBold }
                        MouseArea {
                            anchors.fill: parent
                            acceptedButtons: Qt.RightButton
                            onClicked: { setIn.forceActiveFocus(); root.workspace.setTrimInAtPlayhead(true) }
                        }
                    }
                    Basic.Button {
                        id: setOut
                        objectName: "setTrimOutButton"
                        enabled: !!root.state.trimEnabled
                        width: (content.width - 14) / 2; height: 44
                        Accessible.name: "Set Out"
                        onClicked: root.workspace.setTrimOutAtPlayhead(false)
                        background: Rectangle {
                            radius: 14
                            color: setOut.down || setOut.hovered ? Theme.selectionStrong : Qt.rgba(247/255,249/255,252/255,0.035)
                            border.color: setOut.activeFocus ? Theme.accent : Theme.border
                        }
                        contentItem: Label { horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; text: "Set Out"; color: Theme.textPrimary; font.pixelSize: 12; font.weight: Font.DemiBold }
                        MouseArea {
                            anchors.fill: parent
                            acceptedButtons: Qt.RightButton
                            onClicked: { setOut.forceActiveFocus(); root.workspace.setTrimOutAtPlayhead(true) }
                        }
                    }
                }
                SectionLabel { y: 215; text: "Selected Duration" }
                Label {
                    objectName: "trimSelectedDuration"; y: 234
                    text: root.timeText(root.trimInfo.durationMs)
                    color: Theme.textPrimary; font.pixelSize: 13; font.weight: Font.DemiBold
                }
                SectionLabel {
                    objectName: "trimSelectedFrames"; y: 256
                    text: root.trimInfo.count !== undefined ? root.trimInfo.count + (root.trimInfo.count === 1 ? " frame" : " frames") : root.trimInfo.pending || ""
                }
            }

            Item {
                width: parent.width; height: root.operation === "crop" ? 380 : 0
                visible: root.operation === "crop"
                SectionLabel { text: "Aspect"; y: 16 }
                Rectangle {
                    y: 40; width: parent.width; height: 48; radius: 14
                    color: Qt.rgba(247/255,249/255,252/255,0.025); border.color: Theme.border
                    Label { x: 14; anchors.verticalCenter: parent.verticalCenter; text: "Free"; color: Theme.textPrimary; font.pixelSize: 13 }
                }
                SectionLabel { text: "Frame"; y: 106 }
                Basic.Button {
                    objectName: "resetCropButton"
                    x: parent.width - width; y: 92
                    width: 64; height: 32
                    text: "Reset"
                    Accessible.name: "Reset Crop"
                    onClicked: root.workspace.resetCrop()
                    background: Rectangle { radius: 10; color: parent.hovered ? Theme.selectionStrong : Theme.surface; border.color: parent.activeFocus ? Theme.accent : Theme.border }
                    contentItem: Label { text: parent.text; color: Theme.textPrimary; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                }
                Grid {
                    y: 122; width: parent.width; columns: 2; spacing: 12
                    Repeater {
                        model: ["X", "Y", "W", "H"]
                        Item {
                            required property string modelData
                            width: (content.width - 12) / 2; height: 60
                            SectionLabel { text: modelData }
                            NumberBox {
                                id: numeric
                                objectName: "crop" + modelData + "Input"
                                y: 22; width: parent.width; height: 44
                                cropField: true
                                displayedValue: String(modelData === "X" ? (root.state.cropX || 0) :
                                             modelData === "Y" ? (root.state.cropY || 0) :
                                             modelData === "W" ? (root.state.cropWidth || 0) : (root.state.cropHeight || 0))
                                onEditingFinished: commitValue()
                                function commitValue() {
                                    var value = Number(text)
                                    var x = modelData === "X" ? value : root.state.cropX
                                    var y = modelData === "Y" ? value : root.state.cropY
                                    var w = modelData === "W" ? value : root.state.cropWidth
                                    var h = modelData === "H" ? value : root.state.cropHeight
                                    invalid = !/^\d+$/.test(text) || !Number.isSafeInteger(value)
                                              || value > Math.max(root.state.sourceWidth, root.state.sourceHeight)
                                              || !root.workspace.setCropRect(x, y, w, h)
                                }
                            }
                        }
                    }
                }
                ToggleRow {
                    objectName: "cropPreviewToggle"
                    y: 292
                    text: "Preview cropped area"
                    checked: !!root.state.cropPreview
                    onToggled: root.workspace.setCropPreview(checked)
                }
            }

            ReformatInspector {
                width: parent.width
                visible: root.operation === "resize"
                height: visible ? implicitHeight : 0
                workspace: root.workspace
            }
            OutputInspector {
                id: outputInspector
                width: parent.width
                visible: root.operation === "output"
                height: visible ? implicitHeight : 0
                workspace: root.workspace
            }
            ResultSummary {
                width: parent.width
                height: 230
            }
        }
        ParallelAnimation {
            id: reveal
            NumberAnimation { target: content; property: "opacity"; from: 0; to: 1; duration: 180; easing.type: Easing.OutCubic }
            // Fade without translating form controls into the scrollbar gutter.
        }
    }
    Connections {
        target: root.Window.window
        function onActiveFocusItemChanged() {
            var item=root.Window.window.activeFocusItem
            if (!item) return
            var node=item
            while (node && node !== content) node=node.parent
            if (!node) return
            var point=item.mapToItem(content,0,0)
            var top=inspectorScroll.contentItem.contentY
            if (point.y < top) inspectorScroll.contentItem.contentY=Math.max(0,point.y)
            else if (point.y+item.height > top+inspectorScroll.availableHeight) inspectorScroll.contentItem.contentY=Math.max(0,point.y+item.height-inspectorScroll.availableHeight)
        }
    }
    Loader {
        id: exportActions
        objectName: "outputActionArea"
        visible: root.operation === "output" || root.workspace.output.busy
        sourceComponent: outputInspector.actions
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.leftMargin: 22
        anchors.rightMargin: 10 + inspectorBar.implicitWidth + 12
        anchors.bottomMargin: 22
        height: item ? item.implicitHeight : 44
    }
}
