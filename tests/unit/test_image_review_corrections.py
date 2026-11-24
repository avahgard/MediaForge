"""Adversarial regression gates for the independent TASK-011 findings."""
from fractions import Fraction
from pathlib import Path
import os,threading
import pytest
from tests.unit.test_image_export import make_item,make_sequence,executor
from mediaforge.application.services.image_export import capture
from mediaforge.application.contracts.sequence import sequence_rate
from mediaforge.features.media.export import ExportControl,ExportError
from mediaforge.infrastructure.filesystem import image_capacity,image_output
from mediaforge.infrastructure.filesystem.image_publication import DirectoryPublication
from mediaforge.application.services.images import ImageSourceService
from tests.unit.test_image_preview import Resources,write_image
from mediaforge.infrastructure.images.reader import QtImageReader


@pytest.mark.parametrize('text',['1','120','1000000/999999','1000001/1000000','60000/1001','1000000/1000000'])
def test_full_fps_component_range(text):assert sequence_rate(text)==Fraction(text)


@pytest.mark.parametrize('text',['0','121','120000001/1000000','1000000/1000001','1/0','١/1','1e0','9999999/9999999'])
def test_invalid_fps_components(text):
    with pytest.raises(ValueError):sequence_rate(text)


def test_auto_pressure_selects_qualified_smaller_tier_before_decode(tmp_path):
    path=tmp_path/'source.jpg';write_image(path,(1920,1080));resources=Resources()
    class Reader(QtImageReader):
        plans=[]
        def decode_preview(self,source,plan,cancel):
            self.plans.append(plan.divisor);return super().decode_preview(source,plan,cancel)
    reader=Reader();service=ImageSourceService(reader,resources=resources)
    resources.gpu=service.render_reservation-2*(1920//4)*(1080//4)*4
    value=service.prepare_preview(str(path),'Auto',threading.Event(),viewport=(800,560))
    assert value.raster.plan.divisor==4 and reader.plans==[4];value.release()
    with pytest.raises(ValueError):service.prepare_preview(str(path),'1:2',threading.Event())
    assert reader.plans==[4]
    resources.gpu=service.render_reservation
    with pytest.raises(ValueError):service.prepare_preview(str(path),'Auto',threading.Event())
    assert reader.plans==[4] and service.budget.reserved==service.budget.overhead+service.cache_bytes


@pytest.mark.native
@pytest.mark.integration
def test_directory_transaction_protects_members_and_insertion_at_commit(tmp_path,monkeypatch):
    item=make_sequence(tmp_path);item.output.kind='sequence';item.output.container='png';request=capture(item)
    before={p:Path(p).read_bytes() for p in [m.path for m in request.source_identity.manifest.members]}
    original=DirectoryPublication.commit;denied=[]
    def competing(tx):
        stage=next(Path(item.output.folder).glob('.mediaforge-export-*'))/'frames'
        last=stage/request.numbering.name(request.count-1)
        for action in [lambda:last.write_bytes(b'corrupt'),lambda:last.rename(last.with_suffix('.replaced')),
                       lambda:(tx.target/'extra').write_bytes(b'extra'),lambda:tx.target.mkdir()]:
            with pytest.raises(OSError):action()
            denied.append(True)
        assert not tx.target.exists()
        return original(tx)
    monkeypatch.setattr(DirectoryPublication,'commit',competing)
    result=executor().execute(request,ExportControl())
    assert denied==[True]*4
    assert sorted(p.name for p in result.output.iterdir())==[request.numbering.name(i) for i in range(request.count)]
    assert all(Path(p).read_bytes()==b for p,b in before.items())


@pytest.mark.native
@pytest.mark.integration
def test_single_file_stays_locked_across_publication(tmp_path,monkeypatch):
    item=make_item(tmp_path);request=capture(item);original=image_output.link_file;denied=[]
    def competing(api,stage,target):
        for action in [lambda:stage.write_bytes(b'bad'),lambda:stage.rename(stage.with_suffix('.replaced'))]:
            with pytest.raises(PermissionError):action()
            denied.append(True)
        return original(api,stage,target)
    monkeypatch.setattr(image_output,'link_file',competing)
    assert executor().execute(request,ExportControl()).output.exists() and len(denied)==2


@pytest.mark.native
@pytest.mark.integration
def test_cancel_after_isolated_links_before_commit_rolls_back(tmp_path,monkeypatch):
    item=make_sequence(tmp_path);item.output.kind='sequence';item.output.container='png';r=capture(item)
    control=ExportControl();add=DirectoryPublication.add
    def cancel(tx,member):add(tx,member);control.cancel()
    monkeypatch.setattr(DirectoryPublication,'add',cancel)
    with pytest.raises(ExportError,match='cancel'):executor().execute(r,control)
    assert not r.destination.exists() and not list(r.destination.parent.glob('.mediaforge-*'))


@pytest.mark.parametrize('available',[0,None])
def test_insufficient_or_unknown_capacity_never_spawns(tmp_path,monkeypatch,available):
    item=make_item(tmp_path);r=capture(item);runner=executor()
    def space(folder):
        if available is None:raise ExportError('capacity_unknown','Unknown test capacity')
        return available
    monkeypatch.setattr(image_capacity,'free_bytes',space)
    from mediaforge.infrastructure.ffmpeg.preview_job import PreviewJobs
    monkeypatch.setattr(PreviewJobs,'__call__',lambda *a,**k:pytest.fail('Child spawned without capacity'))
    with pytest.raises(ExportError):runner.execute(r,ExportControl())
    assert not r.destination.exists() and not list(r.destination.parent.glob('.mediaforge-*'))


@pytest.mark.native
@pytest.mark.integration
def test_disappearing_capacity_rolls_back_without_publication(tmp_path,monkeypatch):
    item=make_sequence(tmp_path);item.output.kind='sequence';item.output.container='png';r=capture(item)
    free=[10**12];monkeypatch.setattr(image_capacity,'free_bytes',lambda folder:free[0])
    def progress(stage,message):
        if stage=='validation':free[0]=0
    with pytest.raises(ExportError,match='additional bytes'):executor().execute(r,ExportControl(progress))
    assert not r.destination.exists() and not list(r.destination.parent.glob('.mediaforge-*'))


@pytest.mark.native
@pytest.mark.parametrize('ancestor',[False,True])
def test_lexical_junction_refused_before_staging(tmp_path,ancestor):
    import _winapi
    item=make_item(tmp_path);real=tmp_path/'real Unicode 雪';real.mkdir();(real/'child').mkdir()
    alias=tmp_path/'alias';_winapi.CreateJunction(str(real),str(alias))
    item.output.folder=str(alias/'child' if ancestor else alias)
    with pytest.raises(ExportError,match='Reparse'):capture(item)
    assert not list(real.glob('.mediaforge-*'))


@pytest.mark.native
def test_directory_symlink_refused_when_privilege_available(tmp_path):
    item=make_item(tmp_path);real=tmp_path/'real';real.mkdir();alias=tmp_path/'link'
    try:os.symlink(real,alias,target_is_directory=True)
    except OSError as error:
        if getattr(error,'winerror',None)==1314:pytest.skip('Windows symlink privilege unavailable')
        raise
    item.output.folder=str(alias)
    with pytest.raises(ExportError,match='Reparse'):capture(item)


@pytest.mark.native
@pytest.mark.integration
def test_unicode_spaced_destination_with_case_alias(tmp_path):
    item=make_item(tmp_path);folder=tmp_path/'Unicode 雪 spaced';folder.mkdir();item.output.folder=str(folder).upper()
    r=capture(item);assert executor().execute(r,ExportControl()).output.exists()


@pytest.mark.native
@pytest.mark.integration
def test_stage_insertion_after_validation_never_becomes_success(tmp_path,monkeypatch):
    item=make_sequence(tmp_path);item.output.kind='sequence';item.output.container='png';r=capture(item)
    commit=DirectoryPublication.commit
    def insert(tx):
        stage=next(r.destination.parent.glob('.mediaforge-export-*'))/'frames'
        (stage/'unvalidated-extra.txt').write_bytes(b'competing insertion')
        return commit(tx)
    monkeypatch.setattr(DirectoryPublication,'commit',insert)
    with pytest.raises(ExportError,match='Unexpected sequence staging membership'):
        executor().execute(r,ExportControl())
    assert sorted(p.name for p in r.destination.iterdir())==[r.numbering.name(i) for i in range(r.count)]


@pytest.mark.native
@pytest.mark.integration
def test_unavailable_transaction_never_spawns_encoder(tmp_path,monkeypatch):
    item=make_sequence(tmp_path);item.output.kind='sequence';item.output.container='png';r=capture(item);runner=executor()
    def unavailable(self):raise ExportError('publication_unavailable','Controlled unavailable NTFS transaction')
    monkeypatch.setattr(DirectoryPublication,'begin',unavailable)
    from mediaforge.infrastructure.ffmpeg.preview_job import PreviewJobs
    monkeypatch.setattr(PreviewJobs,'__call__',lambda *a,**k:pytest.fail('Encoder started without protected publication'))
    with pytest.raises(ExportError,match='unavailable'):runner.execute(r,ExportControl())
    assert not r.destination.exists() and not list(r.destination.parent.glob('.mediaforge-*'))


@pytest.mark.native
@pytest.mark.integration
@pytest.mark.parametrize('committed',[False,True])
def test_transaction_cleanup_uncertainty_poisoned_gate(tmp_path,monkeypatch,committed):
    import time
    from mediaforge.application.services.export import ExportService
    from mediaforge.application.services.processing_gate import MediaProcessingGate
    item=make_sequence(tmp_path);item.output.kind='sequence';item.output.container='png';r=capture(item)
    original_begin=DirectoryPublication.begin;original_close=DirectoryPublication.close;held={}
    def begin(tx):
        original_begin(tx)
        if not committed:raise ExportError('controlled_failure','Controlled pre-commit failure')
    def close(tx):held['tx']=tx;return False
    monkeypatch.setattr(DirectoryPublication,'begin',begin);monkeypatch.setattr(DirectoryPublication,'close',close)
    gate=MediaProcessingGate();service=ExportService(executor(),gate=gate);assert service.start_request(r)
    end=time.monotonic()+15
    while service.busy and time.monotonic()<end:time.sleep(.01)
    assert not service.busy and service.snapshot().state=='Failed' and gate.failure
    assert r.destination.exists()==committed and held['tx'].committed==committed
    assert ('Outcome: committed' if committed else 'Outcome: active') in service.snapshot().message
    with pytest.raises(RuntimeError):service.shutdown()
    monkeypatch.setattr(DirectoryPublication,'begin',original_begin);monkeypatch.setattr(DirectoryPublication,'close',original_close)
    owner=next(o for o in service._control.retained_resources if isinstance(o,image_output.ImageOutput))
    owner._cleanup();assert gate.failure
