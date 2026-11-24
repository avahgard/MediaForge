from pathlib import Path
import pytest
from mediaforge.infrastructure.filesystem.source_location import WindowsSourceLocationRevealer


@pytest.mark.parametrize('name',['public source.mp4','Кириллица со звуком.mov','literal & name.mp4'])
def test_reveal_exact_existing_source_with_separated_arguments(tmp_path,name):
    source=tmp_path/name;source.write_bytes(b'public');calls=[]
    adapter=WindowsSourceLocationRevealer(process_factory=lambda args,**kw:calls.append((args,kw)))
    assert adapter.reveal(str(source)).succeeded
    args,options=calls[0]
    assert Path(args[0]).name.lower()=='explorer.exe'
    assert args[1:]==['/select,',str(source.resolve())] and options=={'shell':False}


def test_missing_directory_relative_and_launch_failure_are_bounded(tmp_path):
    calls=[];adapter=WindowsSourceLocationRevealer(process_factory=lambda *a,**k:calls.append(a))
    for source in (tmp_path/'missing.mp4',tmp_path,'relative.mp4'):
        result=adapter.reveal(str(source));assert not result.succeeded and result.message
    assert not calls
    source=tmp_path/'source.mp4';source.write_bytes(b'public')
    def failed(*a,**k): raise OSError('unavailable')
    result=WindowsSourceLocationRevealer(process_factory=failed).reveal(str(source))
    assert not result.succeeded and result.message


def test_exported_sequence_directory_reveal_does_not_change_source_file_policy(tmp_path):
    folder=tmp_path/'кадры output';folder.mkdir();calls=[]
    adapter=WindowsSourceLocationRevealer(process_factory=lambda args,**kw:calls.append((args,kw)))
    assert not adapter.reveal(str(folder)).succeeded
    assert adapter.reveal_output(str(folder)).succeeded
    assert calls[0][0][1:]==[str(folder.resolve())] and calls[0][1]=={'shell':False}


def test_open_output_folder_for_existing_or_removed_file_and_missing_folder(tmp_path):
    calls=[];adapter=WindowsSourceLocationRevealer(process_factory=lambda args,**kw:calls.append((args,kw)))
    output=tmp_path/'result.mp4';output.write_bytes(b'result')
    assert adapter.reveal_output(str(output)).succeeded
    output.unlink()  # Only this test-owned fixture; parent remains navigable.
    assert adapter.reveal_output(str(output)).succeeded
    assert len(calls)==2 and all(args[1:]==[str(tmp_path.resolve())] and kw=={'shell':False} for args,kw in calls)
    for path in ['relative.mp4',str(tmp_path/'missing'/'result.mp4')]:
        result=adapter.reveal_output(path);assert not result.succeeded and result.message
    assert len(calls)==2
