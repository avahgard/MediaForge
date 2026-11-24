"""Explicit TASK-006 managed-runtime and visible-QML acceptance matrix."""

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import threading
import time


TIMESTAMP_TOLERANCE_SECONDS = 0.0015
INTERVAL_CLASS_TOLERANCE_SECONDS = 0.002


def _relative_timestamps(timestamps):
    if not timestamps:
        raise AssertionError("No video frame timestamps")
    first = timestamps[0]
    return [value - first for value in timestamps]


def _interval_classes(timestamps, tolerance=INTERVAL_CLASS_TOLERANCE_SECONDS):
    relative = _relative_timestamps(timestamps)
    classes = []
    for interval in (relative[index] - relative[index - 1] for index in range(1, len(relative))):
        if not any(abs(interval - existing) <= tolerance for existing in classes):
            classes.append(interval)
    return classes


def assert_vfr_cadence(source_timestamps, output_timestamps,
                       tolerance=TIMESTAMP_TOLERANCE_SECONDS):
    if len(source_timestamps) != len(output_timestamps):
        raise AssertionError("Video frame timestamp count changed")
    if len(_interval_classes(source_timestamps)) < 2:
        raise AssertionError("Source fixture is not demonstrably VFR")
    source_relative = _relative_timestamps(source_timestamps)
    output_relative = _relative_timestamps(output_timestamps)
    errors = [abs(source - output) for source, output in zip(source_relative, output_relative)]
    if any(error > tolerance for error in errors):
        raise AssertionError("Relative video timestamp cadence changed")
    return max(errors, default=0.0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args()
    root = args.output.resolve(); root.mkdir(parents=True, exist_ok=True)
    os.environ.pop("MEDIAFORGE_FFPROBE_PATH", None)
    os.environ["QML_DISABLE_DISK_CACHE"] = "1"
    if not args.visible:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PySide6.QtCore import QObject, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow
    from native_window_policy import (load_native_shell as load_shell, close_native_windows)
    from mediaforge.bootstrap.application import build_media_view_model
    from mediaforge.infrastructure.ffmpeg.video_duration import first_video_duration
    from mediaforge.infrastructure.resources.paths import qml_root

    app = QGuiApplication([])
    model = build_media_view_model(logging.getLogger("mediaforge"))
    service = model.resize._use_case; executor = service._executor; runtime = executor._runtime
    assert runtime is not None, "Provision the TASK-003 managed runtime first"
    assert model.trim._use_case._executor._runtime is runtime
    assert model.crop._use_case._executor._runtime is runtime
    assert service._processing_gate is model.trim._use_case._processing_gate is model.crop._use_case._processing_gate
    engine = QQmlApplicationEngine(); warnings = []
    engine.rootContext().setContextProperty("mediaViewModel", model)
    engine.warnings.connect(lambda items: warnings.extend(item.toString() for item in items))
    window = load_shell(engine, qml_root(), logging.getLogger("mediaforge"))
    evidence = {"runtime": {"ffmpeg": str(runtime.ffmpeg), "ffprobe": str(runtime.ffprobe)}, "scenarios": []}

    def wait(condition, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if condition(): return
            time.sleep(.005)
        raise AssertionError("Acceptance deadline exceeded")

    def run(command, timeout=120):
        return subprocess.run(command, check=True, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", shell=False, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def probe(path, frames=False):
        args = [str(runtime.ffprobe), "-v", "error", "-count_frames" if frames else "-show_streams",
                "-show_streams", "-show_format", "-of", "json", str(path)] if frames else [
                str(runtime.ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
        return json.loads(run(args).stdout)

    def frame_timestamps(path):
        payload = json.loads(run([str(runtime.ffprobe), "-v", "error", "-select_streams", "v:0",
                                  "-show_frames", "-show_entries", "frame=best_effort_timestamp_time",
                                  "-of", "json", str(path)]).stdout)
        frames = payload.get("frames")
        assert isinstance(frames, list) and frames
        assert all(isinstance(frame, dict) and "best_effort_timestamp_time" in frame for frame in frames)
        return [float(frame["best_effort_timestamp_time"]) for frame in frames]

    def digest(path):
        with path.open("rb") as stream: return hashlib.file_digest(stream, "sha256").hexdigest()

    def fixture(relative, audio=1, size="640x360", duration=2, long=False):
        source = root / relative; source.parent.mkdir(parents=True, exist_ok=True)
        command = [str(runtime.ffmpeg), "-nostdin", "-v", "error", "-n", "-f", "lavfi", "-i",
                   f"testsrc2=size={size}:rate=30"]
        for index in range(audio):
            command += ["-f", "lavfi", "-i", f"sine=frequency={440 + 220 * index}:sample_rate=48000"]
        command += ["-t", "30" if long else str(duration), "-map", "0:v:0"]
        for index in range(audio): command += ["-map", f"{index + 1}:a:0"]
        command += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)]
        run(command); return source

    def vfr_fixture(relative):
        source = root / relative; source.parent.mkdir(parents=True, exist_ok=True)
        run([str(runtime.ffmpeg), "-nostdin", "-v", "error", "-n",
             "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=1",
             "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15:duration=1",
             "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
             "-fps_mode", "vfr", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(source)])
        return source

    def open_source(source, target=(320, 180)):
        assert model.openMedia(QUrl.fromLocalFile(str(source)))
        wait(lambda: model.canOpen and model.state != "loading")
        assert model.state == "loaded", model.errorMessage
        model.resize.widthText, model.resize.heightText = map(str, target)

    def execute():
        assert model.resize.startResize(); wait(lambda: not model.resize.busy); model.resize.refresh()
        assert not service._processing_gate.busy

    def record(name, source, original, *, expected="Completed", audio=1, dimensions=(320, 180), frames=False):
        assert model.resize.state == expected, (name, service.snapshot())
        assert digest(source) == original and model.localPath == str(source)
        assert not list(source.parent.glob(".mediaforge-*"))
        entry = {"scenario": name, "state": expected, "source_unchanged": True,
                 "staging_clean": True, "gate_released": True}
        if expected == "Completed":
            output = Path(model.resize.outputPath); source_probe = probe(source, frames); result = probe(output, frames)
            source_video = next(s for s in source_probe["streams"] if s["codec_type"] == "video")
            video = next(s for s in result["streams"] if s["codec_type"] == "video")
            source_audio = [s for s in source_probe["streams"] if s["codec_type"] == "audio"]
            output_audio = [s for s in result["streams"] if s["codec_type"] == "audio"]
            assert video["codec_name"] == "h264" and (video["width"], video["height"]) == dimensions
            assert video["sample_aspect_ratio"] == "1:1" and len(source_audio) == len(output_audio) == audio
            source_duration = first_video_duration(source_video); output_duration = first_video_duration(video)
            assert abs(output_duration - source_duration) <= .250 and output.suffix == source.suffix
            entry.update(output=str(output), dimensions=list(dimensions), audio_streams=audio,
                         source_video_duration=source_duration, output_video_duration=output_duration,
                         source_duration_provenance="stream" if source_video.get("duration") else "same-video DURATION tag",
                         output_duration_provenance="stream" if video.get("duration") else "same-video DURATION tag")
            if frames:
                entry.update(source_frames=int(source_video["nb_read_frames"]), output_frames=int(video["nb_read_frames"]))
                assert entry["source_frames"] == entry["output_frames"]
                source_timestamps = frame_timestamps(source)
                output_timestamps = frame_timestamps(output)
                assert len(source_timestamps) == entry["source_frames"]
                assert len(output_timestamps) == entry["output_frames"]
                entry.update(
                    source_interval_classes=len(_interval_classes(source_timestamps)),
                    max_relative_timestamp_error=assert_vfr_cadence(source_timestamps, output_timestamps),
                    timestamp_tolerance=TIMESTAMP_TOLERANCE_SECONDS,
                )
        else:
            assert not model.resize.outputPath
            entry["error"] = service.snapshot().error
        evidence["scenarios"].append(entry); print(f"{name}: {expected}", flush=True)
        return entry

    try:
        assert not window.findChild(QObject, "resizeButton").property("enabled")
        normal_factory = executor._processes._factory
        cases = [
            ("mp4-downscale", "mp4/video.mp4", 1, "640x360", (320, 180)),
            ("mov-downscale", "mov/video.mov", 1, "640x360", (320, 180)),
            ("mkv-downscale", "mkv/video.mkv", 1, "640x360", (320, 180)),
            ("video-only", "silent/video.mp4", 0, "640x360", (320, 180)),
            ("upscale", "upscale/video.mp4", 1, "320x180", (640, 360)),
            ("aspect-change", "aspect/video.mp4", 1, "640x360", (320, 320)),
            ("spaces", "path with spaces/my video.mp4", 1, "640x360", (320, 180)),
            ("unicode", "Папка Кириллица/видео 🎬.mp4", 1, "640x360", (320, 180)),
            ("multi-audio", "multi/video.mkv", 2, "640x360", (320, 180)),
        ]
        for name, relative, audio, size, target in cases:
            source = fixture(relative, audio, size); original = digest(source)
            open_source(source, target); execute(); record(name, source, original, audio=audio, dimensions=target)
            if name == "mp4-downscale":
                scroll = window.findChild(QObject, "mediaDetailsScroll").property("contentItem")
                scroll.setProperty("contentY", max(0, scroll.property("contentHeight") - scroll.property("height")))
                for _ in range(10): app.processEvents(); time.sleep(.02)
                assert window.grabWindow().save(str(root / "resize-success.png"))

        source = fixture("collision/video.mp4"); original = digest(source)
        existing = source.with_name("video_resize.mp4"); existing.write_bytes(b"EXISTING")
        open_source(source); execute(); assert Path(model.resize.outputPath).name == "video_resize_001.mp4"
        assert existing.read_bytes() == b"EXISTING"; record("collision", source, original)

        source = fixture("failure/video.mp4"); original = digest(source); open_source(source)
        def corrupt(command, **kwargs):
            if command[0] == str(runtime.ffprobe) and Path(command[-1]) != source:
                Path(command[-1]).write_bytes(b"invalid staged output")
            return normal_factory(command, **kwargs)
        executor._processes._factory = corrupt; execute(); executor._processes._factory = normal_factory
        record("controlled-failure", source, original, expected="Failed")

        base = fixture("rotation/base.mp4"); source = base.with_name("rotated.mp4")
        run([str(runtime.ffmpeg), "-nostdin", "-v", "error", "-n", "-display_rotation:v:0", "90",
             "-i", str(base), "-c", "copy", str(source)])
        original = digest(source); open_source(source); execute()
        record("rotation-rejected", source, original, expected="Failed")
        assert service.snapshot().error == "unsupported_orientation"

        base = fixture("mismatch/base.mkv"); source = base.with_name("mismatch.mp4"); source.write_bytes(base.read_bytes())
        original = digest(source); open_source(source); execute()
        record("container-mismatch", source, original, expected="Failed")
        assert service.snapshot().error == "unsupported_container"

        source = fixture("cancel/long video.mp4", long=True); original = digest(source); open_source(source, (1280, 720))
        children, encoding = [], threading.Event()
        def observed(command, **kwargs):
            child = normal_factory(command, **kwargs); children.append(child)
            if "-vf" in command: encoding.set()
            return child
        executor._processes._factory = observed; assert model.resize.startResize(); wait(encoding.is_set)
        assert children[-1].poll() is None; model.resize.cancelResize(); wait(lambda: not model.resize.busy); model.resize.refresh()
        record("real-cancel", source, original, expected="Cancelled")
        executor._processes._factory = normal_factory; execute(); record("retry-after-cancel", source, original, dimensions=(1280, 720))

        source = vfr_fixture("vfr/video.mkv"); original = digest(source); open_source(source)
        execute(); vfr_entry = record("vfr-passthrough", source, original, audio=0, frames=True)

        # Test-only negative control: preserve old count/duration assertions while
        # deliberately converting cadence to CFR. Product code never uses this.
        cfr_output = root / "vfr-negative-control" / "cfr.mkv"
        cfr_output.parent.mkdir(parents=True, exist_ok=True)
        run([str(runtime.ffmpeg), "-nostdin", "-v", "error", "-n", "-i", str(source),
             "-vf", "scale=320:180:flags=lanczos,setsar=1/1,fps=22.5", "-frames:v", "45",
             "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-fps_mode", "cfr", str(cfr_output)])
        cfr_probe = probe(cfr_output, True)
        cfr_video = next(stream for stream in cfr_probe["streams"] if stream["codec_type"] == "video")
        assert int(cfr_video["nb_read_frames"]) == vfr_entry["source_frames"]
        assert abs(first_video_duration(cfr_video) - vfr_entry["source_video_duration"]) <= .250
        rejected = False
        try:
            assert_vfr_cadence(frame_timestamps(source), frame_timestamps(cfr_output))
        except AssertionError:
            rejected = True
        assert rejected, "CFR negative control must fail the VFR cadence assertion"
        evidence["scenarios"].append({"scenario": "vfr-negative-cfr-control",
                                      "state": "RejectedByCadence", "same_frame_count": True,
                                      "old_duration_assertion_passed": True})
        print("vfr-negative-cfr-control: RejectedByCadence", flush=True)

        assert warnings == [], warnings
        evidence["qml_warnings"] = warnings; evidence["visible"] = args.visible
        evidence["versions"] = [run([str(path), "-version"]).stdout.splitlines()[0] for path in (runtime.ffmpeg, runtime.ffprobe)]
        (root / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        executor._processes._factory = normal_factory
        model.shutdown(); close_native_windows(window); del engine
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
