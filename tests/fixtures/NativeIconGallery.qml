import QtQuick
import "../../src/mediaforge/presentation/qml" as App

Window {
    visible: true
    width: 550
    height: 64
    color: "#0B0D11"
    Row {
        y: 8
        spacing: 12
        Repeater {
            model: ["play", "pause", "mute", "volume", "fullscreen", "info", "add", "exit"]
            App.IconButton {
                required property string modelData
                objectName: "testIcon" + modelData
                glyphName: modelData
                width: 48
                height: 48
            }
        }
    }
}
