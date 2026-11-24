"""Deterministic real managed-child cancellation/shutdown for Resize."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import threading
import time

from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.application.services.resize import ResizeService
from mediaforge.infrastructure.ffmpeg.resize import FfmpegResizeExecutor
from mediaforge.infrastructure.ffmpeg.runtime import resolve_ffmpeg_runtime


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); assert os.name == "nt"
    os.environ.pop("MEDIAFORGE_FFPROBE_PATH", None)
    runtime = resolve_ffmpeg_runtime(); root = args.output.resolve(); root.mkdir(parents=True, exist_ok=True)
    fixture = root / "fixture.mp4"
    subprocess.run([str(runtime.ffmpeg), "-nostdin", "-v", "error", "-n", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=25", "-t", "1", "-c:v", "libx264", "-preset", "ultrafast", str(fixture)],
                   check=True, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    original = fixture.read_bytes(); evidence = []
    for action in ("cancel", "shutdown"):
        for phase, name in enumerate(("source", "capability", "encode", "output")):
            directory = root / f"{action}-{name}"; directory.mkdir(); source = directory / "видео файл.mp4"
            source.write_bytes(original); calls, children, entered = [], [], threading.Event()
            def factory(command, **kwargs):
                target = len(calls) == phase; calls.append(command)
                if target: kwargs["creationflags"] |= 0x00000004
                child = subprocess.Popen(command, **kwargs); children.append(child)
                if target: entered.set()
                return child
            gate = MediaProcessingGate(); service = ResizeService(
                FfmpegResizeExecutor(runtime, process_factory=factory), gate=gate)
            service.set_source(MediaMetadataDto(source.name, str(source), "mp4", 1, "h264", 640, 360, 25))
            try:
                assert service.start(320, 180) and entered.wait(15)
                assert gate.busy and children[-1].poll() is None; started = time.monotonic()
                if action == "shutdown": service.shutdown()
                else:
                    assert service.cancel(); deadline = time.monotonic() + 6
                    while service.busy and time.monotonic() < deadline: time.sleep(.005)
                elapsed = time.monotonic() - started
                assert not service.busy and service.snapshot().state == "Cancelled"
                assert len(calls) == phase + 1 and all(child.poll() is not None for child in children)
                assert list(directory.iterdir()) == [source] and source.read_bytes() == original
                assert not gate.busy and elapsed < 6
                evidence.append({"action": action, "phase": name, "state": "Cancelled", "seconds": elapsed,
                                 "child_count": len(children), "all_reaped": True, "no_later_spawn": True,
                                 "no_publication": True, "staging_clean": True, "gate_released": True})
                print(f"{action} during {name}: Cancelled; reaped; gate released", flush=True)
            finally:
                service.shutdown()
                for child in children:
                    if child.poll() is None: child.kill(); child.wait(timeout=3)
    (root / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__": raise SystemExit(main())
