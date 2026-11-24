"""Explicit real acceptance (not collected by pytest); managed runtime only."""

import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ.pop("MEDIAFORGE_FFPROBE_PATH", None)
    os.environ["QML_DISABLE_DISK_CACHE"] = "1"
    if not args.visible:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow
    from native_window_policy import (load_native_shell as load_shell, close_native_windows)
    from mediaforge.bootstrap.application import build_media_view_model
    from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime
    from mediaforge.infrastructure.resources.paths import qml_root

    runtime = resolve_ffmpeg_runtime()
    app = QGuiApplication([])
    logger = logging.getLogger("mediaforge")
    model = build_media_view_model(logger)
    assert model.trim._use_case._executor._runtime == runtime
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("mediaViewModel", model)
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(item.toString() for item in items))
    window = load_shell(engine, qml_root(), logger)

    def wait(condition, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if condition():
                return
            time.sleep(.01)
        raise AssertionError("Acceptance state timeout")

    def run(command):
        return subprocess.run(command, check=True, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", shell=False, timeout=30,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    evidence = {"runtime": {"ffmpeg": str(runtime.ffmpeg), "ffprobe": str(runtime.ffprobe)},
                "versions": [run([str(p), "-version"]).stdout.splitlines()[0] for p in (runtime.ffmpeg, runtime.ffprobe)],
                "scenarios": []}
    try:
        for name, relative, audio, collision, failure in [
            ("normal", "normal/video.mp4", True, False, False),
            ("spaces", "path with spaces/my video.mp4", True, False, False),
            ("unicode", "Папка Кириллица/видео 🎬.mp4", True, False, False),
            ("video-only", "video-only/video.mp4", False, False, False),
            ("collision", "collision/video.mp4", True, True, False),
            ("controlled-failure", "failure/video.mp4", True, False, True),
        ]:
            source = root / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            # Encoding exists ONLY in acceptance fixture generation.
            command = [str(runtime.ffmpeg), "-nostdin", "-v", "error", "-n", "-f", "lavfi",
                       "-i", "testsrc2=size=160x96:rate=25"]
            if audio:
                command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
            command += ["-t", "5", "-c:v", "libx264", "-g", "25", "-keyint_min", "25", "-sc_threshold", "0", "-bf", "0", "-pix_fmt", "yuv420p"]
            if audio:
                command += ["-c:a", "aac"]
            command += [str(source)]
            run(command)
            assert model.openMedia(QUrl.fromLocalFile(str(source)))
            wait(lambda: model.canOpen and model.state != "loading")
            assert model.state == "loaded", model.errorMessage
            original = source.read_bytes()
            existing = source.with_name("video_trim.mp4")
            if collision:
                existing.write_bytes(b"EXISTING USER FILE")
            if failure:
                source.write_bytes(b"deliberately invalid acceptance fixture")
            model.trim.startText, model.trim.endText = "1.0", "3.0"
            assert model.trim.startTrim()
            wait(lambda: not model.trim.busy and model.trim.state in ("Completed", "Failed", "Cancelled"))
            entry = {"scenario": name, "state": model.trim.state, "source": str(source)}
            if failure:
                assert model.trim.state == "Failed" and model.trim.outputPath == ""
                assert list(source.parent.iterdir()) == [source]
                entry["error"] = model.trim._use_case.snapshot().error
            else:
                assert model.trim.state == "Completed", model.trim.message
                output = Path(model.trim.outputPath)
                assert output.is_file() and output.stat().st_size > 0
                probe = json.loads(run([str(runtime.ffprobe), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(output)]).stdout)
                videos = [s for s in probe["streams"] if s["codec_type"] == "video"]
                audios = [s for s in probe["streams"] if s["codec_type"] == "audio"]
                duration = float(probe["format"]["duration"])
                assert len(videos) == 1 and videos[0]["codec_name"] == "h264"
                assert bool(audios) == audio
                assert 1.9 <= duration <= 2.2, duration  # Packet-boundary check, not input epsilon.
                assert source.read_bytes() == original
                assert model.localPath == str(source)
                if collision:
                    assert existing.read_bytes() == b"EXISTING USER FILE"
                    assert output.name == "video_trim_001.mp4"
                entry.update(output=str(output), duration=duration, audio_streams=len(audios), video_streams=len(videos))
            assert not list(source.parent.glob(".mediaforge-trim-*"))
            entry["staging_clean"] = True
            evidence["scenarios"].append(entry)
            if name == "normal":
                for _ in range(10):
                    app.processEvents()
                    time.sleep(.02)
                assert window.grabWindow().save(str(root / "trim-success.png"))
        assert warnings == [], warnings
        evidence["qml_warnings"] = warnings
        (root / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=True, indent=2))
    finally:
        model.shutdown()
        close_native_windows(window)
        del engine
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
