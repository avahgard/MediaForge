from contextlib import closing
import subprocess
import sys

import pytest

from mediaforge.features.dub.contracts import DubControl, DubError, Reason
from mediaforge.infrastructure.ffmpeg.dub_process import ProbeProcess
from mediaforge.infrastructure.filesystem.profile import ProfilePaths


pytestmark=pytest.mark.integration


def runner(tmp_path,timeout=5):
    paths=ProfilePaths.for_test(tmp_path/'portable');paths.prepare()
    children=[]
    def factory(command,**kwargs):
        assert kwargs['cwd']==paths.temporary
        assert kwargs['env']['TEMP']==str(paths.temporary)==kwargs['env']['TMP']
        process=subprocess.Popen(command,**kwargs);children.append(process);return process
    control=DubControl()
    return ProbeProcess(factory,paths,control,timeout),control,children


def test_lossless_backpressure_never_drops_clock_records(tmp_path):
    process,control,children=runner(tmp_path)
    records=list(process.lines([sys.executable,'-c','for i in range(500): print(i)']))
    assert [int(r) for r in records]==list(range(500))
    assert all(p.poll()==0 and p.stdout.closed and p.stderr.closed for p in children)


@pytest.mark.parametrize('code,reason',[
    ('print("x"*1000)',Reason.PROBE_FAILED),
    ('import sys; sys.stderr.write("corrupt packet"); print("frame")',Reason.PROBE_FAILED),
    ('raise SystemExit(3)',Reason.PROBE_FAILED),
])
def test_bad_or_oversize_output_fails_and_reaps(tmp_path,code,reason):
    process,control,children=runner(tmp_path)
    with pytest.raises(DubError) as error:list(process.lines([sys.executable,'-c',code],line_limit=128))
    assert error.value.code==reason
    assert all(p.poll() is not None and p.stdout.closed and p.stderr.closed for p in children)
    assert not control.cleanup_failed


def test_hung_child_times_out_and_is_reaped(tmp_path):
    process,control,children=runner(tmp_path,timeout=.05)
    with pytest.raises(DubError) as error:list(process.lines([sys.executable,'-c','import time; time.sleep(30)']))
    assert error.value.code==Reason.TIMEOUT
    assert all(p.poll() is not None for p in children) and not control.cleanup_failed


def test_cancel_and_parser_failure_close_generator_and_terminate_child(tmp_path):
    process,control,children=runner(tmp_path)
    with closing(process.lines([sys.executable,'-u','-c','import time; print("frame"); time.sleep(30)'])) as records:
        assert next(records).strip()==b'frame'
        control.cancel()
        with pytest.raises(DubError) as error:next(records)
        assert error.value.code==Reason.CANCELLED
    assert all(p.poll() is not None for p in children)
