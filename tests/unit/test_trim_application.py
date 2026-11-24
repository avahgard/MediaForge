import math
import threading
import time

import pytest

from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.services.trim import TrimService, normalize_range
from mediaforge.features.media.trim import TrimError, TrimErrorCode, TrimResult


def metadata(path, duration=10.0):
    return MediaMetadataDto(path.name, str(path), "mp4", duration, "h264", 64, 48, 25)


def finish(service):
    deadline = time.monotonic() + 2
    while service.busy and time.monotonic() < deadline:
        time.sleep(.002)
    assert not service.busy
    return service.snapshot()


@pytest.mark.parametrize("start,end,duration,expected", [
    (0, 10, 10, 10), (1, 3, 10, 3), (0, 9.999, 10, 9.999),
    (0, 10.001, 10, 10), (0, 10.05, 10, 10),
    (0, 0.15, .1, .1), (0, 62.30, 62.25, 62.25),
])
def test_exact_duration_acceptance(start, end, duration, expected):
    result = normalize_range(start, end, duration)
    assert result.start == start and result.end == expected


@pytest.mark.parametrize("start,end,duration", [
    (-1, 2, 10), (1, 1, 10), (3, 2, 10), (10, 10.05, 10),
    (0, 10.0500001, 10), (0, math.nextafter(10.05, math.inf), 10),
    (0, .15000001, .1), (0, 0, 0),
    (math.nan, 2, 10), (0, math.inf, 10), (0, 1, math.nan),
    (0, -math.inf, 10), ("0", 1, 10), (True, 2, 10),
])
def test_invalid_range(start, end, duration):
    with pytest.raises(TrimError) as error:
        normalize_range(start, end, duration)
    assert error.value.code == TrimErrorCode.INVALID_RANGE


class Executor:
    def __init__(self, error=None):
        self.requests = []
        self.error = error

    def execute(self, request, control):
        self.requests.append(request)
        if self.error:
            raise self.error
        return TrimResult(request.source.with_name("result.mp4"))


def test_current_source_preconditions_and_success(tmp_path):
    source = tmp_path / "source.mp4"
    executor = Executor()
    service = TrimService(executor)
    assert not service.start(0, 1)
    assert service.snapshot().error == "no_source"
    service.set_source(metadata(source))
    assert not service.start(0, 1)
    assert service.snapshot().error == "source_unavailable"
    source.write_bytes(b"source")
    service.set_source(metadata(source, None))
    assert not service.start(0, 1)
    service.set_source(metadata(source))
    assert not service.start(3, 2)
    assert executor.requests == []
    assert service.start(1, 10.05)
    job = finish(service)
    assert job.state == "Completed" and job.result == str(tmp_path / "result.mp4")
    assert executor.requests[0].interval.end == 10


@pytest.mark.parametrize("code", list(TrimErrorCode))
def test_stable_error_mapping(tmp_path, code):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    service = TrimService(Executor(TrimError(code, "RAW SECRET STDERR")))
    service.set_source(metadata(source))
    assert service.start(0, 1)
    job = finish(service)
    assert job.error == code.value
    assert job.state == ("Cancelled" if code == TrimErrorCode.CANCELLED else "Failed")
    assert "RAW" not in job.message


def test_single_job_cancel_shutdown_and_source_generation(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    entered = threading.Event()

    class Blocking:
        def execute(self, request, control):
            entered.set()
            while True:
                control.check()
                time.sleep(.002)

    service = TrimService(Blocking())
    service.set_source(metadata(source))
    assert service.start(0, 1) and entered.wait(1)
    assert service.snapshot().state == "Running"
    assert not service.start(0, 2)
    assert not service.set_source(metadata(tmp_path / "different.mp4"))
    assert service.cancel()
    assert not service.cancel()
    assert finish(service).state == "Cancelled"
    assert service.start(0, 1)
    service.shutdown()
    assert not service.busy and service.snapshot().state == "Cancelled"
    assert not service.start(0, 1)
    assert not service.set_source(None)


@pytest.mark.integration
def test_qt_free_boundary(run_python):
    result = run_python("import sys; import mediaforge.application.services.trim; assert 'PySide6' not in sys.modules")
    assert result.returncode == 0, result.stderr
