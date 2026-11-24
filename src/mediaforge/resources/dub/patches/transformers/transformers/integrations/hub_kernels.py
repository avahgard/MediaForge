# Copyright 2025 The HuggingFace Team. All rights reserved.
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
import re
from functools import partial
from typing import Optional, Union

from ..modeling_flash_attention_utils import lazy_import_flash_attention
from .flash_attention import flash_attention_forward


_kernels_available = False

# Stub to make decorators int transformers work when `kernels`
# is not installed.
def use_kernel_forward_from_hub(*args, **kwargs):
    def decorator(cls):
        return cls

    return decorator

class LayerRepository:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("LayerRepository requires `kernels` to be installed. Run `pip install kernels`.")

def replace_kernel_forward_from_hub(*args, **kwargs):
    unavailable()

def register_kernel_mapping(*args, **kwargs):
    unavailable()


def is_kernel(attn_implementation: Optional[str]) -> bool:
    """Check whether `attn_implementation` matches a kernel pattern from the hub."""
    eager(attn_implementation)
    return False


def load_and_register_kernel(attn_implementation: str) -> None:
    """Load and register the kernel associated to `attn_implementation`."""
    unavailable()


__all__ = [
    "LayerRepository",
    "use_kernel_forward_from_hub",
    "register_kernel_mapping",
    "replace_kernel_forward_from_hub",
]

from ..mf_security import unavailable, eager
