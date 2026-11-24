"""Real Windows publication, identity and failure/cancel preservation."""
from dataclasses import replace
import os
import pytest

from mediaforge.application.services.export import ExportService, capture
from mediaforge.features.media.export import ExportControl, ExportError
from mediaforge.infrastructure.filesystem.confirmed_output import ConfirmedOutput
from mediaforge.infrastructure.filesystem.export_output import ExportOutput
from tests.unit.test_export_lifecycle import Factory, executor, finish
from tests.unit.test_export_plan import item_at


def prepared(tmp_path):
    _,item=item_at(tmp_path)
    destination=capture(item).destination
    destination.write_bytes(b'previous destination')
    service=ExportService(None)
    return item,destination,service.prepare(item,allow_replacement=True)


@pytest.mark.native
def test_confirmed_atomic_replacement_and_writer_guard(tmp_path):
    item,target,request=prepared(tmp_path)
    original=target.read_bytes()
    with ExportOutput(request.source,target,replacement=request.replacement) as output:
        with output.staging.descriptor() as partial:
            partial.write(b'validated new')
        with pytest.raises(OSError): target.write_bytes(b'unsafe writer')
        with pytest.raises(OSError): target.unlink()
        assert target.read_bytes()==original
        assert output.publish(ExportControl())==target
    assert target.read_bytes()==b'validated new'
    assert request.source.read_bytes()==b'original'
    assert not list(tmp_path.glob('.mediaforge-export-*'))


def test_without_confirmation_or_source_alias_rejected(tmp_path):
    item,target,request=prepared(tmp_path)
    with pytest.raises(ExportError,match='different filename'): capture(item)
    with pytest.raises(ExportError,match='different filename'): ExportService(None).prepare(item)
    target.unlink();os.link(request.source,target)
    with pytest.raises(ExportError,match='sources are never'): ExportService(None).prepare(item)


def test_other_loaded_source_hardlink_protected(tmp_path):
    item,target,request=prepared(tmp_path)
    other=tmp_path/'other source.mp4';other.write_bytes(b'other original')
    target.unlink();os.link(other,target)
    with pytest.raises(ExportError,match='sources are never'):
        ExportService(None).prepare(item,[other])
    assert other.read_bytes()==b'other original'


@pytest.mark.native
def test_changed_destination_identity_rejected(tmp_path):
    item,target,request=prepared(tmp_path)
    target.rename(tmp_path/'prior.mp4');target.write_bytes(b'replacement by other writer')
    with pytest.raises(ExportError,match='changed'):
        with ExportOutput(request.source,target,replacement=request.replacement): pass
    assert target.read_bytes()==b'replacement by other writer'
    assert (tmp_path/'prior.mp4').read_bytes()==b'previous destination'
    assert not list(tmp_path.glob('.mediaforge-export-*'))


@pytest.mark.native
def test_transaction_closes_race_at_move_and_commit_failure_rolls_back(tmp_path):
    item,target,request=prepared(tmp_path)
    outsider=tmp_path/'outside.mp4';outsider.write_bytes(b'outsider')
    with pytest.raises(ExportError,match='publish'):
        with ExportOutput(request.source,target,replacement=request.replacement) as output:
            with output.staging.descriptor() as partial:
                partial.write(b'validated new')
            guard=output._guard
            move=guard.api.MoveFileTransactedW
            def racing_move(*args):
                # This runs after the transacted target handle closes. Isolation
                # still blocks both mutation and name replacement before Move.
                with pytest.raises(OSError): target.write_bytes(b'changed during publication')
                with pytest.raises(OSError): os.replace(outsider,target)
                return move(*args)
            guard.api.MoveFileTransactedW=racing_move
            guard.ktm.CommitTransaction=lambda _: 0
            output.publish(ExportControl())
    assert target.read_bytes()==b'previous destination'
    assert outsider.read_bytes()==b'outsider'
    assert not list(tmp_path.glob('.mediaforge-export-*'))


@pytest.mark.native
def test_read_only_destination_fails_without_damage(tmp_path):
    import stat
    item,target,request=prepared(tmp_path)
    target.chmod(stat.S_IREAD)
    try:
        with pytest.raises(ExportError,match='lock'):
            with ExportOutput(request.source,target,replacement=request.replacement): pass
        assert target.read_bytes()==b'previous destination'
        assert not list(tmp_path.glob('.mediaforge-export-*'))
    finally:
        target.chmod(stat.S_IREAD|stat.S_IWRITE)


@pytest.mark.native
@pytest.mark.parametrize('failure',[2,'validation','publication','cancel'])
def test_failure_cancel_keeps_existing_destination(tmp_path,monkeypatch,failure):
    item,target,request=prepared(tmp_path)
    item.output.codec='h264'
    request=ExportService(None).prepare(item,allow_replacement=True)
    factory=Factory(request.source,invalid=failure=='validation',failure=2 if failure==2 else None,
                    block=2 if failure=='cancel' else None)
    if failure=='publication':
        def denied(*_): raise PermissionError('controlled publication failure')
        monkeypatch.setattr(ConfirmedOutput,'publish',denied)
    service=ExportService(executor(tmp_path,factory))
    assert service.start_request(request)
    if failure=='cancel':
        assert factory.entered.wait(2)
        assert service.cancel()
    job=finish(service)
    assert job.state==('Cancelled' if failure=='cancel' else 'Failed'),job
    assert target.read_bytes()==b'previous destination'
    assert request.source.read_bytes()==b'original'
    assert not list(tmp_path.glob('.mediaforge-export-*'))
    service.shutdown()
