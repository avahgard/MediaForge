"""Candidate identities only. These incomplete manifests never authorize Ready."""
from mediaforge.application.contracts.models import ModelSpec

CANDIDATES=(
    ModelSpec('whisper-base','ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66',(),''),
    ModelSpec('argos-en-ru','1_9',(),''),
    ModelSpec('piper-ruslan','2f8dbe0bb0dde986411632bf014a13cdbe6596e7',(),''),
)
