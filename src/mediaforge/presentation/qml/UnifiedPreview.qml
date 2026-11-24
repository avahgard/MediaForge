import QtQuick
import QtQuick.Controls
import QtQuick.Window
import QtMultimedia
import "."

Item {
    id: root
    required property var workspace
    property var cropHandleHost: root
    signal viewerNavigationRequested()
    readonly property var recipe: workspace.previewPlan
    readonly property var settings: workspace.editState
    readonly property var viewport: workspace.viewerViewport
    readonly property bool videoLoading: workspace.sourceKind === "video" && !workspace.playback.imageMode && !workspace.playback.videoPictureAvailable
    // Upstream uses Qt's actual orientation and pixel shape; downstream uses final SAR.
    readonly property real nativeAspect: videoOutput.contentRect.width / Math.max(0.001, videoOutput.contentRect.height)
    readonly property real codedAspect: Math.max(1, settings.sourceWidth || 1) / Math.max(1, settings.sourceHeight || 1)
    readonly property real sourceAspect: codedAspect * (settings.sourceSar || 1)
    readonly property real nativeDisplayAspect: {
        if ((settings.sourceSar || 1) === 1) return nativeAspect
        // Qt can rasterize SAR into an integer display width (1365 instead of
        // 1365 1/3 for 1024x768 SAR 4:3). Recover exact source pixel shape only
        // within that one-pixel rasterization envelope, retaining Qt's orientation.
        var sourceHeight = Math.max(1, settings.sourceHeight || 1)
        var displayWidth = Math.max(1, settings.sourceWidth || 1) * (settings.sourceSar || 1)
        if (Math.abs(nativeAspect * sourceHeight - displayWidth) <= 1) return sourceAspect
        if (nativeAspect > 0 && Math.abs(sourceHeight / nativeAspect - displayWidth) <= 1) return 1 / sourceAspect
        return nativeAspect
    }
    readonly property real displayCorrection: root.workspace.playback.imageMode ? 1 : recipe.reformatted ? (recipe.displaySar || 1)
        : nativeDisplayAspect / codedAspect
    readonly property real rasterWidth: Math.max(1, recipe.width || 1) * displayCorrection
    readonly property real rasterHeight: Math.max(1, recipe.height || 1)
    readonly property real deviceRatio: Screen.devicePixelRatio
    function configure() { viewport.configure(width, height, rasterWidth, rasterHeight, deviceRatio) }
    onWidthChanged: configure()
    onHeightChanged: configure()
    onRasterWidthChanged: configure()
    onRasterHeightChanged: configure()
    onDeviceRatioChanged: configure()
    Component.onCompleted: configure()
    clip: true
    function requestImageView() {
        if (!workspace.imagePreview || !workspace.playback.imageMode || picture.width <= 0 || picture.height <= 0) return
        var a = root.mapToItem(picture, 0, 0)
        var b = root.mapToItem(picture, root.width, root.height)
        var sx = picture.width / Math.max(1, root.recipe.inputWidth || 1)
        var sy = picture.height / Math.max(1, root.recipe.inputHeight || 1)
        var x = Math.max(0, Math.floor((root.recipe.cropX || 0) + a.x / sx))
        var y = Math.max(0, Math.floor((root.recipe.cropY || 0) + a.y / sy))
        var right = Math.min(root.settings.sourceWidth, Math.ceil((root.recipe.cropX || 0) + b.x / sx))
        var bottom = Math.min(root.settings.sourceHeight, Math.ceil((root.recipe.cropY || 0) + b.y / sy))
        var w = Math.max(1, right-x), h = Math.max(1, bottom-y)
        workspace.imagePreview.setView(workspace.activeMediaId,x,y,w,h,Math.max(1,Math.round(w*sx*deviceRatio)),Math.max(1,Math.round(h*sy*deviceRatio)))
    }
    Timer { id: viewIntent; interval: 40; onTriggered: root.requestImageView() }
    Connections { target: root.viewport; function onChanged() { if (root.workspace.playback.imageMode && !viewIntent.running) viewIntent.start() } }
    Connections { target: root.workspace.playback; function onChanged() { if (root.workspace.playback.imageMode && !viewIntent.running) viewIntent.start() } }

    Item {
        id: canvas
        objectName: "videoViewport"
        x: (root.width - width) / 2 + root.viewport.panX
        y: (root.height - height) / 2 + root.viewport.panY
        width: root.rasterWidth * root.viewport.scale
        height: root.rasterHeight * root.viewport.scale
        visible: root.recipe.valid !== false && !root.videoLoading
        clip: true
        Rectangle { anchors.fill: parent; color: "black" }
        Item {
            id: picture
            x: (root.recipe.x || 0) * root.displayCorrection * root.viewport.scale
            y: (root.recipe.y || 0) * root.viewport.scale
            width: Math.max(.000001, root.recipe.pictureWidth || 1) * root.displayCorrection * root.viewport.scale
            height: Math.max(.000001, root.recipe.pictureHeight || 1) * root.viewport.scale
            clip: true
            Image {
                objectName: 'imageCheckerboard'
                visible: root.workspace.playback.imageMode && root.workspace.playback.imageAlpha
                source: 'checkerboard.svg'
                sourceSize: Qt.size(64, 64)
                width: videoOutput.width; height: videoOutput.height
                x: videoOutput.x; y: videoOutput.y
                fillMode: Image.Tile
                transform: Scale { xScale: videoOutput.sx; yScale: videoOutput.sy }
            }
            VideoOutput {
                id: videoOutput
                objectName: "viewerVideoOutput"
                // Use the native content rect as the source of mapping. Item dimensions
                // are stable across edits; only scene transforms and clipping change.
                readonly property var region: root.workspace.playback.imageRegion
                width: root.workspace.playback.imageMode ? region.width : Math.max(1, root.settings.sourceWidth || 1)
                height: root.workspace.playback.imageMode ? region.height : Math.max(1, root.settings.sourceHeight || 1)
                readonly property real sx: root.workspace.playback.imageMode ? picture.width / Math.max(1,root.recipe.inputWidth || 1) : picture.width / Math.max(.001, contentRect.width) * Math.max(1, root.settings.sourceWidth || 1) / Math.max(1, root.recipe.inputWidth || 1)
                readonly property real sy: root.workspace.playback.imageMode ? picture.height / Math.max(1,root.recipe.inputHeight || 1) : picture.height / Math.max(.001, contentRect.height) * Math.max(1, root.settings.sourceHeight || 1) / Math.max(1, root.recipe.inputHeight || 1)
                x: root.workspace.playback.imageMode ? (region.x - (root.recipe.cropX || 0))*sx : -(contentRect.x + (root.recipe.cropX || 0) / Math.max(1, root.settings.sourceWidth || 1) * contentRect.width) * sx
                y: root.workspace.playback.imageMode ? (region.y - (root.recipe.cropY || 0))*sy : -(contentRect.y + (root.recipe.cropY || 0) / Math.max(1, root.settings.sourceHeight || 1) * contentRect.height) * sy
                transform: Scale { xScale: videoOutput.sx; yScale: videoOutput.sy }
                fillMode: root.workspace.playback.imageMode ? VideoOutput.Stretch : VideoOutput.PreserveAspectFit
                opacity: 1
                visible: !root.workspace.imageTransition &&
                         (root.workspace.playback.imageMode ? root.workspace.playback.imageWidth > 0
                                                           : root.workspace.playback.videoPictureAvailable)
                layer.enabled: root.workspace.playback.imageMode
                layer.textureSize: Qt.size(Math.max(1, root.workspace.playback.imageWidth), Math.max(1, root.workspace.playback.imageHeight))
                layer.smooth: true
                layer.mipmap: false
                layer.samples: 0
                Component.onCompleted: root.workspace.playback.attachVideoOutput(videoOutput)
            }
        }
    }
    Rectangle {
        objectName: "videoLoadingPresentation"
        anchors.fill: parent
        visible: root.videoLoading
        color: Theme.surface
        Label { anchors.centerIn: parent; text: root.workspace.imageTransition ? (root.workspace.imagePreview.busy ? "Preparing image…" : "Preview unavailable") : "Loading video…"; color: Theme.textMuted; font.pixelSize: 12 }
    }
    CropOverlay {
        anchors.fill: parent
        workspace: root.workspace
        videoOutput: videoOutput
        contentHost: canvas
        handleHost: root.cropHandleHost
        displayed: Qt.rect(canvas.x, canvas.y, canvas.width, canvas.height)
    }
    // Observe wheel/middle drag across Crop Edit too. Left-button Crop gestures
    // keep their own grab and never change viewer pan.
    MouseArea {
        objectName: "viewerZoomInput"
        anchors.fill: parent
        acceptedButtons: Qt.MiddleButton
        property real lastX: 0
        property real lastY: 0
        onWheel: function(wheel) {
            root.viewerNavigationRequested()
            var steps = wheel.angleDelta.y ? wheel.angleDelta.y / 120 : wheel.pixelDelta.y / 40
            root.viewport.wheel(wheel.x, wheel.y, steps)
            wheel.accepted = true
        }
        onPressed: function(mouse) {
            root.viewerNavigationRequested()
            lastX = mouse.x
            lastY = mouse.y
        }
        onPositionChanged: function(mouse) {
            if (pressed) { root.viewport.pan(mouse.x - lastX, mouse.y - lastY); lastX = mouse.x; lastY = mouse.y }
        }
    }
    Label {
        anchors.centerIn: parent
        width: parent.width - 30
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        visible: root.recipe.valid === false
        text: "Preview unavailable: " + (root.recipe.error || "Invalid geometry")
        color: Theme.error
    }
}
