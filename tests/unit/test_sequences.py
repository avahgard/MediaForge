"""Numbered manifests and rational slot semantics, independently checked."""
import hashlib
import threading
from dataclasses import FrozenInstanceError
from fractions import Fraction
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QCoreApplication, QObject, Signal
from PySide6.QtGui import QImage, QColor
from mediaforge.application.contracts.sequence import numbered_name, sequence_rate
from mediaforge.application.contracts.image_source import SequenceSource
from mediaforge.application.contracts.media import MediaMetadataDto
from mediaforge.application.state.workspace import MediaWorkspaceSession
from mediaforge.infrastructure.images.reader import QtImageReader
from mediaforge.application.services.images import ImageSourceService
from mediaforge.presentation.bridge.sequence_transport import SequenceTransport


def images(folder,numbers=(1001,1002,1003),extension='png',size=(32,18)):
    folder.mkdir(exist_ok=True)
    paths=[]
    for number in numbers:
        path=folder/f'plate_v2_{number:04d}_beauty.{extension}'
        image=QImage(*size,QImage.Format_RGB32); image.fill(QColor(number%255,70,110))
        assert image.save(str(path)); paths.append(path)
    return paths


def manifest(folder,**kwargs):
    paths=images(folder,**kwargs)
    return QtImageReader().discover_sequence(str(paths[-1]),threading.Event()),paths


def test_immutable_sorted_identity_and_independent_slot_time(tmp_path):
    m,paths=manifest(tmp_path,numbers=(1003,1001,1002))
    assert m.numbers==(1001,1002,1003) and m.pattern=='plate_v2_%04d_beauty.png'
    assert tuple(p.signature.path for p in m.members)==tuple(str(p) for p in sorted(paths))
    assert m.time(2,Fraction(24000,1001))==Fraction(1001,12000)
    assert m.slot_at(Fraction(1001,12000),Fraction(24000,1001))==2
    with pytest.raises(FrozenInstanceError): m.padding=3
    again=QtImageReader().discover_sequence(str(paths[0]),threading.Event())
    assert again==m
    images(tmp_path,numbers=(1002,),size=(40,18))
    with pytest.raises(ValueError,match='mixed'): QtImageReader().discover_sequence(str(paths[0]),threading.Event())


@pytest.mark.parametrize('numbers',[(1001,1003),(1002,1004)])
def test_gap_error(tmp_path,numbers):
    with pytest.raises(ValueError,match='Gap Policy'): manifest(tmp_path,numbers=numbers)


@pytest.mark.parametrize('extra',['plate_v2_1004_beauty.jpg','plate_v2_01004_beauty.png','PLATE_v2_1004_beauty.png','plate_v2_1004_beauty.exr'])
def test_ambiguous_extensions_padding_case(tmp_path,extra):
    paths=images(tmp_path)
    (tmp_path/extra).write_bytes(paths[0].read_bytes())
    with pytest.raises(ValueError,match='Ambiguous'): QtImageReader().discover_sequence(str(paths[0]),threading.Event())


def test_automatic_discovery_keeps_selected_extension_without_weakening_explicit_sequence(tmp_path):
    path = images(tmp_path, numbers=(1001,))[0]
    image = QImage(str(path))
    assert image.save(str(path.with_suffix('.jpg')))
    reader = QtImageReader()
    automatic = reader.discover_sequence(str(path), threading.Event(), automatic=True)
    assert automatic.count == 1 and automatic.members[0].path == str(path)
    with pytest.raises(ValueError, match='Ambiguous mixed extension'):
        reader.discover_sequence(str(path), threading.Event())
    images(tmp_path, numbers=(1003,))
    with pytest.raises(ValueError, match='Gap Policy'):
        reader.discover_sequence(str(path), threading.Event(), automatic=True)


@pytest.mark.parametrize('name',['shot.png','shot1234567890.png','shot0001.webp','shot-1.exr'])
def test_invalid_numbered_names(name):
    with pytest.raises(ValueError): numbered_name(name)


@pytest.mark.parametrize('text',['24/1','25/1','30/1','24000/1001','30000/1001','1','120'])
def test_exact_rates(text): assert sequence_rate(text)==Fraction(text)


@pytest.mark.parametrize('text',['0','121','24/0','23.9760001','-24','2400000/1','1/120','nan',' 24'])
def test_invalid_rates(text):
    with pytest.raises(ValueError): sequence_rate(text)


def test_cancelled_discovery_and_manifest_resource_ownership(tmp_path):
    path=images(tmp_path)[0]
    cancel=threading.Event();cancel.set()
    service=ImageSourceService(QtImageReader())
    with pytest.raises(ValueError,match='cancel'): service.discover_sequence(str(path),cancel)
    assert service.budget.reserved==service.budget.overhead
    owner=service.discover_sequence(str(path),threading.Event())
    assert service.budget.reserved==service.budget.overhead+3*4096
    owner.release();assert service.budget.reserved==service.budget.overhead


def test_metadata_lease_waits_for_all_source_owners(tmp_path):
    path=images(tmp_path)[0]
    service=ImageSourceService(QtImageReader())
    owner=service.discover_sequence(str(path),threading.Event());m=owner.manifest
    owner.release()
    assert service.budget.reserved==service.budget.overhead+3*4096
    del m
    assert service.budget.reserved==service.budget.overhead


@pytest.mark.native
def test_natural_number_width_and_duplicate_alias_rejection(tmp_path):
    import os
    image=QImage(16,10,QImage.Format_RGB32);image.fill(QColor(1,2,3))
    for n in range(1,11): assert image.save(str(tmp_path/f'frame{n}.png'))
    m=QtImageReader().discover_sequence(str(tmp_path/'frame10.png'),threading.Event())
    assert m.numbers==tuple(range(1,11)) and m.padding==1
    alias=tmp_path/'alias';alias.mkdir()
    assert image.save(str(alias/'x001.png'));os.link(alias/'x001.png',alias/'x002.png')
    with pytest.raises(ValueError,match='aliased'):QtImageReader().discover_sequence(str(alias/'x001.png'),threading.Event())


def make_item(tmp_path):
    m,_=manifest(tmp_path)
    source=SequenceSource(m.members[0].path,m.identity,m)
    dto=MediaMetadataDto(m.pattern,source.path,'png sequence',None,'',32,18,None,source=source)
    workspace=MediaWorkspaceSession();item,_=workspace.add(dto)
    return workspace,item


def test_sequence_trim_and_duplicate_are_independent_of_video_ms(tmp_path):
    workspace,item=make_item(tmp_path)
    assert not workspace.set_sequence_trim(1,3)
    assert workspace.set_enabled('trim',True)
    assert workspace.set_sequence_trim(1,3)
    assert not workspace.set_sequence_trim(1,4) and not workspace.set_sequence_trim(2,2)
    assert (item.sequence.in_slot,item.sequence.out_slot)==(1,3)
    clone=workspace.duplicate(item.id)
    clone.sequence.in_slot=0
    assert item.sequence.in_slot==1
    assert workspace.set_enabled('crop',True)  # Owner-authorized source-based conversion geometry


def test_rational_transport_no_skips_loop_and_pause(tmp_path,monkeypatch):
    app=QCoreApplication.instance() or QCoreApplication([])
    class Controller(QObject): changed=Signal()
    controller=Controller();seq=SequenceTransport(controller)
    workspace,item=make_item(tmp_path)
    item.sequence.rate=Fraction(24000,1001)
    calls=[];seq.bind(item,lambda slot:calls.append(slot) or True,0)
    seq.toggle();assert not seq.playing  # assumption cannot start playback
    item.sequence.confirmed=True
    now=10_000_000_000;monkeypatch.setattr('mediaforge.presentation.bridge.sequence_transport.time.monotonic_ns',lambda:now)
    seq.toggle();assert seq.deadline==Fraction(now)+Fraction(1001000000,24)
    seq.tick();assert calls==[1] and seq.slot==0
    seq.tick();assert calls==[1]  # one request, no speculative queue
    seq.admitted(1);assert seq.slot==1
    seq.step(-1);assert calls[-1]==0 and not seq.playing
    seq.admitted(0)
    workspace.set_enabled('trim',True);workspace.set_sequence_trim(0,2)
    seq.toggle();seq.tick();seq.admitted(1);seq.tick();assert calls[-1]==0
    seq.admitted(0);seq.seek(2);assert not seq.loop
    seq.failed();assert not seq.playing and not seq.awaiting
    seq.clear();assert seq.item is None and not seq.timer.isActive()


def test_changed_source_rejected_before_preview(tmp_path):
    m,paths=manifest(tmp_path)
    before=hashlib.sha256(paths[0].read_bytes()).hexdigest()
    image=QImage(32,18,QImage.Format_RGB32);image.fill(QColor(5,6,7));assert image.save(str(paths[0]))
    service=ImageSourceService(QtImageReader())
    with pytest.raises(ValueError,match='changed'):
        service.prepare_preview(str(paths[0]),'Auto',threading.Event(),m.members[0])
    assert service.budget.reserved==service.budget.overhead
    assert hashlib.sha256(paths[0].read_bytes()).hexdigest()!=before


def test_publisher_requires_the_requested_sequence_slot_even_for_current_pixels(tmp_path):
    from mediaforge.presentation.bridge.playback import PlaybackController
    workspace,item=make_item(tmp_path)
    current=SimpleNamespace(metadata=SimpleNamespace(source=item.metadata.source.manifest.members[0]))
    fake=SimpleNamespace(sequence=SimpleNamespace(item=item),_closed=False,_image_mode=True,_image_prepared=current)
    # Same bytes as the current slot must not be relabelled as another ordinal.
    with pytest.raises(ValueError,match='Stale'):
        PlaybackController.update_image_preview(fake,current,None,sequence_slot=1)
    assert fake._image_prepared is current


def test_other_member_removed_after_confirmation_is_rejected_before_decode(tmp_path,monkeypatch):
    m,paths=manifest(tmp_path)
    reader=QtImageReader();service=ImageSourceService(reader)
    paths[-1].unlink()  # exclusively test-owned fixture, before activation
    def unexpected(*_): raise AssertionError('Decoder ran before manifest validation')
    monkeypatch.setattr(service,'prepare_preview',unexpected)
    with pytest.raises(FileNotFoundError):
        service.prepare_sequence_preview(str(paths[0]),'Auto',threading.Event(),m.members[0],(1920,1080),m,True)
    assert service.budget.reserved==service.budget.overhead


def test_source_manifest_identity_is_validated(tmp_path):
    m,_=manifest(tmp_path)
    with pytest.raises(ValueError,match='identity'):SequenceSource(m.members[0].path,'wrong',m)


def test_repeated_pending_steps_preserve_latest_intended_ordinal(tmp_path):
    app=QCoreApplication.instance() or QCoreApplication([])
    class Controller(QObject): changed=Signal()
    controller=Controller();seq=SequenceTransport(controller);workspace,item=make_item(tmp_path)
    calls=[];seq.bind(item,lambda slot:calls.append(slot) or True,0)
    seq.step(1);seq.step(1);seq.step(-1)
    assert calls==[1,2,1] and seq.slot==0 and seq.pending_slot==1
    seq.admitted(1);assert seq.slot==1 and seq.pending_slot is None
    seq.clear()


@pytest.mark.parametrize('text,expected',[('23.976',Fraction(2997,125)),('23.976000',Fraction(2997,125)),
    ('29.970',Fraction(2997,100)),('59.9400',Fraction(2997,50)),('23.9',Fraction(239,10)),
    ('23.976001',Fraction(23976001,1000000)),('23976001/1000000',Fraction(23976001,1000000)),
    ('2997/125',Fraction(2997,125)),('2997/100',Fraction(2997,100)),('2997/50',Fraction(2997,50))])
def test_custom_fps_collision_values_are_never_implicitly_presets(text,expected):
    assert sequence_rate(text)==expected
