"""Stdlib-only restricted CustomVoice contract. No optional import or model I/O."""
from dataclasses import dataclass
from enum import Enum
import math

REVISION='85e237c12c027371202489a0ec509ded67b5e4b5'
SPEAKERS={'vivian':3065,'serena':3066,'uncle_fu':3010,'dylan':2878,'eric':2875,'ryan':3061,'aiden':2861,'ono_anna':2873,'sohee':2864}
class ProfileError(ValueError):
    def __init__(self,code,message,*,retained_resources=()):self.code=code;self.retained_resources=tuple(retained_resources);super().__init__(message)
def unsupported(*args,**kwargs):raise ProfileError('unsupported_feature','This headless profile admits only fixed local12Hz CustomVoice.')
def check_selectors(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if key in {'auto_map','custom_generate','custom_pipelines','quantization_config','_auto_class'} or 'implementation' in key:
                raise ProfileError('unsupported_selector','Executable/model implementation selector is not admitted.')
            check_selectors(item)
    elif isinstance(value,list):
        for item in value:check_selectors(item)
@dataclass(frozen=True)
class VoiceRequest:
    text:str
    speaker:str
    seed:int
    steps:int
    audio_seconds:int
def voice_request(text,speaker,language='Russian',instruct=None,non_streaming_mode=True,**options):
    if type(text) is not str or not text.strip() or len(text)>500 or '\0' in text:raise ProfileError('input_invalid','Expected one nonempty text, at most500 characters.')
    if type(speaker) is not str or speaker not in SPEAKERS:raise ProfileError('unsupported_speaker','Expected a canonical built-in speaker.')
    if type(language) is not str or language.casefold()!='russian':raise ProfileError('unsupported_language','Explicit Russian required.')
    if instruct not in (None,'') or non_streaming_mode is not True:unsupported()
    if set(options)-{'seed','max_new_tokens','max_audio_seconds'}:unsupported()
    seed=options.get('seed',120012);steps=options.get('max_new_tokens',1500);seconds=options.get('max_audio_seconds',30)
    if type(seed) is not int or not 0<=seed<2**32 or type(steps) is not int or not 2<=steps<=1500 or type(seconds) is not int or not 1<=seconds<=120:
        raise ProfileError('input_invalid','Seed/step/audio bound is invalid.')
    return VoiceRequest(text,speaker,seed,steps,seconds)
def input_token_count(count):
    if type(count) is not int or not 1<=count<=1024:raise ProfileError('input_tokens_exceeded','Complete wrapped input must fit1024 tokens.')
def core_request(input_ids,languages,speakers,instruct_ids,voice_clone_prompt,ref_ids,options,cap):
    if not isinstance(input_ids,list) or len(input_ids)!=1 or languages!=['Russian'] or len(speakers or [])!=1 or speakers[0] not in SPEAKERS:unsupported()
    if instruct_ids not in (None,[None]) or voice_clone_prompt is not None or ref_ids is not None or options:unsupported()
    if type(cap) is not int or not 2<=cap<=1500:raise ProfileError('input_invalid','Invalid generated step cap.')
class Reason(str,Enum):
    EOS='normal_eos';LIMIT='limit_exhaustion';INCOMPLETE='incomplete';INVALID='invalid_output';OVERSIZED='oversized_output'
@dataclass(frozen=True)
class Completion:
    reason:Reason
    generated_steps:int
    codec_frames:int
    cap:int
    terminal_eos:bool
    effective_length:int
    @property
    def complete(self):return self.reason is Reason.EOS
def completion(sequence,first_codebook,effective_length,cap,eos=2150,hidden_stop=False):
    """Single-batch GenerationMixin embeds-only output: N samples, N-1 fed frames.

    Last sampled EOS is not fed again. Codec IDs emitted by forward correspond
    exactly to sequences[:-1]. Verify that relationship; never infer termination
    from audio or a counter. Native confirmation remains a P0b-R/model gate.
    """
    if type(cap) is not int or not 2<=cap<=1500 or not isinstance(sequence,list) or not isinstance(first_codebook,list):raise ProfileError('invalid_output','Malformed generation receipt.')
    n=len(sequence);frames=len(first_codebook)
    if n>cap or frames>1500:return Completion(Reason.OVERSIZED,n,frames,cap,False,effective_length)
    if not n:return Completion(Reason.INCOMPLETE,0,frames,cap,False,effective_length)
    if any(type(v) is not int or v<0 or v>=3072 for v in sequence+first_codebook) or type(effective_length) is not int:raise ProfileError('invalid_output','Malformed codec tokens/length.')
    terminal=sequence[-1]==eos
    if hidden_stop or eos in sequence[:-1] or frames!=n-1 or first_codebook!=sequence[:-1] or effective_length!=frames:
        return Completion(Reason.INVALID,n,frames,cap,terminal,effective_length)
    reason=Reason.EOS if terminal and frames else Reason.LIMIT if not terminal and n==cap else Reason.INCOMPLETE
    return Completion(reason,n,frames,cap,terminal,effective_length)
def require_complete(receipt):
    if not isinstance(receipt,Completion) or not receipt.complete:raise ProfileError('generation_incomplete','No corroborated normal EOS; no usable fragment.')
def validate_audio(wavs,rate,seconds,receipt):
    require_complete(receipt)
    if type(seconds) is not int or not 1<=seconds<=120:raise ProfileError('input_invalid','Invalid neutral audio bound.')
    if rate!=24000 or len(wavs)!=1:raise ProfileError('invalid_output','Expected one24kHz mono waveform.')
    wave=wavs[0]
    if getattr(wave,'ndim',1)!=1 or not 0<len(wave)<=seconds*rate:raise ProfileError('oversized_output','Audio duration/shape outside bound.')
    nonzero=False
    for sample in wave:
        v=float(sample)
        if not math.isfinite(v) or abs(v)>1:raise ProfileError('invalid_output','Nonfinite/clipped waveform.')
        nonzero|=v!=0
    if not nonzero:raise ProfileError('invalid_output','Silent waveform.')
@dataclass(frozen=True)
class VoiceOutput:
    audio:tuple
    sample_rate:int
    completion:Completion
