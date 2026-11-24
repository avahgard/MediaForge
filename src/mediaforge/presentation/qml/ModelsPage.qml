import QtQuick
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import "."

Item {
    id: page
    required property var model
    required property bool compact
    property bool active: visible
    property Item opener
    property int announcedTerminal: -1
    readonly property var operation: model.operation
    readonly property var rows: model.components
    readonly property var failedRow: rows.find(row => row.id === operation.component)
    readonly property var damaged: rows.find(row => recovery(row))
    readonly property int installed: rows.filter(row => row.state === "installed_unqualified"
                                                      || row.state === "verified_unqualified").length
    readonly property bool allVerified: rows.length === 6 && rows.every(row => row.state === "verified_unqualified")
    readonly property bool retry: operation.state === "failed" && !!failedRow && !damaged
                                  && ["apply", "plan"].includes(operation.action)
                                  && !["nothing_to_install", "storage_refused"].includes(operation.code)
    readonly property bool wholeScroll: inventoryHeight < 88
    readonly property real inventoryHeight: height - header.height - 6
    objectName: "modelsPage"
    Accessible.role: Accessible.Pane
    Accessible.name: "Dub Models"

    function recovery(row) {
        return row.state === "corrupt" || row.state === "incomplete"
    }

    function prepare(target, repair, trigger) {
        opener = trigger
        if (model.prepare(target, repair))
            phaseStatus.forceActiveFocus(Qt.OtherFocusReason)
    }

    function maybeOpenPlan() {
        if (active && model.plan.figures && !consent.opened)
            consent.open()
    }

    function announceStatus() {
        if (active && phaseStatus.text)
            phaseStatus.Accessible.announce(phaseStatus.text)
    }

    onActiveChanged: {
        if (active) {
            model.reloadInventory()
            maybeOpenPlan()
            announceStatus()
        }
    }
    Connections {
        target: page.model
        function onChanged() {
            page.maybeOpenPlan()
            if (!page.active || page.model.busy)
                return
            if (page.model.message && ["completed", "failed", "cancelled"].includes(page.operation.state)
                    && page.operation.generation !== page.announcedTerminal) {
                page.announcedTerminal = page.operation.generation
                phaseStatus.Accessible.announce(page.model.message)
            }
            if (!consent.visible && phaseStatus.activeFocus) {
                if (page.opener && page.opener.visible && page.opener.enabled)
                    page.opener.forceActiveFocus(Qt.TabFocusReason)
                else if (principal.visible && principal.enabled)
                    principal.forceActiveFocus(Qt.TabFocusReason)
                else if (verify.enabled)
                    verify.forceActiveFocus(Qt.TabFocusReason)
            }
        }
    }

    component Copy: Basic.Label {
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

    SettingsScroll {
        id: body
        anchors.fill: parent
        interactive: page.wholeScroll
        scrollbar: page.wholeScroll
        contentHeight: page.wholeScroll ? header.height + 6 + inventory.contentHeight : height
        ColumnLayout {
            id: header
            width: body.width - (page.wholeScroll ? 16 : 0)
            spacing: 10
            Copy { text: "Dub Models"; font.pixelSize: 18; font.weight: Font.DemiBold; Accessible.role: Accessible.Heading }
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: Math.max(page.compact && page.installed === 0 && !page.damaged ? 142 : 111,
                                         summary.implicitHeight + 24)
                radius: 16
                color: Theme.viewerFrame
                border.color: Theme.border

                ColumnLayout {
                    id: summary
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 14
                    spacing: 5
                    RowLayout {
                        Layout.fillWidth: true
                        Copy {
                            id: phaseStatus
                            Layout.fillWidth: true
                            activeFocusOnTab: false
                            font.pixelSize: 16
                            font.weight: Font.DemiBold
                            color: activeFocus ? Theme.accentBright : Theme.textPrimary
                            text: page.operation.state === "cancelling" ? "Cancelling…"
                                  : page.model.busy ? ({
                                      checking: "Checking", download: "Downloading", install: "Installing", verify: "Verifying"
                                  }[page.operation.phase] || "Checking") + (page.failedRow ? " · " + page.failedRow.name : "…")
                                  : page.damaged ? page.damaged.status + " · " + page.damaged.name
                                  : page.retry ? "Setup failed · " + page.failedRow.name
                                  : page.allVerified ? "Components verified"
                                  : page.installed === 6 ? "Verification pending"
                                  : page.installed ? "Complete Dub setup" : "Set up dubbing"
                            onTextChanged: Qt.callLater(page.announceStatus)
                        }
                        ForgeButton {
                            id: principal
                            objectName: "installDubModelsButton"
                            Layout.minimumWidth: 76
                            primary: !page.model.busy
                            surroundingSurface: Theme.viewerFrame
                            visible: page.model.busy || !!page.damaged || page.retry || page.installed < 6
                            text: page.model.busy ? page.operation.state === "cancelling" ? "Cancelling…" : "Cancel"
                                  : page.damaged ? "Repair" : page.retry ? "Retry"
                                  : page.installed ? "Install" : "Install All"
                            enabled: page.model.busy ? page.operation.state !== "cancelling" : page.model.available
                            onClicked: {
                                if (page.model.busy) {
                                    page.model.cancel()
                                    phaseStatus.forceActiveFocus(Qt.OtherFocusReason)
                                } else {
                                    page.prepare(page.damaged ? page.damaged.id : page.retry ? page.failedRow.id : "",
                                                 !!page.damaged, principal)
                                }
                            }
                        }
                    }
                    Caption {
                        Layout.fillWidth: true
                        visible: text.length > 0
                        text: page.model.busy ? page.operation.object_name || ""
                              : page.model.admission || page.model.message
                              || (page.installed < 6 ? "Install the components needed for English → Russian dubbing." : "")
                        color: page.operation.state === "failed" ? Theme.error : Theme.textSecondary
                        wrapMode: Text.WrapAnywhere
                    }
                    ForgeProgress {
                        Layout.fillWidth: true
                        visible: page.model.busy
                        indeterminate: page.operation.total === 0
                        animateIndeterminate: false
                        from: 0
                        to: Math.max(1, page.operation.total)
                        value: page.operation.received
                        Accessible.name: phaseStatus.text
                    }
                    Caption {
                        visible: page.model.busy && page.operation.total > 0
                        text: Math.floor(100 * page.operation.received / Math.max(1, page.operation.total)) + "%"
                    }
                    Copy {
                        Layout.fillWidth: true
                        visible: !page.model.busy && page.installed === 0 && !page.damaged && !page.retry
                        text: page.model.requirementsText
                        font.weight: Font.DemiBold
                    }
                    Caption {
                        Layout.fillWidth: true
                        visible: !page.model.busy && page.installed < 6 && !page.damaged && !page.retry
                        text: "Qwen requires an NVIDIA GPU with a compatible signed driver."
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: page.compact ? 15 : 7
                Copy { Layout.fillWidth: true; text: "Components"; font.pixelSize: 14; font.weight: Font.DemiBold }
                ForgeButton {
                    id: verify
                    objectName: "settingsVerifyOffline"
                    implicitHeight: 34
                    text: "Verify offline"
                    enabled: page.model.available
                    onClicked: {
                        page.opener = verify
                        if (page.model.verify())
                            phaseStatus.forceActiveFocus(Qt.OtherFocusReason)
                    }
                }
            }
        }

        SettingsScroll {
            id: inventory
            objectName: "settingsModelInventory"
            y: header.height + 6
            width: header.width
            height: page.wholeScroll ? contentHeight : Math.max(0, page.inventoryHeight)
            contentHeight: inventoryRows.height
            interactive: !page.wholeScroll
            scrollbar: !page.wholeScroll
            onEndChanged: if (!moving) moveTo(contentY)

            Column {
                id: inventoryRows
                width: inventory.width - (inventory.scrollbar && inventory.end > 0 ? 16 : 0)
                Repeater {
                    model: page.rows.length
                    Column {
                        id: entry
                        required property int index
                        readonly property var row: page.rows[index]
                        property bool expanded: false
                        width: inventoryRows.width
                        function reveal() {
                            if (page.wholeScroll)
                                body.reveal(entry)
                            else
                                inventory.reveal(entry)
                        }
                        Rectangle { width: parent.width; height: 1; color: Theme.border }
                        Basic.Button {
                            id: disclosure
                            objectName: "settingsModel-" + entry.row.id
                            width: parent.width; height: page.compact ? 43 : 47
                            horizontalPadding: 10; verticalPadding: 3
                            text: entry.row.name
                            Accessible.name: entry.row.name + ", " + entry.row.status
                            Accessible.description: entry.expanded ? "Expanded" : "Collapsed"
                            onActiveFocusChanged: if (activeFocus) entry.reveal()
                            onClicked: { entry.expanded = !entry.expanded; Qt.callLater(entry.reveal) }
                            Keys.onReturnPressed: clicked()
                            Keys.onEnterPressed: clicked()
                            background: Rectangle {
                                radius: 12
                                color: disclosure.hovered ? Theme.selection : "transparent"
                                border.width: disclosure.visualFocus ? 2 : 0
                                border.color: Theme.accentBright
                            }
                            contentItem: RowLayout {
                                spacing: 11
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 0
                                    Copy { Layout.fillWidth: true; text: entry.row.name; font.weight: Font.DemiBold; elide: Text.ElideRight; wrapMode: Text.NoWrap }
                                    Caption { Layout.fillWidth: true; text: entry.row.description; elide: Text.ElideRight; wrapMode: Text.NoWrap }
                                }
                                Caption { text: entry.row.size; visible: text.length > 0 }
                                Caption {
                                    text: entry.row.status
                                    color: entry.row.state === "verified_unqualified" ? Theme.success
                                           : page.recovery(entry.row) ? Theme.warning : Theme.textSecondary
                                }
                                Caption {
                                    text: entry.expanded ? "⌃" : "⌄"
                                    font.pixelSize: 16
                                    Accessible.ignored: true
                                }
                            }
                        }
                        RowLayout {
                            visible: entry.expanded
                            width: parent.width
                            spacing: 8
                            TextEdit {
                                Layout.fillWidth: true
                                Layout.leftMargin: 10
                                Layout.topMargin: 5
                                Layout.bottomMargin: 5
                                text: "Revision · " + entry.row.revision
                                textFormat: TextEdit.PlainText
                                readOnly: true
                                selectByMouse: true
                                wrapMode: TextEdit.Wrap
                                color: Theme.textSecondary
                                font.family: Theme.fontFamily
                                font.pixelSize: 12
                                Accessible.name: "Revision for " + entry.row.name
                                Accessible.description: entry.row.revision
                            }
                            ForgeButton {
                                implicitHeight: 34
                                visible: page.recovery(entry.row) || entry.row.state === "missing"
                                enabled: page.model.available
                                text: page.recovery(entry.row) ? "Repair"
                                      : page.retry && page.failedRow.id === entry.row.id ? "Retry" : "Install"
                                Accessible.name: text + " " + entry.row.name
                                onActiveFocusChanged: if (activeFocus) entry.reveal()
                                onClicked: page.prepare(entry.row.id, page.recovery(entry.row), this)
                            }
                        }
                    }
                }
                Rectangle { width: parent.width; height: 1; color: Theme.border }
            }
        }
    }

    ModelConsent {
        id: consent
        plan: page.model.plan
        available: page.model.available
        onConfirmed: {
            acceptedPlan = true
            if (page.model.confirm())
                close()
            else
                acceptedPlan = false
        }
        onRejected: page.model.dismiss()
        onClosed: {
            if (!page.active)
                return
            if (!acceptedPlan && page.opener && page.opener.enabled && page.opener.visible)
                page.opener.forceActiveFocus(Qt.TabFocusReason)
            else
                phaseStatus.forceActiveFocus(Qt.OtherFocusReason)
            Qt.callLater(page.maybeOpenPlan)
        }
    }
}
