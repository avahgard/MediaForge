import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import "."

ColumnLayout {
    id: root
    required property var workspace
    readonly property var settings: workspace.editState
    readonly property real scaleValue: Number(settings.reformatScale)
    readonly property bool scaleNumberValid: isFinite(scaleValue) && scaleValue > 0
    spacing: 10

    component Entry: NumericField {
        workspace: root.workspace
        Layout.minimumWidth: 0
        onScrubbed: invalid = !root.workspace.setReformat(field, text)
        property string value: ""
        property string field: ""
        property bool invalid: false
        property string mediaId: root.workspace.activeMediaId
        text: value
        onValueChanged: if (!activeFocus || field === "scale") { text = value; invalid = false }
        onMediaIdChanged: { focus = false; invalid = false; text = value }
        onActiveFocusChanged: if (!activeFocus && !invalid) text = value
        selectByMouse: true
        color: invalid ? Theme.error : Theme.textPrimary
        font.pixelSize: 12
        padding: 12
        background: Rectangle { radius: 12; color: Theme.surface; border.color: parent.invalid ? Theme.error : parent.activeFocus ? Theme.accent : Theme.border }
        onEditingFinished: invalid = !root.workspace.setReformat(field, text)
    }
    component Choice: Basic.Button {
        property string field: ""
        property string value: ""
        property bool chosen: false
        property string helpText: ""
        Accessible.description: helpText
        Layout.fillWidth: true
        contentItem: Label { text: parent.text; horizontalAlignment: Text.AlignHCenter; color: Theme.textPrimary; padding: 7; font.pixelSize: 12 }
        background: Rectangle { radius: 10; color: parent.chosen ? Theme.accentSoft : Theme.surface; border.color: parent.activeFocus || parent.chosen ? Theme.accent : Theme.border }
        onClicked: root.workspace.setReformat(field, value)
    }
    Item { Layout.preferredHeight: 6 }
    RowLayout {
        Layout.fillWidth: true
        Choice { objectName: "reformatScaleMode"; text: "Scale"; field: "mode"; value: "scale"; chosen: root.settings.reformatMode === value }
        Choice { objectName: "reformatFormatMode"; text: "Format"; field: "mode"; value: "format"; chosen: root.settings.reformatMode === value }
    }
    ColumnLayout {
        Layout.fillWidth: true
        visible: root.settings.reformatMode === "scale"
        Label { text: "Resolution multiplier"; color: Theme.textMuted; font.pixelSize: 11 }
        Entry { objectName: "reformatScaleInput"; Layout.fillWidth: true; value: root.settings.reformatScale || "1.0"; field: "scale" }
        Label { text: root.settings.reformatScale + "x (" + (Number(root.settings.reformatScale) * 100) + "%)"; color: Theme.textSecondary; font.pixelSize: 12 }
        Basic.Slider {
            id: scaleSlider
            objectName: "reformatScaleSlider"
            Layout.fillWidth: true
            from: root.scaleNumberValid ? Math.min(0.01, root.scaleValue) : 0.01
            to: root.scaleNumberValid ? Math.max(4.0, root.scaleValue) : 4.0
            value: root.scaleNumberValid ? root.scaleValue : 1.0
            stepSize: 0.01
            snapMode: Slider.SnapAlways
            live: true
            enabled: root.scaleNumberValid
            focusPolicy: Qt.StrongFocus
            Accessible.name: "Reformat resolution multiplier"
            Accessible.description: "Right-click to reset to 1.0x (100%). You can also enter 1.0 in the numeric field."
            onMoved: root.workspace.setReformat("scale", value.toFixed(2))
            implicitHeight: 28
            background: Rectangle {
                x: scaleSlider.leftPadding; y: (scaleSlider.height - height) / 2
                width: scaleSlider.availableWidth; height: 4; radius: 2
                color: Theme.sliderTrack
                Rectangle { width: scaleSlider.visualPosition * parent.width; height: parent.height; radius: 2; color: Theme.accent }
            }
            handle: Rectangle {
                x: scaleSlider.leftPadding + scaleSlider.visualPosition * (scaleSlider.availableWidth - width)
                y: (scaleSlider.height - height) / 2
                width: 14; height: 14; radius: 7
                color: scaleSlider.pressed ? Theme.accentBright : Theme.accent
                border.color: scaleSlider.activeFocus ? Theme.textPrimary : "transparent"
            }
            MouseArea {
                objectName: "reformatScaleResetInput"
                anchors.fill: parent
                acceptedButtons: Qt.RightButton
                preventStealing: true
                onClicked: function(mouse) {
                    mouse.accepted = true
                    scaleSlider.forceActiveFocus()
                    root.workspace.setReformat("scale", "1.0")
                }
            }
        }
        Label { Layout.fillWidth: true; text: "Output resolution; Viewer zoom is independent."; wrapMode: Text.Wrap; color: Theme.textMuted; font.pixelSize: 11 }
    }
    ColumnLayout {
        Layout.fillWidth: true
        visible: root.settings.reformatMode === "format"
        Label { text: "Preset"; color: Theme.textMuted; font.pixelSize: 11 }
        Basic.ComboBox {
            id: presets
            objectName: "reformatPresetInput"
            Layout.fillWidth: true
            model: ["Source", "Custom", "HD", "FHD", "2K DCI", "UHD", "4K DCI", "8K UHD"]
            currentIndex: model.indexOf(root.settings.reformatPreset || "Custom")
            displayText: currentText === "Source" ? "Source — " + root.settings.sourceWidth + "×" + root.settings.sourceHeight : currentText
            onActivated: root.workspace.setReformat("preset", currentText)
            padding: 12
            contentItem: Label { text: presets.displayText; color: Theme.textPrimary; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { radius: 12; color: Theme.surface; border.color: presets.activeFocus ? Theme.accent : Theme.border }
            indicator: Item {
                x: presets.width-24; anchors.verticalCenter: parent.verticalCenter; width: 12; height: 8
                Rectangle { x: 1; y: 2; width: 7; height: 1.5; rotation: 45; color: Theme.textSecondary }
                Rectangle { x: 5; y: 2; width: 7; height: 1.5; rotation: -45; color: Theme.textSecondary }
            }
            delegate: Basic.ItemDelegate {
                width: presets.width
                contentItem: Label { text: modelData === "Source" ? "Source — " + root.settings.sourceWidth + "×" + root.settings.sourceHeight : modelData; color: Theme.textPrimary; font.pixelSize: 12 }
                background: Rectangle { color: parent.highlighted ? Theme.selection : Theme.popupSurface }
                highlighted: presets.highlightedIndex === index
            }
            popup: Basic.Popup {
                parent: Overlay.overlay
                x: Math.max(8,Math.min(presets.mapToItem(Overlay.overlay,0,0).x,Overlay.overlay.width-width-8))
                y: { var p=presets.mapToItem(Overlay.overlay,0,0); return p.y+presets.height+height+8 <= Overlay.overlay.height ? p.y+presets.height+4 : Math.max(8,p.y-height-4) }
                width: presets.width
                height: Math.min(240,contentItem.implicitHeight+12)
                onClosed: presets.forceActiveFocus()
                padding: 6
                background: Rectangle { radius: 12; color: Theme.popupSurface; border.color: Theme.border }
                contentItem: ListView { implicitHeight: contentHeight; model: presets.popup.visible ? presets.delegateModel : null; currentIndex: presets.highlightedIndex; clip: true }
            }
        }
        Label {
            Layout.fillWidth: true; wrapMode: Text.Wrap
            visible: root.settings.reformatPreset === "Source"
            text: "Original opened source canvas; Crop changes the input picture."
            color: Theme.textMuted; font.pixelSize: 11
        }
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                Layout.fillWidth: true
                Label { text: "Width"; color: Theme.textMuted; font.pixelSize: 11 }
                Entry { objectName: "resizeWidthInput"; Layout.fillWidth: true; value: String(root.settings.resizeWidth || 0); field: "width"; enabled: root.settings.reformatPreset === "Custom" }
            }
            Basic.Button {
                id: aspectLock
                objectName: "keepAspectRatioControl"
                Layout.alignment: Qt.AlignBottom
                implicitWidth: 34; implicitHeight: 40
                checkable: true
                checked: !!root.settings.reformatLocked
                enabled: root.settings.reformatPreset === "Custom"
                Accessible.name: "Lock aspect ratio"
                Accessible.description: "Link Width and Height using the effective input's coded aspect ratio."
                onToggled: root.workspace.setReformat("lock", checked ? "true" : "false")
                background: Rectangle { radius: 10; color: aspectLock.checked ? Theme.accentSoft : Theme.surface; border.color: aspectLock.activeFocus || aspectLock.checked ? Theme.accent : Theme.border; opacity: aspectLock.enabled ? 1 : .4 }
                contentItem: Canvas {
                    id: lockGlyph
                    implicitWidth: 22; implicitHeight: 22
                    readonly property color stroke: aspectLock.enabled ? Theme.textPrimary : Theme.textMuted
                    onStrokeChanged: requestPaint()
                    onPaint: {
                        var c = getContext("2d"); c.clearRect(0,0,width,height)
                        c.strokeStyle = stroke
                        c.lineWidth = 1.7; c.lineCap = "round"
                        var x = width/2, y = height/2
                        c.strokeRect(x-6,y-1,12,10)
                        c.beginPath(); c.moveTo(x-4,y-1); c.lineTo(x-4,y-5)
                        c.bezierCurveTo(x-4,y-11,x+4,y-11,x+4,y-5)
                        if (aspectLock.checked) c.lineTo(x+4,y-1)
                        c.stroke()
                    }
                }
                onCheckedChanged: lockGlyph.requestPaint()
            }
            ColumnLayout {
                Layout.fillWidth: true
                Label { text: "Height"; color: Theme.textMuted; font.pixelSize: 11 }
                Entry { objectName: "resizeHeightInput"; Layout.fillWidth: true; value: String(root.settings.resizeHeight || 0); field: "height"; enabled: root.settings.reformatPreset === "Custom" }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Repeater {
                model: ["fit", "fill", "stretch"]
                Choice {
                    required property string modelData
                    objectName: "reformat" + modelData
                    text: modelData.charAt(0).toUpperCase() + modelData.slice(1)
                    field: "placement"; value: modelData; chosen: root.settings.reformatPlacement === value
                    helpText: modelData === "fit" ? "Preserves the whole picture and aspect ratio; the output canvas may have padding."
                              : modelData === "fill" ? "Preserves aspect ratio and fills the output canvas; image edges may be cropped."
                              : "Stretches to fill the target canvas; image proportions may change."
                }
            }
        }
    }
    Label { objectName: "pixelAspectLabel"; text: "Pixel Aspect"; color: Theme.textMuted; font.pixelSize: 11 }
    RowLayout {
        id: pixelRow
        objectName: "pixelAspectRow"
        Layout.fillWidth: true
        Entry { objectName: "pixelAspectInput"; Layout.fillWidth: true; value: root.settings.pixelAspectText || "1.0"; field: "pixel_aspect" }
        Choice { objectName: "pixelAspect169"; text: "16:9"; field: "pixel_aspect"; value: "16:9"; Layout.fillWidth: false; Accessible.name: "Display as 16:9" }
        Choice { objectName: "pixelAspectSource"; text: "↺"; field: "pixel_aspect"; value: "source"; Layout.fillWidth: false; Accessible.name: "Reset Pixel Aspect to source" }
    }
    Label {
        Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.textSecondary; font.pixelSize: 11
        text: "16:9 sets the display shape. Crop removes baked-in bars. Enable Reformat to apply the pixel aspect."
    }
    Label {
        Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.textPrimary; font.pixelSize: 12
        text: root.settings.reformatSar ? "Pixel aspect (SAR) " + root.settings.reformatSar
                                         + " · Display aspect (DAR) " + root.settings.reformatDar : ""
    }
    Label {
        objectName: "reformatOutputDimensions"
        Layout.fillWidth: true
        text: root.settings.reformatValid ? "Coded output " + root.settings.reformatOutputWidth + "×" + root.settings.reformatOutputHeight + (root.settings.reformatScaleQuantized ? " · rounded to even pixels" : "") : "Output unavailable"
        wrapMode: Text.Wrap
        color: Theme.textPrimary; font.pixelSize: 13
    }
    Label {
        Layout.fillWidth: true; wrapMode: Text.Wrap
        text: root.settings.reformatError || ""
        visible: text.length > 0
        color: Theme.error; font.pixelSize: 11
    }
    Basic.Button { text: "Cancel Reformat"; visible: root.workspace.resize.canCancel; onClicked: root.workspace.resize.cancelResize() }
    Label { Layout.fillWidth: true; text: root.workspace.resize.message + (root.workspace.resize.outputPath ? "\n" + root.workspace.resize.outputPath : ""); wrapMode: Text.WrapAnywhere; color: Theme.textSecondary; font.pixelSize: 11 }
    Item { Layout.preferredHeight: 12 }
}
