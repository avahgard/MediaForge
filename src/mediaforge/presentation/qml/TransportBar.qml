import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import "."

Rectangle {
    id: root
    required property var playback
    property var workspace: null
    property bool fullscreenMode: false
    readonly property bool compact: width < 520
    signal fullscreenRequested()
    readonly property var settings: workspace && !originalOnly ? workspace.editState : ({})
    property bool originalOnly: false
    readonly property real timelineDuration: Math.max(0, playback.duration)
    function controlsPointer(item, x, y) {
        var point = item.mapToItem(root.parent, x, y)
        root.playback.setControlsZone(point.y >= root.parent.height - 140)
    }
    function takeButtonGrab(button) {
        if (!button.transportGrabbed) { button.transportGrabbed = true; root.playback.beginControlsInteraction() }
    }
    function releaseButtonGrab(button) {
        if (button.transportGrabbed) { button.transportGrabbed = false; root.playback.endControlsInteraction() }
    }
    component FullscreenButton: IconButton {
        id: button
        darkOverlay: true
        property bool transportGrabbed: false
        onPressed: root.takeButtonGrab(button)
        onReleased: root.releaseButtonGrab(button)
        onCanceled: root.releaseButtonGrab(button)
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton
            function inside(mouse) { return mouse.x >= 0 && mouse.x < width && mouse.y >= 0 && mouse.y < height }
            onPressed: function(mouse) {
                button.forceActiveFocus()
                button.down = true
                root.controlsPointer(button, mouse.x, mouse.y)
                root.takeButtonGrab(button)
            }
            onPositionChanged: function(mouse) {
                if (pressed) { button.down = inside(mouse); root.controlsPointer(button, mouse.x, mouse.y) }
            }
            onReleased: function(mouse) {
                button.down = false
                root.controlsPointer(button, mouse.x, mouse.y)
                root.releaseButtonGrab(button)
                if (inside(mouse)) button.clicked()
            }
            onCanceled: { button.down = false; root.releaseButtonGrab(button) }
        }
    }
    radius: fullscreenMode ? 28 : 20
    color: fullscreenMode ? Theme.fullscreenTransport : Theme.transportSurface
    border.color: fullscreenMode ? Theme.fullscreenBorder : Theme.border
    border.width: Theme.strokeWidth

    MouseArea {
        // Unoccupied control-surface space also belongs to transport, never to
        // the Viewer background fallback beneath it.
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onPressed: if (root.fullscreenMode) root.playback.beginControlsInteraction()
        onReleased: function(mouse) {
            if (root.fullscreenMode) { root.controlsPointer(root, mouse.x, mouse.y); root.playback.endControlsInteraction() }
        }
        onCanceled: if (root.fullscreenMode) root.playback.endControlsInteraction()
    }

    RowLayout {
        visible: !root.fullscreenMode
        anchors.fill: parent
        anchors.leftMargin: root.compact ? 10 : 14
        anchors.rightMargin: root.compact ? 10 : 14
        spacing: root.compact ? 8 : 14
        IconButton {
            objectName: "playPauseButton"
            glyphName: root.playback.playing ? "pause" : "play"
            accessibleText: root.playback.playing ? "Pause" : "Play"
            enabled: root.playback.canPlay
            onClicked: root.playback.togglePlayback()
        }
        Item {
            id: timeline
            objectName: "mediaTimeline"
            Layout.fillWidth: true
            Layout.preferredHeight: root.compact ? 62 : 44
            property string dragging: ""
            property real handleIn: root.timelineDuration > 0 ?
                                    (root.settings.trimIn || 0) / root.timelineDuration * track.width : 0
            property real handleOut: root.timelineDuration > 0 ?
                                     (root.settings.trimOut || 0) / root.timelineDuration * track.width : track.width
            function positionAt(x) {
                return Math.round(Math.max(0, Math.min(1, x / Math.max(1, track.width))) * root.timelineDuration)
            }
            Label {
                id: currentTimeLabel
                visible: !root.playback.imageMode || root.playback.sequenceMode
                objectName: "playbackTimeText"
                anchors.left: parent.left
                width: root.compact ? parent.width / 2 - 4 : parent.width * 0.23
                elide: Text.ElideRight
                text: root.playback.positionText
                color: Theme.textDisabled
                font.pixelSize: 12
                font.features: { "tnum": 1 }
            }
            Label {
                id: frameLabel
                objectName: "frameReliabilityText"
                anchors.horizontalCenter: parent.horizontalCenter
                y: root.compact ? 20 : 0
                width: root.compact ? parent.width : parent.width * 0.52
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
                text: root.playback.frameText
                Accessible.description: root.playback.frameStepStatus
                color: Theme.textMuted
                font.pixelSize: 11
                font.features: { "tnum": 1 }
            }
            Label {
                id: durationLabel
                visible: !root.playback.imageMode || root.playback.sequenceMode
                objectName: "playbackDurationText"
                anchors.right: parent.right
                width: root.compact ? parent.width / 2 - 4 : parent.width * 0.23
                horizontalAlignment: Text.AlignRight
                elide: Text.ElideLeft
                text: root.playback.durationText
                color: Theme.textDisabled
                font.pixelSize: 12
                font.features: { "tnum": 1 }
            }
            Rectangle {
                id: track
                visible: !root.playback.imageMode || root.playback.sequenceMode
                anchors.left: parent.left
                anchors.right: parent.right
                y: root.compact ? 50 : 31
                height: 8
                radius: 4
                color: Theme.sliderTrack
                Rectangle {
                    visible: !root.fullscreenMode && !!root.workspace && !root.originalOnly
                    x: timeline.handleIn
                    width: Math.max(0, timeline.handleOut - timeline.handleIn)
                    height: parent.height
                    radius: 3
                    color: root.settings.trimEnabled ? Theme.accent : Theme.accentSoft
                }
                Rectangle {
                    objectName: "timelinePlayhead"
                    x: root.timelineDuration > 0 ? Math.max(0, Math.min(1, root.playback.position / root.timelineDuration)) * parent.width - width / 2 : 0
                    y: -5
                    width: 4
                    height: 16
                    radius: 2
                    color: Theme.accentBright
                }
                Rectangle {
                    objectName: "trimInHandle"
                    opacity: root.settings.trimEnabled ? 1 : 0.35
                    visible: !root.fullscreenMode && !!root.workspace && !root.originalOnly
                    x: timeline.handleIn - width / 2
                    y: -7
                    width: 8
                    height: 20
                    radius: 3
                    color: Theme.accentBright
                }
                Rectangle {
                    objectName: "trimOutHandle"
                    opacity: root.settings.trimEnabled ? 1 : 0.35
                    visible: !root.fullscreenMode && !!root.workspace && !root.originalOnly
                    x: timeline.handleOut - width / 2
                    y: -7
                    width: 8
                    height: 20
                    radius: 3
                    color: Theme.accentBright
                }
            }
            MouseArea {
                anchors.fill: parent
                enabled: root.playback.canPlay && root.timelineDuration > 0
                cursorShape: Qt.PointingHandCursor
                onPressed: function(mouse) {
                    timeline.forceActiveFocus()
                    var x = mouse.x
                    if (!root.fullscreenMode && root.workspace && root.settings.trimEnabled && Math.abs(x - timeline.handleIn) < 11)
                        timeline.dragging = "in"
                    else if (!root.fullscreenMode && root.workspace && root.settings.trimEnabled && Math.abs(x - timeline.handleOut) < 11)
                        timeline.dragging = "out"
                    else {
                        timeline.dragging = "seek"
                        root.playback.seek(timeline.positionAt(x))
                    }
                }
                onPositionChanged: function(mouse) {
                    if (!pressed) return
                    var ms = timeline.positionAt(mouse.x)
                    if (timeline.dragging === "in")
                        root.workspace.setTrimBoundary(false, ms)
                    else if (timeline.dragging === "out")
                        root.workspace.setTrimBoundary(true, ms)
                    else root.playback.seek(ms)
                }
                onReleased: timeline.dragging = ""
            }
        }
        IconButton {
            objectName: "muteButton"
            glyphName: root.playback.muted ? "mute" : "volume"
            accessibleText: root.playback.muted ? "Unmute" : "Mute"
            onClicked: root.playback.toggleMuted()
        }
        Basic.Slider {
            id: volumeSlider
            objectName: "volumeSlider"
            Layout.preferredWidth: root.compact ? 48 : 96
            from: 0; to: 100; stepSize: 1
            value: root.playback.volume
            Accessible.name: "Volume"
            onMoved: root.playback.setVolume(Math.round(value))
            onPressedChanged: { if (root.fullscreenMode) { if (pressed) root.playback.beginControlsInteraction(); else root.playback.endControlsInteraction() } }
            background: Rectangle {
                x: volumeSlider.leftPadding
                y: volumeSlider.topPadding + volumeSlider.availableHeight / 2 - height / 2
                width: volumeSlider.availableWidth
                height: 5
                radius: 3
                color: Theme.sliderTrack
                Rectangle {
                    width: volumeSlider.visualPosition * parent.width
                    height: parent.height
                    radius: parent.radius
                    color: Theme.accent
                }
            }
            handle: Rectangle {
                x: volumeSlider.leftPadding + volumeSlider.visualPosition * (volumeSlider.availableWidth - width)
                y: volumeSlider.topPadding + volumeSlider.availableHeight / 2 - height / 2
                implicitWidth: 14; implicitHeight: 14; radius: 7
                color: Theme.accent
                border.color: Theme.textPrimary
                border.width: volumeSlider.activeFocus ? 2 : 0
            }
        }
        IconButton {
            objectName: "normalTransportFullscreenButton"
            visible: root.fullscreenMode
            glyphName: root.fullscreenMode ? "exit" : "fullscreen"
            accessibleText: root.fullscreenMode ? "Exit fullscreen" : "Enter fullscreen"
            onClicked: root.fullscreenRequested()
        }
    }
    Item {
        anchors.fill: parent
        visible: root.fullscreenMode
        FullscreenButton {
            objectName: "fullscreenPlayPause"
            x: 20; y: 23; width: 48; height: 48
            glyphName: root.playback.playing ? "pause" : "play"
            accessibleText: root.playback.playing ? "Pause" : "Play"
            enabled: root.playback.canPlay
            onClicked: root.playback.togglePlayback()
        }
        Label {
            objectName: "fullscreenTimeText"
            x: 90; y: 30
            text: root.playback.positionText
            color: Theme.fullscreenText
            font.pixelSize: 11
            font.features: { "tnum": 1 }
        }
        Item {
            id: fullTimeline
            objectName: "fullscreenTimeline"
            x: 90; y: 43; width: Math.max(80, parent.width - 432); height: 30
            property string dragging: ""
            readonly property real handleIn: root.timelineDuration > 0 ? (root.settings.trimIn || 0) / root.timelineDuration * width : 0
            readonly property real handleOut: root.timelineDuration > 0 ? (root.settings.trimOut || 0) / root.timelineDuration * width : width
            function positionAt(x) {
                return Math.round(Math.max(0, Math.min(1, x / Math.max(1, width))) * root.timelineDuration)
            }
            Label {
                objectName: "fullscreenFrameReliabilityText"
                anchors.horizontalCenter: parent.horizontalCenter
                y: -22; width: Math.max(0, parent.width - 130)
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
                text: root.playback.frameText
                color: Theme.fullscreenDisabled
                font.pixelSize: 11
                font.features: { "tnum": 1 }
                Accessible.description: root.playback.frameStepStatus
            }
            Rectangle {
                anchors.left: parent.left; anchors.right: parent.right
                y: 11; height: 8; radius: 4
                color: Theme.fullscreenTrack
                Rectangle {
                    objectName: "fullscreenTrimRange"
                    visible: !!root.workspace && !root.originalOnly
                    x: fullTimeline.handleIn
                    width: Math.max(0, fullTimeline.handleOut - fullTimeline.handleIn)
                    height: parent.height; radius: 4
                    color: root.settings.trimEnabled ? Theme.fullscreenAccent : Theme.fullscreenControlHover
                }
                Rectangle {
                    x: root.playback.duration > 0 ? parent.width * root.playback.position / root.playback.duration - width / 2 : 0
                    y: -3; width: 14; height: 14; radius: 7; color: Theme.fullscreenAccent
                }
                Rectangle {
                    objectName: "fullscreenTrimInHandle"
                    visible: !!root.workspace && !root.originalOnly
                    opacity: root.settings.trimEnabled ? 1 : 0.35
                    x: fullTimeline.handleIn - width / 2
                    y: -7; width: 8; height: 20; radius: 3; color: Theme.fullscreenAccent
                }
                Rectangle {
                    objectName: "fullscreenTrimOutHandle"
                    visible: !!root.workspace && !root.originalOnly
                    opacity: root.settings.trimEnabled ? 1 : 0.35
                    x: fullTimeline.handleOut - width / 2
                    y: -7; width: 8; height: 20; radius: 3; color: Theme.fullscreenAccent
                }
            }
            MouseArea {
                anchors.fill: parent
                enabled: root.playback.canPlay && root.playback.duration > 0
                onPressed: function(mouse) {
                    fullTimeline.forceActiveFocus()
                    root.controlsPointer(fullTimeline, mouse.x, mouse.y)
                    root.playback.beginControlsInteraction()
                    if (root.workspace && root.settings.trimEnabled && Math.abs(mouse.x - fullTimeline.handleIn) < 11)
                        fullTimeline.dragging = "in"
                    else if (root.workspace && root.settings.trimEnabled && Math.abs(mouse.x - fullTimeline.handleOut) < 11)
                        fullTimeline.dragging = "out"
                    else { fullTimeline.dragging = "seek"; root.playback.seek(fullTimeline.positionAt(mouse.x)) }
                }
                onReleased: function(mouse) { fullTimeline.dragging = ""; root.controlsPointer(fullTimeline, mouse.x, mouse.y); root.playback.endControlsInteraction() }
                onCanceled: { fullTimeline.dragging = ""; root.playback.endControlsInteraction() }
                onPositionChanged: function(mouse) {
                    if (pressed) {
                        root.controlsPointer(fullTimeline, mouse.x, mouse.y)
                        var ms = fullTimeline.positionAt(mouse.x)
                        if (fullTimeline.dragging === "in") root.workspace.setTrimBoundary(false, ms)
                        else if (fullTimeline.dragging === "out") root.workspace.setTrimBoundary(true, ms)
                        else root.playback.seek(ms)
                    }
                }
            }
        }
        Label {
            x: fullTimeline.x + fullTimeline.width + 16; y: 30
            text: root.playback.durationText
            color: Theme.fullscreenText
            font.pixelSize: 11
            font.features: { "tnum": 1 }
        }
        FullscreenButton {
            objectName: "fullscreenMuteButton"
            x: parent.width - 272; y: 23; width: 48; height: 48
            glyphName: root.playback.muted ? "mute" : "volume"
            accessibleText: root.playback.muted ? "Unmute" : "Mute"
            onClicked: root.playback.toggleMuted()
        }
        Basic.Slider {
            id: fullVolume
            objectName: "fullscreenVolumeSlider"
            x: parent.width - 224; y: 23; width: 96; height: 48
            from: 0; to: 100; stepSize: 1; value: root.playback.volume
            Accessible.name: "Volume"
            onMoved: root.playback.setVolume(Math.round(value))
            MouseArea {
                objectName: "fullscreenVolumePointer"
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton
                function moveVolume(mouse) {
                    root.controlsPointer(fullVolume, mouse.x, mouse.y)
                    var fraction = (mouse.x - fullVolume.leftPadding - 7) / Math.max(1, fullVolume.availableWidth - 14)
                    root.playback.setVolume(Math.round(Math.max(0, Math.min(1, fraction)) * 100))
                }
                onPressed: function(mouse) { fullVolume.forceActiveFocus(); root.playback.beginControlsInteraction(); moveVolume(mouse) }
                onPositionChanged: function(mouse) { if (pressed) moveVolume(mouse) }
                onReleased: function(mouse) { root.controlsPointer(fullVolume, mouse.x, mouse.y); root.playback.endControlsInteraction() }
                onCanceled: { root.playback.setControlsZone(false); root.playback.endControlsInteraction() }
            }
            background: Rectangle {
                x: fullVolume.leftPadding
                y: fullVolume.topPadding + fullVolume.availableHeight / 2 - height / 2
                width: fullVolume.availableWidth; height: 6; radius: 3
                color: Theme.fullscreenTrack
                Rectangle { width: fullVolume.visualPosition * parent.width; height: parent.height; radius: 3; color: Theme.fullscreenAccent }
            }
            handle: Rectangle {
                x: fullVolume.leftPadding + fullVolume.visualPosition * (fullVolume.availableWidth - width)
                y: fullVolume.topPadding + fullVolume.availableHeight / 2 - height / 2
                width: 14; height: 14; radius: 7; color: Theme.fullscreenAccent
                border.color: fullVolume.activeFocus ? Theme.fullscreenText : Theme.fullscreenBorder
                border.width: fullVolume.activeFocus ? 2 : 1
            }
        }
        FullscreenButton {
            objectName: "exitFullscreenButton"
            x: parent.width - 68; y: 23; width: 48; height: 48
            glyphName: "exit"
            accessibleText: "Exit fullscreen"
            onClicked: root.fullscreenRequested()
        }
    }
}
