"""Explicit real-runtime acceptance check; synthetic fixtures only, no app feature."""

import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import time

from PySide6.QtCore import QCoreApplication, QUrl

from mediaforge.bootstrap.application import build_media_view_model
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.ffmpeg.provisioning import verify_archive
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest, FfmpegRuntimeResolver


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("MEDIAFORGE_FFPROBE_PATH", "").strip():
        parser.error("Unset MEDIAFORGE_FFPROBE_PATH: acceptance requires managed runtime")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("mediaforge")
    manifest = FfmpegManifest.load()
    profile = ProfilePaths.from_environment()
    runtime = FfmpegRuntimeResolver(manifest, logger=logger).resolve(profile)
    digest = verify_archive(manifest.location(profile) / manifest.artifact, manifest)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    evidence = {"archive_sha256": digest, "banners": {}, "metadata": []}
    for executable in (runtime.ffmpeg, runtime.ffprobe):
        result = subprocess.run(
            [str(executable), "-version"], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=10,
            shell=False, creationflags=flags, check=True,
        )
        evidence["banners"][str(executable)] = result.stdout.splitlines()[0]
        print(result.stdout.splitlines()[0])
    app = QCoreApplication.instance() or QCoreApplication([])
    model = build_media_view_model(logger)
    try:
        for folder in ("normal", "path with spaces", "Видео с кириллицей"):
            directory = output / folder
            directory.mkdir(parents=True, exist_ok=True)
            for audio in (False, True):
                source = directory / ("with-audio.mkv" if audio else "silent.mkv")
                command = [str(runtime.ffmpeg), "-y", "-v", "error", "-f", "lavfi",
                           "-i", "color=c=blue:s=64x48:r=25:d=1"]
                if audio:
                    command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                                "-c:a", "pcm_s16le"]
                command += ["-c:v", "ffv1", "-t", "1", str(source)]
                subprocess.run(command, capture_output=True, timeout=30, check=True,
                               shell=False, creationflags=flags)
                assert model.openMedia(QUrl.fromLocalFile(str(source)))
                assert model.state == "loading"
                deadline = time.monotonic() + 20
                while not model.canOpen and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.005)
                assert model.state == "loaded", model.errorMessage
                assert Path(model.localPath) == source
                assert model.resolution == "64 × 48"
                assert model.frameRate == "25 fps"
                assert model.duration == "0:01.000"
                evidence["metadata"].append({
                    "source": str(source), "has_audio_fixture": audio,
                    "state": model.state, "resolution": model.resolution,
                    "frame_rate": model.frameRate, "duration": model.duration,
                })
                print(f"PASS: {source}")
    finally:
        model.shutdown()
    (output / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"SHA-256: {digest}; metadata matrix: {len(evidence['metadata'])} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
