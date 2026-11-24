"""Explicit C3 composition; no startup inference, settings/UI or publication."""
from mediaforge.application.services.speech import DubSpeechService
from mediaforge.infrastructure.filesystem.dub_source import ProtectedDubSource
from mediaforge.infrastructure.filesystem.speech_session import SpeechSessions
from mediaforge.infrastructure.ffmpeg.speech import SpeechAudio
from mediaforge.infrastructure.models.dub_providers import SpeechProviders
from mediaforge.infrastructure.models.dub_runtime import DubWorkerRuntime,select_dub_runtime
from mediaforge.infrastructure.models.catalog import CANDIDATES
from mediaforge.bootstrap.local_ai import compose_local_ai
from mediaforge.application.contracts.local_ai import LocalAiError

def compose_dub_local_ai(paths,runtime,gate,*,specs=CANDIDATES):
    # Select BEFORE ModelService captures the original providers' smoke callback.
    # Caller creates exactly one inventory; never replace a live service later.
    return compose_local_ai(paths,select_dub_runtime(runtime),gate,specs=specs)

def compose_speech(local_ai,ffmpeg_runtime,*,edge=None,tts=None):
    if not isinstance(local_ai.providers.runtime,DubWorkerRuntime):
        raise LocalAiError('runtime_integrity','C3 requires its selected source-authenticated runtime before model composition.')
    if tts is not None:
        from mediaforge.infrastructure.models.final_speech_providers import FinalSpeechProviders
        providers=FinalSpeechProviders.from_local(local_ai)
        providers.reuse_runtime=True
    else:providers=SpeechProviders.from_local(local_ai)
    if tts is not None:
        from mediaforge.infrastructure.ffmpeg.qwen_speech import QwenSpeechAudio
        audio=QwenSpeechAudio(providers.paths,ffmpeg_runtime)
    else:audio=SpeechAudio(providers.paths,ffmpeg_runtime)
    return DubSpeechService(providers,audio,
        SpeechSessions(providers.paths),ProtectedDubSource,edge=edge,tts=tts)
