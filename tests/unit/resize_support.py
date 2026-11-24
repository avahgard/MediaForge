"""Controlled four-phase children for Resize behavioral tests."""

import io
import json
from pathlib import Path
import subprocess
import threading
import time

from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.features.media.resize import ResizeDimensions, ResizeRequest
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegRuntime


def payload(width=640, height=360, audio=1, family="mov,mp4,m4a,3gp,3g2,mj2",
            duration="2.0", sar="1:1", tags=None):
    video = {"codec_type": "video", "codec_name": "h264", "width": width,
             "height": height, "sample_aspect_ratio": sar}
    if duration is not None:
        video["duration"] = duration
    if tags is not None:
        video["tags"] = tags
    return {"streams": [video] + [{"codec_type": "audio", "codec_name": "aac"} for _ in range(audio)],
            "format": {"format_name": family}}


def setup(tmp_path, name="видео файл.mp4", width=320, height=180):
    source = tmp_path / name
    source.write_bytes(b"SOURCE")
    runtime = FfmpegRuntime(tmp_path / "managed pair/ffmpeg.exe", tmp_path / "managed pair/ffprobe.exe")
    return ResizeRequest(source, ResizeDimensions(width, height), 640, 360), runtime


def metadata(source, width=640, height=360):
    return MediaMetadataDto(source.name, str(source), "mp4", 2.0, "h264", width, height, 25)


def finish(service):
    deadline = time.monotonic() + 3
    while service.busy and time.monotonic() < deadline:
        time.sleep(.001)
    assert not service.busy
    return service.snapshot()


class CountingGate(MediaProcessingGate):
    def __init__(self):
        super().__init__()
        self.admitted = 0
        self.released = 0

    def acquire(self):
        owner = super().acquire()
        if owner is not None:
            self.admitted += 1
        return owner

    def release(self, owner):
        super().release(owner)
        self.released += 1


class Child:
    def __init__(self, text="", *, code=0, block=False, force=False, staged=None):
        self.text = text
        self.returncode = None if block else code
        self.force = force
        self.staged = staged
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.entered = threading.Event()
        self.actions = []

    def communicate(self, timeout):
        self.entered.set()
        if self.returncode is None:
            time.sleep(min(timeout, .002))
            raise subprocess.TimeoutExpired("controlled-child", timeout)
        return self.text, "private diagnostic"

    def poll(self):
        return self.returncode

    def terminate(self):
        if self.staged is not None:
            assert self.staged.exists()
        self.actions.append("terminate")
        if not self.force:
            self.returncode = -15

    def wait(self, timeout):
        self.actions.append(("wait", timeout))
        if self.returncode is None:
            raise subprocess.TimeoutExpired("controlled-child", timeout)
        return self.returncode

    def kill(self):
        self.actions.append("kill")
        self.returncode = -9


class Factory:
    def __init__(self, *, source=None, output=None, inventory=" V....D libx264 H.264 encoder\n",
                 block=None, force=False, spawn_failure=None, exit_failure=None):
        self.source = payload() if source is None else source
        self.output = payload(320, 180) if output is None else output
        self.inventory = inventory
        self.block, self.force = block, force
        self.spawn_failure, self.exit_failure = spawn_failure, exit_failure
        self.calls, self.children = [], []
        self.entered = threading.Event()
        self.staged = None

    def __call__(self, command, **kwargs):
        phase = len(self.calls)
        self.calls.append((command, kwargs))
        if phase == self.spawn_failure:
            raise OSError("controlled spawn failure")
        if phase == 2:
            self.staged = Path(command[-1])
            self.staged.write_bytes(b"STAGED")
        text = (json.dumps(self.source) if phase == 0 else self.inventory if phase == 1
                else "" if phase == 2 else json.dumps(self.output))
        child = Child(text, code=1 if phase == self.exit_failure else 0,
                      block=phase == self.block, force=self.force, staged=self.staged)
        self.children.append(child)
        if phase == self.block:
            self.entered.set()
        return child
