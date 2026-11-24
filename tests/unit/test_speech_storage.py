"""Native accounting without restart authentication or orphan deletion."""
from pathlib import Path
from types import SimpleNamespace as NS
import subprocess,sys
import pytest
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
from mediaforge.features.dub.contracts import DubControl
from mediaforge.application.contracts.local_ai import LocalAiError

pytestmark=pytest.mark.native
def request(retry=None):return NS(retry_session=retry,retain_private_recovery=True)
def test_other_manager_and_restart_quota_never_adopt_or_delete(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'Portable Юникод/Data/v2');paths.prepare()
    first=SpeechSessions(paths);control=DubControl()
    with first.open(request(),{},control) as session:
        name=session.path.name;session.put('row.json',{'private':'test'})
        with pytest.raises(Exception) as busy:
            with SpeechSessions(paths).open(request(),{},control):pass
        assert busy.value.code=='in_use'
    restarted=SpeechSessions(paths)
    with pytest.raises(LocalAiError) as denied:
        with restarted.open(request(name),{},control):pass
    assert denied.value.code=='checkpoint_ineligible'
    with restarted.open(request(),{},control) as session:second=session.path.name
    third=SpeechSessions(paths)
    with third.open(request(),{},control) as session:third_name=session.path.name
    with pytest.raises(LocalAiError) as full:
        with SpeechSessions(paths).open(request(),{},control):pass
    assert full.value.code=='disk_budget'
    assert (paths.sessions/name/'row.json').is_file()
    third.close(control);restarted.close(control);first.close(control)
    assert not any((paths.sessions/n).exists() for n in (name,second,third_name))

def test_three_prior_directories_refuse_before_admission_without_deletion(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'Portable three remnants/Data/v2');paths.prepare()
    sentinels=[]
    for index in range(3):
        folder=paths.sessions/f'dub-prior-{index}';folder.mkdir()
        sentinel=folder/'private.pcm';sentinel.write_bytes(b'unknown private bytes')
        sentinels.append((sentinel,sentinel.stat().st_ino))
    store=SpeechSessions(paths);control=DubControl()
    assert store.MAX_JOB_SLOTS==3 and store.MAX_BYTES==1024**3
    assert store.MAX_FILES==16000 and store.MAX_METADATA==32*1024**2
    with store.reservation():assert store.accounting(control)['jobs']==3
    with pytest.raises(LocalAiError) as refused:
        with store.admission(control):pytest.fail('Fourth session must not reach model admission')
    assert refused.value.code=='disk_budget'
    with pytest.raises(LocalAiError):
        with store.open(request(),{},control):pytest.fail('No fourth directory')
    assert len(list(paths.sessions.iterdir()))==3
    assert all(p.stat().st_ino==identity and p.read_bytes()==b'unknown private bytes' for p,identity in sentinels)

def test_orphan_bytes_unknown_members_count_without_deletion(tmp_path,monkeypatch):
    paths=ProfilePaths.for_test(tmp_path/'Portable/Data/v2');paths.prepare()
    orphan=paths.sessions/'dub-unknown';orphan.mkdir();sentinel=orphan/'private.pcm';sentinel.write_bytes(b'untouched')
    store=SpeechSessions(paths);monkeypatch.setattr(store,'MAX_BYTES',4096)
    with pytest.raises(LocalAiError) as full:
        with store.open(request(),{},DubControl()):pass
    assert full.value.code=='disk_budget' and sentinel.read_bytes()==b'untouched'
    paths.prepare();(orphan/'nested').mkdir()
    with pytest.raises(Exception):
        with SpeechSessions(paths).open(request(),{},DubControl()):pass
    assert sentinel.read_bytes()==b'untouched'

def test_process_death_releases_reservation_but_remnants_charge(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'Portable/Data/v2');paths.prepare()
    code='''
import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
from mediaforge.features.dub.contracts import DubControl
from types import SimpleNamespace as NS
store=SpeechSessions(ProfilePaths.for_test(Path(sys.argv[2])))
with store.open(NS(retry_session=None,retain_private_recovery=True),{},DubControl()) as session:
 session.put('row.json',{'private':'synthetic'})
 os._exit(9)
'''
    result=subprocess.run([sys._base_executable,'-I','-S','-B','-c',code,str(Path(__file__).resolve().parents[2]/'src'),str(paths.root)],env=paths.child_environment(),timeout=20,capture_output=True)
    assert result.returncode==9,result.stderr
    store=SpeechSessions(paths);control=DubControl()
    with store.reservation():facts=store.accounting(control)
    assert facts['jobs']==1 and facts['files']==1 and facts['bytes']>=4096
    with store.open(request(),{},control) as session:assert session.hits==0
    store.close(control)
    assert len(list(paths.sessions.glob('dub-*')))==1

def test_names_shutdown_refuse_unsafe_or_active_objects(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'Portable/Data/v2');paths.prepare();store=SpeechSessions(paths)
    with store.open(request(),{},DubControl()) as session:
        for name in ('.','..','a/b','a\\b','con.pcm','nul','ending.'):
            with pytest.raises(LocalAiError):session.name(name)
        with pytest.raises(LocalAiError) as busy:store.close(DubControl())
        assert busy.value.retained_resources
    store.close(DubControl())

def test_open_pcm_reservation_counts_before_commit(tmp_path,monkeypatch):
    paths=ProfilePaths.for_test(tmp_path/'Portable/Data/v2');paths.prepare();store=SpeechSessions(paths)
    monkeypatch.setattr(store,'MAX_BYTES',128*1024)
    monkeypatch.setattr(store,'INFERENCE_RESERVATION',0)
    with store.open(request(),{},DubControl()) as session:
        with session.pcm('active.pcm',50000) as writer:
            writer.silence(10)
            with pytest.raises(LocalAiError) as full:session.put_bytes('extra.pcm',bytes(32768))
            assert full.value.code=='disk_budget'
        before=dict(session.budget);session.track(session.path/'active.pcm')
        assert session.budget==before and not session._reserved
        session.put_bytes('extra.pcm',bytes(32768))
    store.close(DubControl())

def test_directory_replacement_in_access_upgrade_gap_is_refused(tmp_path,monkeypatch):
    from mediaforge.infrastructure.models.owned import Ownership
    paths=ProfilePaths.for_test(tmp_path/'Portable/Data/v2');paths.prepare();store=SpeechSessions(paths)
    with store.open(request(),{},DubControl()) as session:name=session.path.name
    path=paths.sessions/name;moved=paths.sessions/'original-moved';seen=[];original=Ownership.open
    def attack(owner,target,access,share,disposition,**kw):
        if target==path and access==0x10080:
            path.rename(moved);path.mkdir();(path/'unknown.txt').write_bytes(b'never delete this')
            seen.append(True)
        return original(owner,target,access,share,disposition,**kw)
    monkeypatch.setattr(Ownership,'open',attack)
    with pytest.raises(LocalAiError) as refused:store.retire(name,DubControl())
    assert seen and refused.value.code=='lifecycle_failed'
    assert (path/'unknown.txt').read_bytes()==b'never delete this' and moved.is_dir()
    # Explicitly retire test-held capabilities only. Neither directory is deleted.
    store._tickets[name]['directory_owner'].close();store._tickets[name]['identity_pin'].close()

def test_partial_retirement_retains_remaining_truthful_ownership(tmp_path,monkeypatch):
    from mediaforge.infrastructure.models.owned import Ownership
    paths=ProfilePaths.for_test(tmp_path/'Portable/Data/v2');paths.prepare();store=SpeechSessions(paths)
    with store.open(request(),{},DubControl()) as session:
        name=session.path.name;session.put_bytes('first.pcm',b'aa');session.put_bytes('second.pcm',b'bb')
    original=Ownership.pending;attempts=[]
    def fail_second(owner,handle,value):
        if value:
            attempts.append(handle)
            if len(attempts)==2:raise OSError('injected native retirement failure')
        return original(owner,handle,value)
    monkeypatch.setattr(Ownership,'pending',fail_second)
    with pytest.raises(OSError):store.retire(name,DubControl())
    assert set(store._tickets[name]['files'])=={'second.pcm'}
    monkeypatch.setattr(Ownership,'pending',original)
    store.retire(name,DubControl());assert not (paths.sessions/name).exists()

def test_close_releases_portable_directory_for_unicode_move(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'Portable/Data/v2');paths.prepare();store=SpeechSessions(paths)
    with store.open(request(),{},DubControl()) as session:session.put_bytes('private.pcm',b'aa')
    store.close(DubControl())
    (tmp_path/'Portable').rename(tmp_path/'Moved Юникод')
    moved=ProfilePaths.for_test(tmp_path/'Moved Юникод/Data/v2');moved.prepare()
    assert not list(moved.sessions.iterdir())

def test_os_reservation_is_cross_process_and_releases_cleanly(tmp_path):
    from mediaforge.infrastructure.models.process import run_worker
    paths=ProfilePaths.for_test(tmp_path/'Portable/Data/v2');paths.prepare();store=SpeechSessions(paths)
    code='''
import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
from mediaforge.features.dub.contracts import DubControl
from types import SimpleNamespace as NS
try:
 with SpeechSessions(ProfilePaths.for_test(Path(sys.argv[2]))).open(NS(retry_session=None,retain_private_recovery=True),{},DubControl()):
  raise RuntimeError('unexpected second admission')
except Exception as error:
 print(json.dumps({'ok':True,'code':getattr(error,'code',None)}))
'''
    with store.open(request(),{},DubControl()):
        result=run_worker([sys._base_executable,'-I','-S','-B','-c',code,str(Path(__file__).resolve().parents[2]/'src'),str(paths.root)],
            paths=paths,environment=paths.child_environment(),control=DubControl(),timeout=20)
        assert result['code']=='in_use'
    store.close(DubControl())

def test_wall_clock_cannot_extend_same_process_retry(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'Portable/Data/v2');paths.prepare();store=SpeechSessions(paths)
    with store.open(request(),{},DubControl()) as session:name=session.path.name
    store._tickets[name]['created_monotonic']-=86401
    with pytest.raises(LocalAiError) as refused:
        with store.open(request(name),{},DubControl()):pass
    assert refused.value.code=='checkpoint_ineligible' and not (paths.sessions/name).exists()

def test_pre_session_model_worker_crash_remnants_cannot_evade_quota(tmp_path,monkeypatch):
    paths=ProfilePaths.for_test(tmp_path/'Portable/Data/v2');paths.prepare();store=SpeechSessions(paths)
    orphan=paths.temporary/'inference-crashed';orphan.mkdir();file=orphan/'request.json';file.write_bytes(b'private synthetic bytes')
    with store.reservation():facts=store.accounting(DubControl())
    assert facts['files']==1 and facts['metadata']==len(file.read_bytes()) and facts['jobs']==0
    monkeypatch.setattr(store,'MAX_BYTES',facts['bytes']+store.INFERENCE_RESERVATION)
    with pytest.raises(LocalAiError) as full:
        with store.admission(DubControl()):pytest.fail('must refuse before model loading')
    assert full.value.code=='disk_budget' and file.read_bytes()==b'private synthetic bytes'

def test_atomic_directory_uses_utf16_units_for_astral_path(tmp_path):
    paths=ProfilePaths.for_test(tmp_path/'Portable Ж 😀/Data/v2');paths.prepare();store=SpeechSessions(paths)
    with store.open(request(),{},DubControl()) as session:session.put_bytes('private.pcm',b'aa')
    store.close(DubControl());assert not list(paths.sessions.iterdir())
