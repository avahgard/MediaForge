"""Exact-object publication and real seekable FFmpeg fd output, no AI mocks."""
from contextlib import closing
import hashlib
import io
import os
from pathlib import Path
import subprocess
import wave

import pytest

from mediaforge.features.dub.contracts import DubControl
from mediaforge.infrastructure.filesystem.dub_output import DubOutput
from mediaforge.infrastructure.filesystem.export_identity import file_identity
from mediaforge.infrastructure.ffmpeg.preview_job import PreviewJobs
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest,FfmpegRuntimeResolver

pytestmark=[pytest.mark.native,pytest.mark.integration]


def test_stdio_capability_can_be_duplicated_inside_cancel_admission_without_deadlock(run_python,tmp_path):
    target=tmp_path/'admitted.bin'
    result=run_python(f'''
import os
from pathlib import Path
from mediaforge.features.dub.contracts import DubControl
from mediaforge.infrastructure.filesystem.dub_output import DubOutput
control=DubControl()
with DubOutput(Path({str(target)!r}),control) as output:
    with control.starting():
        with output.descriptor() as descriptor:
            os.write(descriptor,b'owned admitted bytes')
    output.validated();output.publish()
assert Path({str(target)!r}).read_bytes()==b'owned admitted bytes'
''')
    assert result.returncode==0,result.stderr


def test_owned_output_cannot_be_opened_or_replaced_before_exact_publish(tmp_path):
    target=tmp_path/'output.bin';control=DubControl()
    with DubOutput(target,control) as output:
        with output.descriptor() as descriptor:
            os.write(descriptor,b'owned test bytes')
        assert not target.exists()
        with pytest.raises(PermissionError):
            next(output.directory.iterdir()).write_bytes(b'replaced')
        sealed=output.validated()
        output.publish()
        assert target.exists()
        with pytest.raises(PermissionError):
            target.read_bytes()
    assert target.read_bytes()==b'owned test bytes'
    assert file_identity(target,target.name,DubControl())==sealed.files[0]
    assert not any(p.name.startswith('.mediaforge-dub-') for p in tmp_path.iterdir())


def test_unpublished_output_disappears_and_collision_never_replaces(tmp_path):
    target=tmp_path/'source.bin';control=DubControl()
    with DubOutput(target,control) as output:
        with output.descriptor() as descriptor:os.write(descriptor,b'unpublished bytes')
    assert not target.exists() and list(tmp_path.iterdir())==[]
    with DubOutput(target,DubControl()) as output:
        with output.descriptor() as descriptor:os.write(descriptor,b'new bytes')
        output.validated()
        target.write_bytes(b'other owner')
        with pytest.raises(Exception) as failure:output.publish()
        assert getattr(failure.value,'code',None)=='destination_exists'
    assert target.read_bytes()==b'other owner'


def test_real_ffmpeg_fd_output_is_seekable_for_normal_container_header(tmp_path):
    root=os.environ.get('MEDIAFORGE_TEST_RUNTIME_ROOT')
    if not root:pytest.skip('Explicit authenticated managed FFmpeg root required.')
    runtime=FfmpegRuntimeResolver(FfmpegManifest.load()).verify_directory(Path(root))
    target=tmp_path/'native-seekable.wav';control=DubControl();jobs=PreviewJobs(512*1024**2)
    with DubOutput(target,control) as output:
        process=None
        try:
            with output.descriptor() as descriptor,control.starting():
                process=jobs([str(runtime.ffmpeg),'-nostdin','-hide_banner','-v','error','-f','lavfi',
                              '-i','sine=frequency=440:sample_rate=48000:duration=0.1',
                              '-c:a','pcm_s16le','-f','wav','-fd','1','fd:'],
                             stdin=subprocess.DEVNULL,stdout=descriptor,stderr=subprocess.PIPE,
                             shell=False,cwd=tmp_path,env=dict(TEMP=str(tmp_path),TMP=str(tmp_path)))
            diagnostic=process.communicate(timeout=10)[1]
            assert process.returncode==0,diagnostic
            output.validated();output.publish()
        finally:
            if process is not None:
                if process.poll() is None:process.kill();process.wait(timeout=2)
                process.stderr.close()
            jobs.close()
            if process is not None:process._handle.Close()
    raw=target.read_bytes()
    assert int.from_bytes(raw[4:8],'little')==len(raw)-8  # NOT nonseekable FFFFFFFF
    with wave.open(io.BytesIO(raw),'rb') as wav:
        assert (wav.getframerate(),wav.getnframes(),wav.getnchannels())==(48000,4800,1)
