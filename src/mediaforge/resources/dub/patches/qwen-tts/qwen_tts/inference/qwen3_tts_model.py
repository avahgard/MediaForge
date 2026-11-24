# coding=utf-8
# Copyright 2026 The Alibaba Qwen team.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import base64
import io
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import torch
from transformers import Qwen2TokenizerFast, AddedToken

from ..core.models import Qwen3TTSConfig, Qwen3TTSForConditionalGeneration, Qwen3TTSProcessor

AudioLike = Union[
    str,                     # wav path, URL, base64
    np.ndarray,              # waveform (requires sr)
    Tuple[np.ndarray, int],  # (waveform, sr)
]

MaybeList = Union[Any, List[Any]]


@dataclass
class VoiceClonePromptItem:
    """
    Container for one sample's voice-clone prompt information that can be fed to the model.

    Fields are aligned with `Qwen3TTSForConditionalGeneration.generate(..., voice_clone_prompt=...)`.
    """
    ref_code: Optional[torch.Tensor]                 # (T, Q) or (T,) depending on tokenizer 25Hz/12Hz
    ref_spk_embedding: torch.Tensor                  # (D,)
    x_vector_only_mode: bool
    icl_mode: bool
    ref_text: Optional[str] = None


class Qwen3TTSModel:
    """
    A HuggingFace-style wrapper for Qwen3 TTS models (CustomVoice/VoiceDesign/Base) that provides:
      - from_pretrained() initialization via AutoModel/AutoProcessor
      - generation APIs for:
          * CustomVoice: generate_custom_voice()
          * VoiceDesign: generate_voice_design()
          * Base: generate_voice_clone() + create_voice_clone_prompt()
      - consistent output: (wavs: List[np.ndarray], sample_rate: int)

    Notes:
      - This wrapper expects the underlying model class to be `Qwen3TTSForConditionalGeneration`
      - Language / speaker validation is done via model methods:
          model.get_supported_languages(), model.get_supported_speakers()
    """

    def __init__(self, model: Qwen3TTSForConditionalGeneration, processor, generate_defaults: Optional[Dict[str, Any]] = None):
        self.model = model
        self.processor = processor
        self.generate_defaults = generate_defaults or {}

        self.device = getattr(model, "device", None)
        if self.device is None:
            try:
                self.device = next(model.parameters()).device
            except StopIteration:
                self.device = torch.device("cpu")

    @classmethod
    def from_pretrained(
        cls,
        pretrained_model_name_or_path: str,
        **kwargs,
    ) -> "Qwen3TTSModel":
        """
        Load a Qwen3 TTS model and its processor in HuggingFace `from_pretrained` style.

        This method:
          1) Loads config via AutoConfig (so your side can register model_type -> config/model).
          2) Loads the model via AutoModel.from_pretrained(...), forwarding `kwargs` unchanged.
          3) Loads the processor via AutoProcessor.from_pretrained(model_path).
          4) Loads optional `generate_config.json` from the model directory/repo snapshot if present.

        Args:
            pretrained_model_name_or_path (str):
                HuggingFace repo id or local directory of the model.
            **kwargs:
                Forwarded as-is into `AutoModel.from_pretrained(...)`.
                Typical examples: device_map="cuda:0", dtype=torch.bfloat16, attn_implementation="flash_attention_2".

        Returns:
            Qwen3TTSModel:
                Wrapper instance containing `model`, `processor`, and generation defaults.
        """
        unsupported()

    def _supported_languages_set(self) -> Optional[set]:
        langs = getattr(self.model, "get_supported_languages", None)
        if callable(langs):
            v = langs()
            if v is None:
                return None
            return set([str(x).lower() for x in v])
        return None

    def _supported_speakers_set(self) -> Optional[set]:
        spks = getattr(self.model, "get_supported_speakers", None)
        if callable(spks):
            v = spks()
            if v is None:
                return None
            return set([str(x).lower() for x in v])
        return None

    def _validate_languages(self, languages: List[str]) -> None:
        """
        Validate that requested languages are supported by the model.

        Args:
            languages (List[str]): Language names for each sample.

        Raises:
            ValueError: If any language is not supported.
        """
        supported = self._supported_languages_set()
        if supported is None:
            return

        bad = []
        for lang in languages:
            if lang is None:
                bad.append(lang)
                continue
            if str(lang).lower() not in supported:
                bad.append(lang)
        if bad:
            raise ValueError(f"Unsupported languages: {bad}. Supported: {sorted(supported)}")

    def _validate_speakers(self, speakers: List[Optional[str]]) -> None:
        """
        Validate that requested speakers are supported by the Instruct model.

        Args:
            speakers (List[Optional[str]]): Speaker names for each sample.

        Raises:
            ValueError: If any speaker is not supported.
        """
        supported = self._supported_speakers_set()
        if supported is None:
            return

        bad = []
        for spk in speakers:
            if spk is None or spk == "":
                continue
            if str(spk).lower() not in supported:
                bad.append(spk)
        if bad:
            raise ValueError(f"Unsupported speakers: {bad}. Supported: {sorted(supported)}")

    def _is_probably_base64(self, s: str) -> bool:
        unsupported()

    def _is_url(self, s: str) -> bool:
        unsupported()

    def _decode_base64_to_wav_bytes(self, b64: str) -> bytes:
        unsupported()

    def _load_audio_to_np(self, x: str) -> Tuple[np.ndarray, int]:
        unsupported()

    def _normalize_audio_inputs(self, audios: Union[AudioLike, List[AudioLike]]) -> List[Tuple[np.ndarray, int]]:
        """
        Normalize audio inputs into a list of (waveform, sr).

        Supported forms:
          - str: wav path / URL / base64 audio string
          - (np.ndarray, sr): waveform + sampling rate
          - list of the above

        Args:
            audios:
                Audio input(s).

        Returns:
            List[Tuple[np.ndarray, int]]:
                List of (float32 waveform, original sr).

        Raises:
            ValueError: If a numpy waveform is provided without sr.
        """
        unsupported()

    def _ensure_list(self, x: MaybeList) -> List[Any]:
        return x if isinstance(x, list) else [x]

    def _build_assistant_text(self, text: str) -> str:
        return f"<|im_start|>assistant\n{text}<|im_end|>\n<|im_start|>assistant\n"

    def _build_ref_text(self, text: str) -> str:
        return f"<|im_start|>assistant\n{text}<|im_end|>\n"

    def _build_instruct_text(self, instruct: str) -> str:
        return f"<|im_start|>user\n{instruct}<|im_end|>\n"

    def _tokenize_texts(self, texts: List[str]) -> List[torch.Tensor]:
        input_ids = []
        for text in texts:
            input = self.processor(text=text, return_tensors="pt", padding=True)
            input_id = input["input_ids"].to(self.device)
            input_id = input_id.unsqueeze(0) if input_id.dim() == 1 else input_id
            input_ids.append(input_id)
        return input_ids

    def _merge_generate_kwargs(
        self,
        do_sample: Optional[bool] = None,
        top_k: Optional[int] = None,
        top_p: Optional[float] = None,
        temperature: Optional[float] = None,
        repetition_penalty: Optional[float] = None,
        subtalker_dosample: Optional[bool] = None,
        subtalker_top_k: Optional[int] = None,
        subtalker_top_p: Optional[float] = None,
        subtalker_temperature: Optional[float] = None,
        max_new_tokens: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Merge user-provided generation arguments with defaults from `generate_config.json`.

        Rule:
          - If the user explicitly passes a value (not None), use it.
          - Otherwise, use the value from generate_config.json if present.
          - Otherwise, fall back to the hard defaults.

        Args:
            do_sample, top_k, top_p, temperature, repetition_penalty,
            subtalker_dosample, subtalker_top_k, subtalker_top_p, subtalker_temperature, max_new_tokens:
                Common generation parameters.
            **kwargs:
                Other arguments forwarded to model.generate().

        Returns:
            Dict[str, Any]: Final kwargs to pass into model.generate().
        """
        hard_defaults = dict(
            do_sample=True,
            top_k=50,
            top_p=1.0,
            temperature=0.9,
            repetition_penalty=1.05,
            subtalker_dosample=True,
            subtalker_top_k=50,
            subtalker_top_p=1.0,
            subtalker_temperature=0.9,
            max_new_tokens=2048,
        )

        def pick(name: str, user_val: Any) -> Any:
            if user_val is not None:
                return user_val
            if name in self.generate_defaults:
                return self.generate_defaults[name]
            return hard_defaults[name]

        merged = dict(kwargs)
        merged.update(
            do_sample=pick("do_sample", do_sample),
            top_k=pick("top_k", top_k),
            top_p=pick("top_p", top_p),
            temperature=pick("temperature", temperature),
            repetition_penalty=pick("repetition_penalty", repetition_penalty),
            subtalker_dosample=pick("subtalker_dosample", subtalker_dosample),
            subtalker_top_k=pick("subtalker_top_k", subtalker_top_k),
            subtalker_top_p=pick("subtalker_top_p", subtalker_top_p),
            subtalker_temperature=pick("subtalker_temperature", subtalker_temperature),
            max_new_tokens=pick("max_new_tokens", max_new_tokens),
        )
        return merged

    # voice clone model
    @torch.inference_mode()
    def create_voice_clone_prompt(
        self,
        ref_audio: Union[AudioLike, List[AudioLike]],
        ref_text: Optional[Union[str, List[Optional[str]]]] = None,
        x_vector_only_mode: Union[bool, List[bool]] = False,
    ) -> List[VoiceClonePromptItem]:
        """
        Build voice-clone prompt items from reference audio (and optionally reference text) using Base model.

        Modes:
          - x_vector_only_mode=True:
              Only speaker embedding is used to clone voice; ref_text/ref_code are ignored.
              This is mutually exclusive with ICL.
          - x_vector_only_mode=False:
              ICL mode is enabled automatically (icl_mode=True). In this case ref_text is required,
              because the model continues/conditions on the reference text + reference speech codes.

        Batch behavior:
          - ref_audio can be a single item or a list.
          - ref_text and x_vector_only_mode can be scalars or lists.
          - If any of them are lists with length > 1, lengths must match.

        Audio input:
          - str: local wav path / URL / base64
          - (np.ndarray, sr): waveform + sampling rate

        Args:
            ref_audio:
                Reference audio(s) used to extract:
                  - ref_code via `model.speech_tokenizer.encode(...)`
                  - ref_spk_embedding via `model.extract_speaker_embedding(...)` (resampled to 24k)
            ref_text:
                Reference transcript(s). Required when x_vector_only_mode=False (ICL mode).
            x_vector_only_mode:
                Whether to use speaker embedding only. If False, ICL mode will be used.

        Returns:
            List[VoiceClonePromptItem]:
                List of prompt items that can be converted into `voice_clone_prompt` dict.

        Raises:
            ValueError:
                - If x_vector_only_mode=False but ref_text is missing.
                - If batch lengths mismatch.
        """
        unsupported()

    def _prompt_items_to_voice_clone_prompt(self, items: List[VoiceClonePromptItem]) -> Dict[str, Any]:
        unsupported()

    # voice clone model
    @torch.no_grad()
    def generate_voice_clone(
        self,
        text: Union[str, List[str]],
        language: Union[str, List[str]] = None,
        ref_audio: Optional[Union[AudioLike, List[AudioLike]]] = None,
        ref_text: Optional[Union[str, List[Optional[str]]]] = None,
        x_vector_only_mode: Union[bool, List[bool]] = False,
        voice_clone_prompt: Optional[Union[Dict[str, Any], List[VoiceClonePromptItem]]] = None,
        non_streaming_mode: bool = False,
        **kwargs,
    ) -> Tuple[List[np.ndarray], int]:
        """
        Voice clone speech using the Base model.

        You can provide either:
          - (ref_audio, ref_text, x_vector_only_mode) and let this method build the prompt, OR
          - `VoiceClonePromptItem` returned by `create_voice_clone_prompt`, OR
          - a list of `VoiceClonePromptItem` returned by `create_voice_clone_prompt`.
        
        `ref_audio` Supported forms:
        - str: wav path / URL / base64 audio string
        - (np.ndarray, sr): waveform + sampling rate
        - list of the above

        Input flexibility:
          - text/language can be scalar or list.
          - prompt can be single or batch.
          - If batch mode (len(text)>1), lengths must match.

        Args:
            text:
                Text(s) to synthesize.
            language:
                Language(s) for each sample.
            ref_audio:
                Reference audio(s) for prompt building. Required if voice_clone_prompt is not provided.
            ref_text:
                Reference text(s) used for ICL mode (required when x_vector_only_mode=False).
            x_vector_only_mode:
                If True, only speaker embedding is used (ignores ref_text/ref_code).
                If False, ICL mode is used automatically.
            voice_clone_prompt:
                list[VoiceClonePromptItem] from `create_voice_clone_prompt`.
            non_streaming_mode:
                Using non-streaming text input, this option currently only simulates streaming text input when set to `false`, 
                rather than enabling true streaming input or streaming generation.
            do_sample:
                Whether to use sampling, recommended to be set to `true` for most use cases.
            top_k:
                Top-k sampling parameter.
            top_p:
                Top-p sampling parameter.
            temperature:
                Sampling temperature; higher => more random.
            repetition_penalty:
                Penalty to reduce repeated tokens/codes.
            subtalker_dosample:
                Sampling switch for the sub-talker (only valid for qwen3-tts-tokenizer-v2) if applicable.
            subtalker_top_k:
                Top-k for sub-talker sampling (only valid for qwen3-tts-tokenizer-v2).
            subtalker_top_p:
                Top-p for sub-talker sampling (only valid for qwen3-tts-tokenizer-v2).
            subtalker_temperature:
                Temperature for sub-talker sampling (only valid for qwen3-tts-tokenizer-v2).
            max_new_tokens:
                Maximum number of new codec tokens to generate.
            **kwargs:
                Any other keyword arguments supported by HuggingFace Transformers `generate()` can be passed.
                They will be forwarded to the underlying `Qwen3TTSForConditionalGeneration.generate(...)`.

        Returns:
            Tuple[List[np.ndarray], int]:
                (wavs, sample_rate)

        Raises:
            ValueError:
                If batch sizes mismatch or required prompt inputs are missing.
        """
        unsupported()

    # voice design model
    @torch.no_grad()
    def generate_voice_design(
        self,
        text: Union[str, List[str]],
        instruct: Union[str, List[str]],
        language: Union[str, List[str]] = None,
        non_streaming_mode: bool = True,
        **kwargs,
    ) -> Tuple[List[np.ndarray], int]:
        """
        Generate speech with the VoiceDesign model using natural-language style instructions.

        Args:
            text:
                Text(s) to synthesize.
            language:
                Language(s) for each sample.
            instruct:
                Instruction(s) describing desired voice/style. Empty string is allowed (treated as no instruction).
            non_streaming_mode:
                Using non-streaming text input, this option currently only simulates streaming text input when set to `false`, 
                rather than enabling true streaming input or streaming generation.
            do_sample:
                Whether to use sampling, recommended to be set to `true` for most use cases.
            top_k:
                Top-k sampling parameter.
            top_p:
                Top-p sampling parameter.
            temperature:
                Sampling temperature; higher => more random.
            repetition_penalty:
                Penalty to reduce repeated tokens/codes.
            subtalker_dosample:
                Sampling switch for the sub-talker (only valid for qwen3-tts-tokenizer-v2) if applicable.
            subtalker_top_k:
                Top-k for sub-talker sampling (only valid for qwen3-tts-tokenizer-v2).
            subtalker_top_p:
                Top-p for sub-talker sampling (only valid for qwen3-tts-tokenizer-v2).
            subtalker_temperature:
                Temperature for sub-talker sampling (only valid for qwen3-tts-tokenizer-v2).
            max_new_tokens:
                Maximum number of new codec tokens to generate.
            **kwargs:
                Any other keyword arguments supported by HuggingFace Transformers `generate()` can be passed.
                They will be forwarded to the underlying `Qwen3TTSForConditionalGeneration.generate(...)`.

        Returns:
            Tuple[List[np.ndarray], int]:
                (wavs, sample_rate)
        """
        unsupported()

    # custom voice model
    def generate_custom_voice(self, text, speaker, language='Russian', instruct=None, non_streaming_mode=True, **options):
        request = voice_request(text, speaker, language, instruct, non_streaming_mode, **options)
        if self.model.tts_model_type != 'custom_voice' or self.model.tts_model_size != '0b6': unsupported()
        texts = [request.text]
        languages = ['Russian']
        speakers = [request.speaker]
        self._validate_languages(languages)
        self._validate_speakers(speakers)
        input_ids = self._tokenize_texts([self._build_assistant_text(t) for t in texts])
        input_token_count(int(input_ids[0].numel()))
        torch.manual_seed(request.seed)
        torch.cuda.manual_seed_all(request.seed)
        with torch.inference_mode():
            talker_codes_list, _, receipts = self.model.generate(input_ids=input_ids, instruct_ids=[None], languages=languages, speakers=speakers, non_streaming_mode=True, max_new_tokens=request.steps, do_sample=True, top_k=50, top_p=1.0, temperature=0.9, repetition_penalty=1.05, subtalker_dosample=True, subtalker_top_k=50, subtalker_top_p=1.0, subtalker_temperature=0.9)
            if len(receipts) != 1: raise ProfileError('invalid_output','Expected one completion receipt.')
            require_complete(receipts[0])
            wavs, fs = self.model.speech_tokenizer.decode([{'audio_codes': c} for c in talker_codes_list])
        validate_audio(wavs, fs, request.audio_seconds, receipts[0])
        return VoiceOutput(tuple(wavs),fs,receipts[0])


    def get_supported_speakers(self) -> Optional[List[str]]:
        """
        List supported speaker names for the current model.

        This is a convenience wrapper around `model.get_supported_speakers()`.
        If the underlying model does not expose speaker constraints (returns None),
        this method also returns None.

        Returns:
            Optional[List[str]]:
                - A sorted list of supported speaker names (lowercased), if available.
                - None if the model does not provide supported speakers.
        """
        supported = self._supported_speakers_set()
        if supported is None:
            return None
        return sorted(supported)


    def get_supported_languages(self) -> Optional[List[str]]:
        """
        List supported language names for the current model.

        This is a convenience wrapper around `model.get_supported_languages()`.
        If the underlying model does not expose language constraints (returns None),
        this method also returns None.

        Returns:
            Optional[List[str]]:
                - A sorted list of supported language names (lowercased), if available.
                - None if the model does not provide supported languages.
        """
        supported = self._supported_languages_set()
        if supported is None:
            return None
        return sorted(supported)

    @classmethod
    def from_local_components(cls, root, talker, tokenizer, *, device='cuda:0'):
        if device != 'cuda:0': unsupported()
        components = Components(root, talker, tokenizer)
        try:
            model = Qwen3TTSForConditionalGeneration._load_local(components)
            settings = dict(components.documents['talker/tokenizer_config.json'])
            if settings.pop('tokenizer_class', None) != 'Qwen2Tokenizer': unsupported()
            for key in ('tokenizer_file','vocab_file','merges_file','auto_map','name_or_path'):
                if key in settings: unsupported()
            settings.pop('chat_template', None)
            settings['added_tokens_decoder'] = {int(k): AddedToken(**v) for k,v in settings.get('added_tokens_decoder',{}).items()}
            tokenizer_object = Qwen2TokenizerFast(vocab_file=str(talker/'vocab.json'), merges_file=str(talker/'merges.txt'), tokenizer_file=None, **settings)
            tokenizer_object.padding_side = 'left'
            processor = Qwen3TTSProcessor(tokenizer=tokenizer_object, chat_template=None)
            model.to(device=device, dtype=torch.bfloat16).eval()
            model.speech_tokenizer.model.to(device=device, dtype=torch.bfloat16).eval()
            model.speech_tokenizer.device = torch.device(device)
            result = cls(model=model, processor=processor, generate_defaults=model.generate_config)
            result._component_owner = components
            return result
        except BaseException:
            components.close()
            raise



from ..mf_contracts import ProfileError, unsupported, voice_request, input_token_count, require_complete, validate_audio, VoiceOutput
from ..mf_components import Components
