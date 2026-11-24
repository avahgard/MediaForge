pragma Singleton
import QtQuick

QtObject {
    readonly property string preference: typeof settingsViewModel !== "undefined" ? settingsViewModel.theme : "System"
    property bool light: preference === "Light" || (preference === "System" && Qt.styleHints.colorScheme === Qt.Light)
    readonly property color homeSurface: light ? "#FFFFFF" : "#12161D"
    readonly property color homeText: light ? "#171C26" : Qt.rgba(247/255, 249/255, 252/255, 0.94)
    readonly property color homeSecondary: light ? "#576579" : Qt.rgba(247/255, 249/255, 252/255, 0.42)
    // Home geometry and dark materials from the approved v4 SVG.
    readonly property int windowWidth: 1440
    readonly property int windowHeight: 900
    readonly property int minimumWidth: 800
    readonly property int minimumHeight: 560
    readonly property int spaceSmall: 12
    readonly property int spaceMedium: 18
    readonly property int spacePanel: 24
    readonly property int spaceSection: 34
    readonly property int sidebarWidth: 190
    readonly property int navigationHeight: 44
    readonly property int navigationTop: 74
    readonly property int headerHeight: 78
    readonly property int loadedHeaderHeight: 58
    readonly property int emptyHeight: 520
    readonly property int transportHeight: 66
    readonly property int fullscreenTransportHeight: 76
    readonly property int volumeWidth: 112
    readonly property int radiusControl: 14
    readonly property int radiusPanel: 24
    readonly property int radiusContent: 30
    readonly property int radiusFullscreenTransport: 24
    readonly property int strokeWidth: 1
    readonly property int markerSize: 14
    readonly property string fontFamily: "Segoe UI"
    readonly property int fontBody: 13
    readonly property int fontSmall: 12
    readonly property int fontNavigation: 14
    readonly property int fontBrand: 18
    readonly property int fontTitle: 24
    readonly property int weightMedium: Font.Medium
    readonly property int weightStrong: Font.DemiBold
    readonly property color background: light ? "#F3F5F8" : "#0B0D11"
    readonly property color surface: light ? "#FFFFFF" : "#171B23"
    readonly property color viewerFrame: light ? "#E8EDF5" : "#12161D"
    readonly property color viewerBackground: light ? "#151922" : "#050608"
    readonly property color transportSurface: light ? "#E8EDF5" : "#161B23"
    readonly property color fullscreenBackground: "#050608"
    readonly property color fullscreenTransport: light ? Qt.rgba(232/255, 237/255, 245/255, 0.98) : Qt.rgba(23/255, 27/255, 35/255, 0.94)
    readonly property color fullscreenControl: light ? selection : "#20252F"
    readonly property color fullscreenControlHover: light ? selectionStrong : "#303744"
    readonly property color fullscreenControlPressed: light ? accentSoft : "#3A4352"
    readonly property color fullscreenText: light ? textPrimary : "#E6EBF4"
    readonly property color fullscreenDisabled: light ? textDisabled : "#9BA2B1"
    readonly property color fullscreenBorder: light ? textMuted : "#596575"
    readonly property color fullscreenTrack: light ? "#A8B4C5" : "#343C49"
    readonly property color fullscreenAccent: light ? accent : "#79BEFF"
    readonly property color viewerControl: light ? "#FFFFFF" : fullscreenTransport
    readonly property color viewerControlHover: light ? selection : selectionStrong
    readonly property color viewerControlPressed: selectionStrong
    readonly property color viewerControlSelected: selection
    readonly property color popupSurface: light ? "#FFFFFF" : "#171B23"
    readonly property color sidebar: light ? "#FFFFFF" : "#171B23"
    readonly property color textPrimary: light ? "#171C26" : "#F5F7FC"
    readonly property color textSecondary: light ? "#576579" : "#9BA2B1"
    readonly property color textMuted: light ? "#576579" : "#9BA2B1"
    readonly property color textDisabled: light ? "#6F7788" : "#9BA2B1"
    readonly property color border: light ? "#DDE2EA" : "#333947"
    readonly property color borderStrong: light ? "#DDE2EA" : "#333947"
    readonly property color fieldBorder: light ? "#6D788A" : "#737E91"
    readonly property color warning: light ? "#805500" : "#F2C680"
    readonly property color success: light ? "#147D56" : "#79D7B1"
    readonly property color selection: light ? "#F0F2F6" : "#20252F"
    readonly property color selectionStrong: light ? "#DDE2EA" : "#303744"
    readonly property color accent: light ? "#397BE5" : "#5AA9FF"
    readonly property color accentBright: light ? "#2565C5" : "#79BEFF"
    readonly property color primaryFill: light ? "#2565C5" : accent
    readonly property color primaryHoverFill: light ? "#1F5CB6" : accentBright
    readonly property color primaryPressedFill: light ? "#194C99" : Qt.darker(accent, 1.12)
    readonly property color primaryDisabledFill: light ? "#DDE2EA" : accent
    readonly property color primaryLabel: light ? "#FFFFFF" : background
    readonly property color primaryDisabledLabel: light ? "#506079" : textDisabled
    readonly property color primaryFocusRing: light ? "#2565C5" : accentBright
    readonly property color accentSoft: Qt.rgba(90/255, 169/255, 1, 0.13)
    readonly property color accentBorder: Qt.rgba(90/255, 169/255, 1, 0.25)
    readonly property color sliderTrack: light ? "#DDE2EA" : "#343C49"
    readonly property color error: light ? "#B52936" : "#FF6B72"
    readonly property real opacityPrimary: 0.94
    readonly property real opacityDisabled: 0.48
    readonly property int motionFast: 120
    readonly property int motionNormal: 200
    readonly property int fullscreenControlsMotion: 180
    readonly property int motionEasing: Easing.OutCubic
}
