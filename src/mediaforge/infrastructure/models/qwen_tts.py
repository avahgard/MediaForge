"""Narrow CustomVoice adapter for the existing speech-clock pipeline."""
from contextlib import contextmanager
import time
import sys

from mediaforge.infrastructure.models.qwen_runtime import QwenRuntime, ENTRY_SHA256
from mediaforge.infrastructure.models.qwen_session import synthesis_session, SEED, REVISION


class QwenControl:
    """Pump resident GPU ownership while managed audio fitting owns its child."""
    def __init__(self, inner, engine):
        self.inner, self.engine = inner, engine
        self._last_pump = float('-inf')

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def check(self):
        self.inner.check()
        # A control check is also called for EVERY 32 KiB of an authenticated
        # 105 MB FFmpeg executable. Windows debug waits can round to a timer
        # tick; pumping on every hash block costs minutes per fragment. Keep
        # cancellation per block, but service the unchanged stopped-event gate
        # at 20 Hz during CPU/file work. Unknown images remain OS-stopped until
        # admission; engine IPC wait loops keep their own continuous pump.
        if time.monotonic()-self._last_pump >= .05:
            self.engine.pump()
            self._last_pump=time.monotonic()

    @contextmanager
    def starting(self):
        self.check()
        with self.inner.starting():
            yield


class QwenTts:
    sample_rate = 24000
    # Candidate Russian subset. Final visibility/readiness requires the actual
    # native/listening qualification, not upstream speaker availability alone.
    voices = ('serena', 'aiden', 'ryan')

    def __init__(self, paths, resources):
        self.runtime = QwenRuntime(paths, resources)
        self.last_statistics = None

    def binding(self):
        profile = self.runtime.resources.profile
        return dict(provider='qwen12-customvoice', model_revision=REVISION,
                    portable_core=getattr(getattr(sys,'mediaforge_core_guard',None),'expected',None),
                    runtime_manifest=profile.data['runtime_manifest_sha256'],
                    components=[dict(id=s.model_id, revision=s.revision,
                                     members=[dict(name=m.name, size=m.size, sha256=m.sha256) for m in s.members])
                                for s in profile.models],
                    entry_sha256=ENTRY_SHA256, policy='qwen-24k-pcm16-seed120012-eos1500-fragment60-v1', seed=SEED)

    @contextmanager
    def open(self, control, owner):
        with synthesis_session(self.runtime, control, gate_owner=owner) as engine:
            try:
                yield engine, QwenControl(control, engine)
            finally:
                self.last_statistics = engine.statistics
