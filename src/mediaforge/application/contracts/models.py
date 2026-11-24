"""Shared TASK-012/TASK-013 inventory contracts, independent of Qt/providers."""
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
from typing import Protocol


class ModelState(str, Enum):
    MISSING='missing'
    VERIFYING='verifying'
    READY='ready'
    CORRUPT='corrupt'
    INCOMPATIBLE='incompatible'
    UNQUALIFIED='unqualified'
    IN_USE='in_use'


class ModelError(RuntimeError):
    def __init__(self,code,message,*,retained_resources=()):
        self.code=code
        self.retained_resources=tuple(retained_resources)
        super().__init__(message)


@dataclass(frozen=True)
class ModelMember:
    name:str
    size:int
    sha256:str

    def __post_init__(self):
        parts=self.name.split('/')
        if (not parts or any(not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,95}',p)
                             or p.endswith('.') or p.upper().split('.')[0] in
                             {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
                             for p in parts) or len(parts)>8):
            raise ValueError('Unsafe model member path.')
        if type(self.size) is not int or not 0<self.size<=2*1024**3 or not re.fullmatch('[a-f0-9]{64}',self.sha256):
            raise ValueError('Expected bounded size and literal SHA-256.')


@dataclass(frozen=True)
class ModelSpec:
    model_id:str
    revision:str
    members:tuple[ModelMember,...]
    runtime_fingerprint:str
    # Set only by a reviewed composition registry, never read from model-side JSON.
    approved:bool=False
    qualification:str='production'

    def __post_init__(self):
        if not isinstance(self.members,tuple) or any(not isinstance(m,ModelMember) for m in self.members):
            raise ValueError('Model members must be an immutable typed tuple.')
        for name in (self.model_id,self.revision):
            if not re.fullmatch('[a-z0-9][a-z0-9_-]{0,79}',name):raise ValueError('Unsafe model identity.')
            if name.upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
                raise ValueError('Reserved model identity.')
        names=[m.name.casefold() for m in self.members]
        if len(names)>128 or len(set(names))!=len(names):raise ValueError('Duplicate/oversized manifest.')
        if sum(m.size for m in self.members)>2*1024**3:raise ValueError('Model manifest exceeds size bound.')
        if self.approved and (not self.members or not self.runtime_fingerprint):
            raise ValueError('Approved models require full members and runtime fingerprint.')
        if self.qualification not in ('production','development'):
            raise ValueError('Unknown model qualification scope.')


@dataclass(frozen=True)
class ModelSnapshot:
    model_id:str
    revision:str
    state:ModelState
    reason:str


class ModelGuard(Protocol):
    path:Path
    def close(self)->None: ...


class ModelStore(Protocol):
    def exists(self,spec:ModelSpec)->bool: ...
    def verify(self,spec:ModelSpec)->ModelGuard: ...
    def prepare(self)->None: ...
    def reserve(self,spec:ModelSpec)->ModelGuard: ...


class ModelSmoke(Protocol):
    def __call__(self,spec:ModelSpec,guard:ModelGuard)->None:
        """Must actually load/test the exact runtime/model with owned local child."""
        ...
