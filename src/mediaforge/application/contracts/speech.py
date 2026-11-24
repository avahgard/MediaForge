"""C3 internal speech composition, never a published Dub output."""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from mediaforge.features.dub.contracts import DubSourceDescriptor, OutputPlan

@dataclass(frozen=True)
class PhraseSegmentV2:
    """Derived phrase estimates, never a native <=30-second ASR segment."""
    start_sample:int
    end_sample:int
    text:str

@dataclass(frozen=True)
class SpeechRequest:
    source:DubSourceDescriptor
    plan:OutputPlan
    job_id:str
    voice:str='ruslan'
    acknowledge_clipped_span:bool=False
    # Explicit private recovery authorization. Default: session-only.
    retain_private_recovery:bool=False
    retry_session:str|None=None
    online_consent_job:str|None=None
    online_text_transmission:bool=False

@dataclass(frozen=True)
class SpeechComposition:
    path:Path
    samples:int
    sha256:str
    peak:float
    integrated_lufs:float
    warnings:tuple[str,...]
    session_id:str
    sample_rate:int=48000
    channels:int=1
    sample_width:int=2
    # C4 must reacquire/validate this internal artifact. Never a Completed export.
    internal_only:bool=True
    # Exact source-clock exclusions, merged and verified in the internal PCM.
    # Published lossy audio has a separate bounded codec-ringing check.
    protected_silence:tuple[tuple[int,int],...]=()

class SpeechProgress(Protocol):
    # total=0 explicitly denotes an indeterminate stage, never a fake denominator.
    def __call__(self,stage:str,completed:int,total:int):...
