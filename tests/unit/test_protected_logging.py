import os
from pathlib import Path
import subprocess
import sys
import shutil
import pytest
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.logging.startup import configure_logging,close_logging
from mediaforge.infrastructure.logging.protected import ProtectedLogs,LogOwnershipError

pytestmark=pytest.mark.native

@pytest.fixture
def profile(tmp_path):
    p=ProfilePaths.for_test(tmp_path/'portable');p.prepare();return p

@pytest.mark.parametrize('suffix',['','.1','.2'])
def test_external_alias_refuses_without_writes(profile,tmp_path,suffix):
    outside=tmp_path/'outside';outside.write_bytes(b'EXTERNAL SENTINEL\n')
    path=profile.logs/('application.log'+suffix);os.link(outside,path)
    with pytest.raises(ValueError):profile.prepare()
    with pytest.raises(ValueError):configure_logging(profile)
    assert outside.read_bytes()==b'EXTERNAL SENTINEL\n'
    path.unlink();logger=configure_logging(profile);logger.info('safe');close_logging(logger)

def test_lifecycle_and_new_alias(profile,tmp_path):
    logger=configure_logging(profile);logger.info('one');close_logging(logger)
    logger=configure_logging(profile);logger.info('two')
    handler=logger.handlers[0];handler.doRollover();logger.info('three')
    assert b'two' in (profile.logs/'application.log.1').read_bytes()
    old=profile.logs/'application.log';alias=tmp_path/'alias';os.link(old,alias)
    original=alias.read_bytes()
    with pytest.raises(ValueError):logger.info('REFUSE')
    assert alias.read_bytes()==original
    close_logging(logger);alias.unlink()
    logger=configure_logging(profile);logger.info('four');close_logging(logger)
    assert b'four' in old.read_bytes()

def test_stage_hardlink_and_path_substitution(profile,tmp_path,monkeypatch):
    logger=configure_logging(profile);logger.info('before');handler=logger.handlers[0]
    original=handler.owner.pending;attempts=[]
    def pending(handle,value):
        original(handle,value)
        if value:
            stage=next(profile.logs.glob('.log-*.tmp'))
            with pytest.raises(OSError):os.link(stage,tmp_path/'stage-alias')
            attempts.append(1)
    monkeypatch.setattr(handler.owner,'pending',pending)
    logger.info('after');assert attempts
    # Substitution after reading cannot redirect writes: publish replaces a
    # directory entry with a frozen new object; it never writes the alias object.
    outside=tmp_path/'outside';outside.write_bytes(b'SENTINEL')
    publish=handler.owner.publish
    def substitute(index,data):
        target=profile.logs/'application.log'
        target.unlink();os.link(outside,target)
        return publish(index,data)
    monkeypatch.setattr(handler.owner,'publish',substitute)
    logger.info('substituted');close_logging(logger)
    assert outside.read_bytes()==b'SENTINEL'

@pytest.mark.parametrize('point',['active','rotation','pending','frozen'])
def test_process_death_restart_same_token(profile,tmp_path,point):
    for suffix in ('','.1','.2'):(profile.logs/('application.log'+suffix)).write_bytes(b'valid existing\n')
    code='''
import os,sys
from pathlib import Path
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.logging.startup import configure_logging
p=ProfilePaths.for_test(Path(sys.argv[1]));p.prepare();l=configure_logging(p)
point=sys.argv[2];owner=l.handlers[0].owner
if point in ('pending','frozen'):
 original=owner.pending
 def death(h,v):
  original(h,v)
  if v==(point=='pending'):os._exit(23)
 owner.pending=death
l.info('crash witness')
if point=='rotation':
 original=owner.publish
 def death(i,d):
  original(i,d)
  os._exit(23)
 owner.publish=death
 l.handlers[0].doRollover()
os._exit(23)
'''
    for cycle in range(3):
        child=subprocess.run([sys.executable,'-c',code,str(profile.root),point],capture_output=True,timeout=15)
        assert child.returncode==23,child.stderr
        profile.prepare();logger=configure_logging(profile);logger.info('restart');close_logging(logger)
        assert b'valid existing' in (profile.logs/'application.log').read_bytes()
        for suffix in ('','.1','.2'):
            with (profile.logs/('application.log'+suffix)).open('ab') as f:f.write(b'ordinary user access\n')
    moved=tmp_path/'moved'
    assert profile.root.resolve().is_relative_to(tmp_path.resolve()) and moved.resolve().is_relative_to(tmp_path.resolve())
    profile.root.rename(moved)
    # Explicitly test-owned fixture only; no historical evidence is removed.
    assert moved.resolve().is_relative_to(tmp_path.resolve());shutil.rmtree(moved)

@pytest.mark.parametrize('operation',['pending','publish'])
def test_failure_propagation_and_reopen(profile,monkeypatch,operation):
    logger=configure_logging(profile);logger.info('preserve');owner=logger.handlers[0].owner
    target=profile.logs/'application.log';before=target.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(owner,operation,lambda *a,**k:(_ for _ in ()).throw(OSError('injected failure')))
        with pytest.raises(OSError):logger.info('fail')
    close_logging(logger);assert target.read_bytes()==before
    logger=configure_logging(profile);logger.info('resume');close_logging(logger)

def test_cleanup_failure_retains_handle_record(profile,monkeypatch):
    logger=configure_logging(profile);logger.info('held');owner=logger.handlers[0].owner
    original=owner.api.CloseHandle
    with monkeypatch.context() as patch:
        patch.setattr(owner.api,'CloseHandle',lambda h:False)
        with pytest.raises(LogOwnershipError) as error:owner.close()
        assert error.value.retained_resources==(owner,) and owner.handles
    close_logging(logger)

@pytest.mark.integration
def test_bootstrap_refuses_alias_no_fallback(profile,tmp_path):
    outside=tmp_path/'outside';outside.write_bytes(b'EXTERNAL SENTINEL\n');os.link(outside,profile.logs/'application.log')
    env=os.environ.copy();env.update(MEDIAFORGE_DATA_ROOT=str(profile.root),LOCALAPPDATA=str(tmp_path/'appdata'),TEMP=str(tmp_path/'temp'),QT_QPA_PLATFORM='offscreen')
    result=subprocess.run([sys.executable,'-c','from mediaforge.bootstrap.application import main; raise SystemExit(main(["mediaforge"]))'],env=env,capture_output=True,timeout=20)
    assert result.returncode==1 and outside.read_bytes()==b'EXTERNAL SENTINEL\n'
    assert not (tmp_path/'appdata').exists() and not (tmp_path/'temp').exists()

def test_symlink_refusal(profile,tmp_path):
    outside=tmp_path/'outside';outside.write_bytes(b'SENTINEL')
    try:os.symlink(outside,profile.logs/'application.log')
    except OSError as error:pytest.skip(f'Native symlink fixture unavailable: {error}')
    with pytest.raises(ValueError):configure_logging(profile)
    assert outside.read_bytes()==b'SENTINEL'

def test_acquisition_failure_and_session_exclusion(profile,monkeypatch):
    logger=configure_logging(profile);logger.info('preserve');owner=logger.handlers[0].owner
    with pytest.raises(ValueError):ProtectedLogs(profile.logs)
    with monkeypatch.context() as patch:
        patch.setattr(owner.api,'CreateFileW',lambda *args:__import__('ctypes').c_void_p(-1).value)
        with pytest.raises((ValueError,OSError)):logger.info('fail')
    close_logging(logger)
    logger=configure_logging(profile);logger.info('reopen');close_logging(logger)

def test_prior_crash_sealed_logs_recovered_without_acl_repair(profile):
    import ctypes as C
    from ctypes import wintypes as W
    adv=C.WinDLL('advapi32',use_last_error=True);kernel=C.WinDLL('kernel32')
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[W.LPCWSTR,W.DWORD,C.POINTER(C.c_void_p),C.c_void_p]
    adv.GetSecurityDescriptorDacl.argtypes=[C.c_void_p,C.POINTER(W.BOOL),C.POINTER(C.c_void_p),C.POINTER(W.BOOL)]
    adv.SetNamedSecurityInfoW.argtypes=[W.LPWSTR,W.DWORD,W.DWORD,C.c_void_p,C.c_void_p,C.c_void_p,C.c_void_p]
    kernel.LocalFree.argtypes=[C.c_void_p]
    for suffix in ('','.1','.2'):
        path=profile.logs/('application.log'+suffix);path.write_bytes(b'original contents\n')
        sd=C.c_void_p();acl=C.c_void_p();present=W.BOOL();default=W.BOOL()
        assert adv.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:(D;;0x100;;;WD)(A;;FA;;;OW)',1,C.byref(sd),None)
        try:
            assert adv.GetSecurityDescriptorDacl(sd,C.byref(present),C.byref(acl),C.byref(default))
            assert adv.SetNamedSecurityInfoW(str(path),1,4,None,None,acl,None)==0
        finally:kernel.LocalFree(sd)
    profile.prepare();logger=configure_logging(profile);logger.info('recovered')
    logger.handlers[0].doRollover();close_logging(logger)
    assert b'original contents' in (profile.logs/'application.log.1').read_bytes()
    for suffix in ('','.1','.2'):
        with (profile.logs/('application.log'+suffix)).open('ab') as stream:stream.write(b'normal access\n')

@pytest.mark.parametrize('index',[2,1,0])
def test_death_after_each_rotation_publication(profile,index):
    for suffix in ('','.1','.2'):(profile.logs/('application.log'+suffix)).write_bytes(b'ACTIVE HISTORY\n')
    code='''
import os,sys
from pathlib import Path
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.logging.startup import configure_logging
l=configure_logging(ProfilePaths.for_test(Path(sys.argv[1])));o=l.handlers[0].owner;publish=o.publish
def die(i,data):
 publish(i,data)
 if i==int(sys.argv[2]):os._exit(23)
o.publish=die;l.handlers[0].doRollover()
'''
    child=subprocess.run([sys.executable,'-c',code,str(profile.root),str(index)],capture_output=True,timeout=15)
    assert child.returncode==23,child.stderr
    assert any(b'ACTIVE HISTORY' in p.read_bytes() for p in profile.logs.glob('application.log*'))
    logger=configure_logging(profile);logger.info('restart after partial rotation');close_logging(logger)
    assert any(b'ACTIVE HISTORY' in p.read_bytes() for p in profile.logs.glob('application.log*'))

def test_backup_insertion_during_session_refuses(profile,tmp_path):
    logger=configure_logging(profile);logger.info('safe')
    outside=tmp_path/'outside';outside.write_bytes(b'SENTINEL')
    os.link(outside,profile.logs/'application.log.2')
    with pytest.raises(ValueError):logger.handlers[0].doRollover()
    close_logging(logger);assert outside.read_bytes()==b'SENTINEL'

def test_repeated_configuration_does_not_leak_native_handles(profile):
    import ctypes as C
    from ctypes import wintypes as W
    api=C.WinDLL('kernel32');api.GetCurrentProcess.restype=W.HANDLE
    api.GetProcessHandleCount.argtypes=[W.HANDLE,C.POINTER(W.DWORD)]
    def count():
        value=W.DWORD();assert api.GetProcessHandleCount(api.GetCurrentProcess(),C.byref(value));return value.value
    logger=configure_logging(profile);logger.info('warm');close_logging(logger)
    before=count()
    for _ in range(20):
        logger=configure_logging(profile);logger.info('cycle');logger.handlers[0].doRollover();close_logging(logger)
    assert count()<=before+2

def test_automatic_threshold_rotation(profile):
    logger=configure_logging(profile);logger.handlers[0].maxBytes=100
    for i in range(6):logger.info('automatic rotation message %s',i)
    close_logging(logger)
    assert b'message 5' in (profile.logs/'application.log').read_bytes()
    assert b'message 4' in (profile.logs/'application.log.1').read_bytes()
    assert b'message 3' in (profile.logs/'application.log.2').read_bytes()
