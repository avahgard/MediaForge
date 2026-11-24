"""Native H.264 publication and Windows handle-retirement fault controls."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

from mediaforge.application.services.export import ExportService
from mediaforge.application.services.images import ImageSourceService
from mediaforge.application.services.media import OpenMediaService
from mediaforge.application.state.workspace import MediaWorkspaceSession
from mediaforge.infrastructure.ffmpeg.export import FfmpegExportExecutor
from mediaforge.infrastructure.ffmpeg.metadata_probe import FfprobeMediaMetadataProbe
from mediaforge.infrastructure.ffmpeg.preview_job import PreviewJobs
from mediaforge.infrastructure.ffmpeg.runtime import FfmpegManifest, FfmpegRuntimeResolver
from mediaforge.infrastructure.filesystem.export_output import ExportOutput
from mediaforge.infrastructure.filesystem.export_staging import ExportStaging
from mediaforge.infrastructure.filesystem.image_output import ReadLocks
from mediaforge.infrastructure.images.resources import WindowsPreviewResources
from tests.unit.resize_support import finish


pytestmark = [pytest.mark.native, pytest.mark.integration,
              pytest.mark.skipif(os.name != 'nt', reason='Windows Job handles and NTFS staging required')]


@pytest.fixture(scope='module')
def authenticated_runtime():
    configured = os.environ.get('MEDIAFORGE_TEST_RUNTIME_ROOT')
    if not configured:
        pytest.skip('Provide the managed FFmpeg root in MEDIAFORGE_TEST_RUNTIME_ROOT.')
    root = Path(configured)
    packaged_manifest = root.parent / 'manifests/ffmpeg.json'
    manifest = FfmpegManifest.load(packaged_manifest if packaged_manifest.is_file() else None)
    return FfmpegRuntimeResolver(manifest).verify_directory(root)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('scenario', ['job', 'source', 'before_publication', 'normal', 'readonly'])
def test_av_export_retirement_preserves_confirmed_publication(tmp_path, monkeypatch, caplog,
                                                              authenticated_runtime, scenario):
    runtime = authenticated_runtime
    source = tmp_path / 'synthetic.mp4'
    subprocess.run([str(runtime.ffmpeg), '-nostdin', '-v', 'error', '-n', '-f', 'lavfi',
                    '-i', 'testsrc2=size=160x120:rate=25:duration=0.8', '-c:v', 'libx264',
                    '-threads', '1', str(source)], capture_output=True, check=True, timeout=20)
    sentinel = tmp_path / 'prior.mp4'
    sentinel.write_bytes(b'prior destination')
    expected = {p: digest(p) for p in (source, sentinel)}
    metadata = OpenMediaService(FfprobeMediaMetadataProbe(runtime.ffprobe)).open_media(str(source)).metadata
    item = MediaWorkspaceSession().add(metadata)[0]
    item.output.folder = str(tmp_path)
    item.output.filename = 'published Cyrillic Кириллица'
    item.output.codec = 'h264'
    resources = WindowsPreviewResources()
    executor = FfmpegExportExecutor(runtime, image_service=ImageSourceService(None, resources=resources))
    service = ExportService(executor)
    request = service.prepare(item)
    target = request.destination
    fault_records = []
    restored = []
    staged_attributes = []
    original_job_close = PreviewJobs.close

    def refuse_close(owner, refused, kind):
        real_close = owner.api.CloseHandle
        restored.append((owner, real_close))
        def close(handle):
            if handle == refused:
                fault_records.append({'owner': kind, 'winerror': 5})
                ctypes.set_last_error(5)
                return 0
            return real_close(handle)
        owner.api.CloseHandle = close

    if scenario in ('job', 'before_publication'):
        def close_jobs(owner):
            assert [process.poll() for process in owner.processes] == [0, 0, 0, 0]
            refuse_close(owner, owner.jobs[0], 'job')
            return original_job_close(owner)
        monkeypatch.setattr(PreviewJobs, 'close', close_jobs)

    if scenario == 'source':
        original_add = ReadLocks.add
        def add_source(owner, path, directory=False, **kwargs):
            handle = original_add(owner, path, directory, **kwargs)
            if path == source and not directory:
                refuse_close(owner, handle, 'source')
            return handle
        monkeypatch.setattr(ReadLocks, 'add', add_source)

    if scenario == 'before_publication':
        original_publish = ExportOutput.publish
        def collide(output, control):
            target.write_bytes(b'independent output won the race')
            return original_publish(output, control)
        monkeypatch.setattr(ExportOutput, 'publish', collide)

    if scenario == 'readonly':
        original_staging_close = ExportStaging.close
        def close_readonly(stage):
            assert stage.published
            os.chmod(stage.directory, stat.S_IREAD)
            staged_attributes.append(stage.directory.lstat().st_file_attributes)
            return original_staging_close(stage)
        monkeypatch.setattr(ExportStaging, 'close', close_readonly)

    try:
        assert service.start_request(request)
        job = finish(service)
        assert not service.busy
        committed = service._control.published
        gate = service._processing_gate
        assert committed == (scenario != 'before_publication')
        assert not list(tmp_path.glob('.mediaforge-export-*'))
        assert all(digest(p) == previous for p, previous in expected.items())
        if committed:
            subprocess.run([str(runtime.ffmpeg), '-nostdin', '-v', 'error', '-xerror',
                            '-i', str(target), '-f', 'null', '-'], capture_output=True, check=True, timeout=20)
        else:
            assert target.read_bytes() == b'independent output won the race'
        proof = dict(scenario=scenario, state=job.state, code=job.error, message=job.message, published=committed,
                     destination=str(target), result=job.result, has_completed_result=service.completed_result() is not None,
                     target_sha256=digest(target), staging_removed=True, staged_attributes=staged_attributes,
                     fault_records=fault_records, preserved_inputs=True, gate_failure=gate.failure)
        (tmp_path / 'retirement-proof.json').write_text(json.dumps(proof, indent=2, ensure_ascii=False), encoding='utf-8')

        retained = scenario in ('job', 'source', 'before_publication')
        if retained:
            assert len(fault_records) == 1 and fault_records[0]['winerror'] == 5
            assert job.state == 'Failed' and not job.result and service.completed_result() is None
            assert gate.failure == 'export_resource_cleanup_failed'
            assert not service.start_request(request)
            with pytest.raises(RuntimeError, match='cleanup'):
                service.shutdown()
        else:
            assert job.state == 'Completed' and Path(job.result) == target
            assert not gate.failure and not gate.busy
            assert service.completed_result() is not None
            if scenario == 'readonly':
                assert len(staged_attributes) == 1 and staged_attributes[0] & stat.FILE_ATTRIBUTE_READONLY
            service.shutdown()
        if scenario in ('job', 'source'):
            assert job.error == 'lifecycle_failed', job
            assert f'Output was published to {target} before cleanup failed.' in job.message
            assert 'blocked' in job.message
            assert ('Preview Job handle cleanup failed' if scenario == 'job'
                    else 'Native source/path handles could not be released') in job.message
            if scenario == 'job':
                assert '[WinError 5]' in caplog.text
        elif scenario == 'before_publication':
            assert 'Output was published' not in job.message
            assert 'Destination appeared during Export' in caplog.text
    finally:
        for owner, real_close in restored:
            owner.api.CloseHandle = real_close
        for owner in service._control.retained_resources:
            if isinstance(owner, PreviewJobs):
                original_job_close(owner)
            elif isinstance(owner, ReadLocks):
                owner.close()
        resources.close()
