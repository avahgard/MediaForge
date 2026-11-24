"""Fail-closed fragment, attribution and freshness contracts without GPU mocks
claiming hardware compatibility. Real synthesis is a separate native gate.
"""
import base64
import hashlib
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.infrastructure.models.gpu_memory import Sample, TelemetryError, admission, shared_growth
from mediaforge.infrastructure.models.qwen_session import QwenSynthesisSession


def fragment():
    pcm = b'\0\0\x01\0\x02\0\0\0'
    return dict(pcm16=base64.b64encode(pcm).decode(), speaker='ryan', sample_rate=24000,
                request_index=0, text_sha256=hashlib.sha256('Привет.'.encode()).hexdigest(),
                samples=4, sha256=hashlib.sha256(pcm).hexdigest(), seconds=.1,
                termination=dict(reason='normal_eos', terminal_eos=True, codec_frames=3,
                                 effective_length=3, generated_steps=4, cap=1500))


def engine(row):
    # Construct only the pure protocol endpoint; no Windows/GPU ownership.
    obj = object.__new__(QwenSynthesisSession)
    obj.send = Mock(); obj.receive = Mock(return_value=row); obj.pump = Mock()
    obj.statistics = dict(fragments=0, samples=0, synthesis_seconds=0.)
    return obj


@pytest.mark.parametrize('mutate', [
    lambda r: r.update(pcm16='!not base64'),
    lambda r: r.update(sha256='0'*64),
    lambda r: r.update(speaker='aiden'),
    lambda r: r.update(sample_rate=22050),
    lambda r: r.update(request_index=1),
    lambda r: r.update(text_sha256='0'*64),
    lambda r: r.update(samples=24000*61),
    lambda r: r['termination'].update(terminal_eos=False),
    lambda r: r['termination'].update(reason='limit_exhaustion'),
    lambda r: r['termination'].update(effective_length=2),
    lambda r: r['termination'].update(generated_steps=3),
    lambda r: r['termination'].update(cap=1400),
])
def test_audio_never_accepted_from_hash_identity_or_termination_mismatch(mutate):
    row = fragment(); mutate(row)
    obj = engine(row)
    with pytest.raises(LocalAiError, match='audio/termination'):
        obj.synthesize('Привет.', 'ryan')
    assert obj.statistics['fragments'] == 0


def test_complete_fragment_keeps_all_boundary_samples():
    obj = engine(fragment())
    result = obj.synthesize('Привет.', 'ryan')
    assert result.pcm16 == b'\0\0\x01\0\x02\0\0\0'
    assert result.sample_rate == 24000 and result.speaker == 'ryan'
    assert obj.statistics['fragments'] == 1


@pytest.mark.parametrize('voice,text', [('custom', 'Привет.'), ('ryan', ''), ('ryan', 'a'*501)])
def test_invalid_voice_or_text_never_sent(voice, text):
    obj = engine(fragment())
    with pytest.raises((ValueError, LocalAiError)):
        obj.synthesize(text, voice)
    obj.send.assert_not_called()


def sample(**changes):
    values = dict(identity=(123, 456), luid=(1, 2), physical=0, begin_ns=10, end_ns=100,
                  local=(8*1024**3, 1024**3, 0, 0), nonlocal_=(16*1024**3, 0, 0, 0))
    values.update(changes)
    return Sample(**values)


@pytest.mark.parametrize('changes,now,code', [
    ({'identity': (123, 457)}, 101, 'identity_mismatch'),
    ({'luid': (2, 2)}, 101, 'identity_mismatch'),
    ({'physical': 1}, 101, 'identity_mismatch'),
    ({}, 500000101, 'stale'),
    ({'end_ns': 500000011}, 500000012, 'stale'),
    ({'local': (0, 0, 0, 0)}, 101, 'budget_unknown'),
    ({'local': (8*1024**3, 6*1024**3+1, 0, 0)}, 101, 'resource_refused'),
])
def test_gpu_measurement_cannot_authorize_wrong_owner_stale_or_overbudget(changes, now, code):
    with pytest.raises(TelemetryError) as failure:
        admission(sample(**changes), (123, 456), (1, 2), 0, now_ns=now)
    assert failure.value.code == code


def test_shared_backing_is_separately_charged_and_never_added_to_vram():
    base = sample()
    current = sample(nonlocal_=(16*1024**3, 256*1024**2, 0, 0))
    assert shared_growth(current, base, 256*1024**2) == 256*1024**2
    ledger = admission(current, (123, 456), (1, 2), 0, now_ns=101)
    assert ledger['C'] == 6*1024**3 and ledger['nonlocal_is_vram'] is False
    with pytest.raises(TelemetryError, match='Shared/system'):
        shared_growth(current, base, 256*1024**2-1)


def test_unbounded_ipc_line_is_not_parsed_or_queued():
    obj = engine(fragment())
    obj.process = SimpleNamespace(stdout=io.BytesIO(b' '* (5*1024**2+1)))
    from queue import Queue
    obj.messages = Queue(maxsize=2); obj.read_errors = []
    obj._read_output()
    assert obj.messages.empty() and obj.read_errors == ['ValueError']


def test_caller_cannot_borrow_another_processing_gate_token():
    from mediaforge.application.services.processing_gate import MediaProcessingGate
    from mediaforge.infrastructure.models.qwen_session import synthesis_session
    gate = MediaProcessingGate(); token = gate.acquire()
    runtime = SimpleNamespace(resources=SimpleNamespace(gate=gate))
    with pytest.raises(LocalAiError, match='current processing owner'):
        with synthesis_session(runtime, None, gate_owner=object()):
            pytest.fail('unowned worker admitted')
    assert gate.owned_by(token)
    gate.release(token)
