from types import SimpleNamespace as NS
import pytest

from mediaforge.infrastructure.models import qwen_tts


def test_bulk_hash_checks_keep_cancellation_per_block_and_bounded_native_pumping(monkeypatch):
    now=[0.];calls=[];checks=[]
    monkeypatch.setattr(qwen_tts.time,'monotonic',lambda:now[0])
    control=qwen_tts.QwenControl(NS(check=lambda:checks.append(True)),NS(pump=lambda:calls.append(now[0])))
    for _ in range(3200):control.check()
    assert len(checks)==3200 and calls==[0.]
    now[0]=.051;control.check()
    assert calls==[0.,.051]
    def cancelled():raise RuntimeError('cancelled')
    control.inner.check=cancelled
    with pytest.raises(RuntimeError,match='cancelled'):control.check()
    assert calls==[0.,.051]
