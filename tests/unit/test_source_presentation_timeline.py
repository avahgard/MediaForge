from array import array
from dataclasses import FrozenInstanceError
from pathlib import Path
from threading import Event
import pytest

from mediaforge.application.contracts.source_timeline import (
    SourcePresentationTimeline,SourceSignature,TimelineResult,TIMELINE_BYTES_LIMIT,
)
from mediaforge.application.services.source_timeline import SourceTimelineService


def test_compact_immutable_vfr_order_and_exact_total():
    source=array('q',[0,40000,100000,120000,160000])
    timeline=SourcePresentationTimeline.from_timestamps(source,20000)
    source[1]=999
    assert timeline.count==5 and timeline.storage_bytes==40
    assert list(timeline.timestamps)==[0,40000,100000,120000,160000]
    with pytest.raises(TypeError): timeline.timestamps[1]=999
    with pytest.raises(FrozenInstanceError): timeline.last_duration_us=999
    assert timeline.map_timestamp(100000)==2
    assert timeline.seek_ms(2)==110 and timeline.seek_ms(4)==170


@pytest.mark.parametrize('values',[[],[0,0],[0,40000,20000]])
def test_missing_duplicate_or_unordered_timing_fails_closed(values):
    with pytest.raises(ValueError): SourcePresentationTimeline.from_timestamps(array('q',values),40000)


def test_neighbor_interval_tolerance_without_rate():
    timeline=SourcePresentationTimeline.from_timestamps(array('q',[0,20000,22000,82000]),40000)
    assert timeline.map_timestamp(20500)==1
    assert timeline.map_timestamp(20501) is None
    assert timeline.map_timestamp(21999)==2
    assert timeline.map_timestamp(-5000)==0
    assert timeline.map_timestamp(87000)==3
    assert timeline.map_timestamp(87001) is None


def test_storage_ceiling_and_unaddressable_qt_interval(monkeypatch):
    import mediaforge.application.contracts.source_timeline as contract
    assert TIMELINE_BYTES_LIMIT==64*1024*1024
    monkeypatch.setattr(contract,'TIMELINE_BYTES_LIMIT',32)
    with pytest.raises(ValueError): SourcePresentationTimeline.from_timestamps(array('q',range(5)),1)
    timeline=SourcePresentationTimeline.from_timestamps(array('q',[100,200]),50)
    with pytest.raises(ValueError): timeline.seek_ms(0)


def test_cache_reuse_checks_size_and_mtime_and_does_not_reindex_per_read(tmp_path):
    path=tmp_path/'video Кириллица.mp4';path.write_bytes(b'first')
    calls=[]
    class Indexer:
        def build(self,request,control):
            calls.append(request)
            return TimelineResult(request,SourcePresentationTimeline.from_timestamps(array('q',[0,40000]),40000))
    service=SourceTimelineService(Indexer())
    results=[];completed=Event()
    def finish(result):results.append(result);completed.set()
    try:
        service.activate('A',path,finish);assert completed.wait(2)
        completed.clear();service.activate('A',path,finish);assert completed.wait(2)
        assert len(calls)==1 and results[0].timeline is results[1].timeline
        path.write_bytes(b'changed source')
        completed.clear();service.activate('A',path,finish);assert completed.wait(2)
        assert len(calls)==2 and results[-1].request.source==SourceSignature.capture(path)
    finally:service.shutdown()


def test_source_switch_cancels_and_stale_completion_never_publishes(tmp_path):
    path=tmp_path/'clip.mp4';path.write_bytes(b'fixture')
    second=tmp_path/'replacement.mp4';second.write_bytes(b'successor')
    started=Event();release=Event();completed=Event();seen=[]
    class Indexer:
        def build(self,request,control):
            if request.item_id=='A':
                started.set();assert release.wait(2)
                assert control.cancelled.is_set()
            return TimelineResult(request,SourcePresentationTimeline.from_timestamps(array('q',[0,40000]),40000))
    service=SourceTimelineService(Indexer())
    try:
        service.activate('A',path,lambda result:seen.append('stale A'))
        assert started.wait(2)
        service.activate('B',second,lambda result:(seen.append(result.request.item_id),completed.set()))
        release.set();assert completed.wait(2)
        assert seen==['B']
    finally:release.set();service.shutdown()


def test_duplicate_source_shares_one_scan_and_publishes_only_latest_item(tmp_path):
    path=tmp_path/'same.mp4';path.write_bytes(b'unchanged')
    started=Event();release=Event();finished=Event();calls=[];seen=[]
    class Indexer:
        def build(self,request,control):
            calls.append(request);started.set();assert release.wait(2)
            assert not control.cancelled.is_set()
            return TimelineResult(request,SourcePresentationTimeline.from_timestamps(array('q',[0,40000]),40000))
    service=SourceTimelineService(Indexer())
    try:
        service.activate('original',path,lambda result:seen.append('old item'))
        assert started.wait(2)
        service.activate('clone',path,lambda result:(seen.append(result.request.item_id),finished.set()))
        service.forget('original')
        release.set();assert finished.wait(2)
        assert seen==['clone'] and len(calls)==1
        assert service.source_matches('original',path)
        service.activate('original',path,lambda result:seen.append(result.request.item_id))
        assert len(calls)==1 and seen==['clone','original']
        path.write_bytes(b'changed bytes')
        assert not service.source_matches('original',path)
    finally:release.set();service.shutdown()


def test_revisit_pending_source_after_cached_visit_reuses_the_same_scan(tmp_path):
    first, cached = tmp_path / 'A.mp4', tmp_path / 'B.mp4'
    first.touch()
    cached.touch()
    started, release, finished = Event(), Event(), Event()
    calls, seen = [], []

    class Indexer:
        def build(self, request, control):
            calls.append(request.item_id)
            if request.item_id == 'A':
                started.set()
                assert release.wait(5)
                assert not control.cancelled.is_set()
            return TimelineResult(request, SourcePresentationTimeline.from_timestamps(array('q', [0, 40000]), 40000))

    service = SourceTimelineService(Indexer())
    try:
        service.activate('B', cached, lambda _: finished.set())
        assert finished.wait(2)
        finished.clear()
        service.activate('A', first, lambda _: seen.append('obsolete'))
        assert started.wait(2)
        service.cancel(keep_scan=True)
        service.activate('B', cached, lambda result: seen.append(result.request.item_id))
        service.cancel(keep_scan=True)
        service.activate('A', first, lambda result: (seen.append(result.request.item_id), finished.set()))
        release.set()
        assert finished.wait(2)
        assert calls == ['B', 'A'] and seen == ['B', 'A']
    finally:
        release.set()
        service.shutdown()


def test_unreaped_child_poisons_new_scan_admission_and_fails_pending_labels_closed(tmp_path):
    path=tmp_path/'owned.mp4';path.write_bytes(b'fixture')
    finished=Event();calls=[];results=[]
    class Process:
        alive=True
        def poll(self):return None if self.alive else 0
        def terminate(self):self.alive=False
        def wait(self,timeout):return 0
    child=Process()
    class Indexer:
        def build(self,request,control):
            calls.append(request);control.spawn(lambda *a,**k:child,[])
            return TimelineResult(request,SourcePresentationTimeline.from_timestamps(array('q',[0,40000]),40000))
    service=SourceTimelineService(Indexer())
    service.activate('A',path,lambda result:(results.append(result),finished.set()))
    assert finished.wait(2)
    assert results[0].timeline is None and results[0].error
    service.activate('B',path,results.append)
    assert len(calls)==1 and results[-1].request.item_id=='B' and results[-1].timeline is None
    with pytest.raises(RuntimeError,match='child ownership retained'):service.shutdown()
    assert child.poll()==0 and service._current.owns_process


def test_requested_vfr_interval_is_distinct_from_decoded_pts():
    from array import array
    from mediaforge.application.contracts.source_timeline import SourcePresentationTimeline
    t=SourcePresentationTimeline.from_timestamps(array('q',[120000,160000,235000,270000]),45000)
    assert [t.index_at_time(n) for n in [-1,120000,159999,160000,234999,235000,999999]]==[0,0,0,1,1,2,3]
    assert t.map_timestamp(190000) is None and t.index_at_time(190000)==1
