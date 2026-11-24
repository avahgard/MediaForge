"""Run the real composition root, capture its window, then request normal close.

Use --visible for the Windows desktop check; default is offscreen.
All runtime data goes into the supplied --output directory.
"""
import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ['MEDIAFORGE_DATA_ROOT'] = str(output / 'Data/v2')
    os.environ["LOCALAPPDATA"] = str(output / "Local profile")
    os.environ["TEMP"] = str(output / "Temporary profile")
    os.environ["QML_DISABLE_DISK_CACHE"] = "1"
    if not args.visible:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PySide6.QtCore import QTimer
    from PySide6.QtQuick import QQuickWindow
    from mediaforge.bootstrap import application

    original_load = application.load_shell
    checks = []

    def load_and_capture(engine, source, logger):
        window = original_load(engine, source, logger)

        def capture_and_close():
            try:
                image = window.grabWindow()
                checks.append(window.isVisible() and not image.isNull())
                checks.append(image.save(str(output / "shell.png")))
                print(f"QML source: {source}; visible: {window.isVisible()}; capture: {image.size()}")
            finally:
                window.close()

        QTimer.singleShot(1500, capture_and_close)
        return window

    application.load_shell = load_and_capture
    result = application.main(["mediaforge"])
    return result if checks and all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
