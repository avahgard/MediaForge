import QtQuick
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import "."

Item {
    id: page
    required property var settings
    required property var models
    required property bool compact
    property int selectedTab: 1
    readonly property var tabNames: ["Appearance", "Dub Models", "Storage", "About"]
    readonly property var themeNames: ["System", "Dark", "Light"]
    readonly property int inset: compact ? 18 : 22
    objectName: "settingsPage"

    function selectTab(index) {
        selectedTab = index
        tabs.focusSelection()
        if (index === 2)
            settings.refreshStorage()
    }

    onVisibleChanged: if (visible) Qt.callLater(selectTab, selectedTab)
    Component.onCompleted: if (visible) Qt.callLater(selectTab, selectedTab)

    component Copy: Basic.Label {
        Layout.fillWidth: true
        color: Theme.textPrimary
        font.family: Theme.fontFamily
        font.pixelSize: 13
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }
    component Secondary: Copy {
        color: Theme.textSecondary
        font.pixelSize: 12
    }
    component Heading: Copy {
        font.pixelSize: 18
        font.weight: Font.DemiBold
        Accessible.role: Accessible.Heading
    }
    component Rule: Rectangle {
        Layout.fillWidth: true
        implicitHeight: 1
        color: Theme.border
    }
    component Metric: ColumnLayout {
        required property string label
        required property string value
        Layout.fillWidth: true
        spacing: 0
        Rule {}
        RowLayout {
            Layout.fillWidth: true
            Layout.minimumHeight: 32
            Copy { text: parent.parent.label }
            Copy { Layout.fillWidth: false; text: parent.parent.value; font.weight: Font.DemiBold }
        }
    }

    Heading {
        y: 13
        text: "Settings"
        font.pixelSize: 24
    }
    SettingsSelector {
        id: tabs
        y: 56
        tabs: true
        labels: page.tabNames
        selected: page.selectedTab
        onChosen: index => page.selectTab(index)
    }

    Rectangle {
        id: panel
        y: 112
        width: Math.min(880, page.width)
        readonly property real availableHeight: page.height - y
        height: page.selectedTab === 1 ? availableHeight
              : Math.min(availableHeight, Math.max(224, sections[page.selectedTab].contentHeight + 2 * page.inset + 22))
        radius: Theme.radiusPanel
        color: Theme.surface
        border.color: Theme.border
        readonly property var sections: [appearance, null, storage, about]

        ModelsPage {
            anchors.fill: parent
            anchors.margins: page.inset
            visible: page.selectedTab === 1
            active: visible && page.visible
            model: page.models
            compact: page.compact
        }

        SettingsScroll {
            id: appearance
            objectName: "settingsAppearance"
            anchors.fill: parent; anchors.margins: page.inset
            visible: page.selectedTab === 0
            contentHeight: appearanceContent.height
            Accessible.role: Accessible.Pane
            Accessible.name: "Appearance"
            ColumnLayout {
                id: appearanceContent
                width: appearance.width - (appearance.end > 0 ? 16 : 0)
                spacing: 6
                Heading { text: "Appearance" }
                Copy { text: "Choose how MediaForge looks."; color: Theme.textSecondary }
                Copy { text: "Theme"; font.weight: Font.DemiBold }
                SettingsSelector {
                    Layout.topMargin: 2
                    labels: page.themeNames
                    selected: page.themeNames.indexOf(page.settings.theme)
                    onChosen: index => page.settings.setTheme(page.themeNames[index])
                }
                Secondary {
                    Layout.topMargin: 7
                    text: page.settings.theme === "System" ? "Follows the Windows app theme."
                          : page.settings.theme + " is used across MediaForge."
                }
                Secondary { visible: !!text; color: Theme.warning; text: page.settings.message }
            }
        }

        SettingsScroll {
            id: storage
            objectName: "settingsStorage"
            anchors.fill: parent; anchors.margins: page.inset
            visible: page.selectedTab === 2
            contentHeight: storageContent.height
            Accessible.role: Accessible.Pane
            Accessible.name: "Storage"
            ColumnLayout {
                id: storageContent
                width: storage.width - (storage.end > 0 ? 16 : 0)
                spacing: 8
                Heading { text: "Storage" }
                Copy { color: Theme.textSecondary; text: "MediaForge keeps its managed data beside the application." }
                Copy { text: "Portable Data folder"; font.weight: Font.DemiBold }
                SettingsPath {
                    objectName: "settingsDataPath"
                    Layout.fillWidth: true
                    label: "Portable Data folder"
                    value: page.settings.dataPath
                    onFocused: storage.reveal(this)
                }
                ForgeButton {
                    Layout.alignment: Qt.AlignRight
                    Layout.topMargin: 4
                    Layout.bottomMargin: 4
                    text: "Open Data Folder"
                    onActiveFocusChanged: if (activeFocus) storage.reveal(this)
                    onClicked: page.settings.openData()
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    Metric { label: "Installed components"; value: page.settings.storage.models || "Measuring…" }
                    Metric { label: "Downloads and cache"; value: page.settings.storage.cache || "Measuring…" }
                    Metric { label: "Available on Data drive"; value: page.settings.storage.available || "Measuring…" }
                    Rule {}
                }
                Secondary { text: page.settings.storage.sampled || "Measuring…" }
                Secondary { visible: !!text; color: Theme.warning; text: page.settings.message }
            }
        }

        SettingsScroll {
            id: about
            objectName: "settingsAbout"
            anchors.fill: parent; anchors.margins: page.inset
            visible: page.selectedTab === 3
            contentHeight: aboutContent.height
            Accessible.role: Accessible.Pane
            Accessible.name: "About"
            ColumnLayout {
                id: aboutContent
                width: about.width - (about.end > 0 ? 16 : 0)
                spacing: 10
                Heading { text: "About MediaForge" }
                Copy {
                    Layout.topMargin: 3
                    text: page.settings.about.version + " · " + page.settings.about.build
                    font.weight: Font.DemiBold
                }
                Secondary { text: "Windows x64" }
                SettingsPath {
                    Layout.fillWidth: true
                    visible: !!page.settings.about.manifest
                    label: "Authenticated package manifest SHA-256"
                    value: page.settings.about.manifest
                    onFocused: about.reveal(this)
                }
                Rule { Layout.topMargin: 12 }
                RowLayout {
                    Layout.fillWidth: true
                    Copy { text: "Project / GitHub" }
                    Secondary { Layout.fillWidth: false; visible: !page.settings.about.project; text: "Link unavailable in this build" }
                    ForgeButton { visible: page.settings.about.project; text: "Open project"; Accessible.description: "Opens the project website"; onClicked: page.settings.openProject() }
                }
                Rule {}
                RowLayout {
                    Layout.fillWidth: true
                    Copy { text: "Licenses and third-party notices" }
                    Secondary { Layout.fillWidth: false; visible: !page.settings.about.notices; text: "Unavailable in this build" }
                    ForgeButton { visible: page.settings.about.notices; text: "View notices"; onClicked: page.settings.openNotices() }
                }
                Rule {}
                Copy { text: "Diagnostics"; font.weight: Font.DemiBold }
                Secondary { text: "Log folder" }
                SettingsPath {
                    objectName: "settingsLogPath"
                    Layout.fillWidth: true
                    label: "Log folder"
                    value: page.settings.logPath
                    onFocused: about.reveal(this)
                }
            }
        }
    }
}
