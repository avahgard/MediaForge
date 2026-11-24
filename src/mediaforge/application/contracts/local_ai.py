"""Bounded local inference contracts; no source decoding or final Dub output."""
from dataclasses import dataclass

from mediaforge.application.contracts.models import ModelError


class LocalAiError(ModelError):
    pass


@dataclass(frozen=True)
class RecognitionRequest:
    pcm16: bytes
    offset_samples: int = 0
    sample_rate: int = 16000

    def __post_init__(self):
        if not isinstance(self.pcm16,bytes) or len(self.pcm16)%2 or len(self.pcm16)>30*16000*2 or self.sample_rate!=16000 or type(self.offset_samples) is not int or self.offset_samples<0:
            raise ValueError('Expected at most 30 seconds of mono 16 kHz PCM16 with an exact nonnegative offset.')


@dataclass(frozen=True)
class SpeechSegment:
    start_sample: int
    end_sample: int
    text: str


@dataclass(frozen=True)
class RecognitionResult:
    segments: tuple[SpeechSegment,...]
    window_samples: int
    model_revision: str


@dataclass(frozen=True)
class TranslationRequest:
    text: str

    def __post_init__(self):
        if not isinstance(self.text,str) or len(self.text)>4000 or '\x00' in self.text:
            raise ValueError('Translation text exceeds the bounded request contract.')


@dataclass(frozen=True)
class TranslatedSpan:
    start: int
    end: int
    source: str
    russian: str


@dataclass(frozen=True)
class TranslationResult:
    spans: tuple[TranslatedSpan,...]
    model_revision: str
    reference_spans: tuple[TranslatedSpan,...] = ()
    glossary_policy: str = ''

    @property
    def text(self):
        if not self.glossary_policy:
            return ' '.join(span.russian for span in self.spans if span.russian)
        result = ''
        for span in self.spans:
            value = span.russian.strip()
            if not value:continue
            separator = (' ' if result and value[0] not in '.,!?;:)]}'
                         and result[-1] not in '([{«' else '')
            result += separator + value
        return result


@dataclass(frozen=True)
class SynthesisRequest:
    text: str

    def __post_init__(self):
        if not isinstance(self.text,str) or not self.text.strip() or len(self.text)>500 or '\x00' in self.text:
            raise ValueError('Expected nonempty Russian text within 500 characters.')


@dataclass(frozen=True)
class CompletionReceipt:
    reason:str
    terminal_eos:bool
    codec_frames:int
    effective_length:int
    generated_steps:int
    cap:int

@dataclass(frozen=True)
class PcmFragment:
    pcm16: bytes
    sample_rate: int
    model_revision: str
    speaker: str = 'ruslan'
    completion:CompletionReceipt|None=None

    @property
    def samples(self):
        return len(self.pcm16)//2
