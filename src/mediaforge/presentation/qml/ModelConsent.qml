import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import "."

Basic.Popup {
    id: consent
    required property var plan
    required property bool available
    signal confirmed()
    signal rejected()
    property bool acceptedPlan: false
    objectName: "modelsInstallConfirmation"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: parent ? Math.min(560, parent.width - 48) : 560
    height: parent ? Math.min(640, parent.height - 64) : 496
    modal: true
    focus: true
    padding: 20
    closePolicy: Popup.CloseOnEscape
    Overlay.modal: Rectangle { color: Qt.rgba(11/255, 13/255, 17/255, Theme.light ? 0.58 : 0.55) }
    background: Rectangle { radius: Theme.radiusPanel; color: Theme.popupSurface; border.color: Theme.border }
    onOpened: {
        acceptedPlan = false
        viewport.savedPosition = 0
        viewport.moveTo(0)
        cancel.forceActiveFocus(Qt.TabFocusReason)
    }
    onAboutToHide: if (!acceptedPlan) rejected()

    component Copy: Label {
        Layout.fillWidth: true
        color: Theme.textPrimary
        font.family: Theme.fontFamily
        font.pixelSize: 13
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }
    component Caption: Copy {
        color: Theme.textSecondary
        font.pixelSize: 12
    }
    component Rule: Rectangle {
        Layout.fillWidth: true
        implicitHeight: 1
        color: Theme.border
    }

    contentItem: Item {
      ColumnLayout {
        anchors.fill: parent
        spacing: 8
        Copy {
            text: consent.plan.manifest_only ? "Repair verification manifest?"
                  : consent.plan.repair ? "Repair Dub components?" : "Install Dub components?"
            font.pixelSize: 18
            font.weight: Font.DemiBold
            Accessible.role: Accessible.Heading
        }
        SettingsScroll {
            id: viewport
            objectName: "settingsConsentBody"
            Layout.fillWidth: true
            Layout.fillHeight: true
            activeFocusOnTab: true
            focusScrollbar: true
            Accessible.role: Accessible.Pane
            Accessible.name: "Acquisition plan"
            contentHeight: details.height
            ColumnLayout {
                id: details
                width: viewport.width - 16
                height: implicitHeight
                spacing: 5
                Copy { text: "Pinned revisions"; font.weight: Font.DemiBold }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 14
                    Repeater {
                        model: consent.plan.steps || []
                        ColumnLayout {
                            required property var modelData
                            Layout.preferredWidth: 1
                            Layout.fillWidth: true
                            spacing: 0
                            Copy { text: modelData.name }
                            Caption { text: modelData.revision; wrapMode: Text.WrapAnywhere }
                        }
                    }
                }
                Rule {}
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    rowSpacing: 4
                    columnSpacing: 14
                    Repeater {
                        model: consent.plan.figures || []
                        ColumnLayout {
                            required property var modelData
                            Layout.preferredWidth: 1
                            Layout.fillWidth: true
                            spacing: 2
                            Caption { text: modelData.label }
                            Copy { text: modelData.value; font.weight: Font.DemiBold }
                        }
                    }
                }
                Copy { text: "Verified files reused"; font.weight: Font.DemiBold }
                Caption { text: consent.plan.reuse || "" }
                Caption { text: "Network authorization" }
                Copy { text: consent.plan.network || "" }
                Rule {}
                Copy { text: "Authorized scope"; font.weight: Font.DemiBold }
                Copy { text: consent.plan.scope || "No eligible changes." }
                Caption { visible: !!consent.plan.component_destination; text: "Component destination" }
                SettingsPath {
                    Layout.fillWidth: true
                    visible: !!consent.plan.component_destination
                    label: "Component destination"
                    value: consent.plan.component_destination || ""
                    onFocused: viewport.reveal(this)
                }
                Caption { visible: !!consent.plan.companion_destination; text: "Companion destination" }
                SettingsPath {
                    Layout.fillWidth: true
                    visible: !!consent.plan.companion_destination
                    label: "Companion destination"
                    value: consent.plan.companion_destination || ""
                    onFocused: viewport.reveal(this)
                }
                Copy {
                    visible: !!consent.plan.blocked
                    text: consent.plan.blocked || ""
                    color: Theme.warning
                }
            }
            Rectangle {
                anchors.fill: parent; color: "transparent"; radius: 4; border.width: 2
                border.color: Theme.accentBright; visible: viewport.activeFocus
            }
        }
        Rule {}
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Item { Layout.fillWidth: true }
            ForgeButton {
                id: cancel
                objectName: "settingsConsentCancel"
                Layout.minimumWidth: 76
                text: "Cancel"
                onClicked: consent.close()
            }
            ForgeButton {
                objectName: "settingsConsentConfirm"
                Layout.minimumWidth: 76
                text: consent.plan.repair ? "Repair" : "Install"
                primary: true
                surroundingSurface: Theme.popupSurface
                enabled: consent.available && !consent.plan.blocked
                onClicked: consent.confirmed()
            }
        }
      }
    }
}
