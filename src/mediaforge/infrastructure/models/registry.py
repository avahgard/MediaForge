"""Load an explicitly selected development registry, authenticated out of band.

Never auto-discovered in a model directory or used to approve production models.
"""
import hashlib
import json

from mediaforge.application.contracts.models import ModelError,ModelMember,ModelSpec


def development_registry(path,expected_sha256,*,allow_development=False):
    if not allow_development:
        raise ModelError('unqualified','Development model pins require explicit development authorization.')
    with path.open('rb') as stream:raw=stream.read(1024*1024+1)
    if len(raw)>1024*1024 or hashlib.sha256(raw).hexdigest()!=expected_sha256:
        raise ModelError('unqualified','Registry digest does not match its externally frozen pin.')
    data=json.loads(raw)
    if data['schema']!=1 or not data['approval'].startswith('development-only;'):
        raise ModelError('unqualified','Expected a development-only registry.')
    specs=tuple(ModelSpec(row['model_id'],row['revision'],tuple(ModelMember(**m) for m in row['members']),row['runtime_fingerprint'],True,'development') for row in data['models'])
    if {s.model_id for s in specs}!={'whisper-base','argos-en-ru','piper-ruslan'} or len(specs)!=3:
        raise ModelError('unqualified','Registry changes the approved provider set.')
    if any(s.runtime_fingerprint!=data['runtime_manifest_sha256'] for s in specs):
        raise ModelError('unqualified','Registry runtime fingerprints disagree.')
    return specs
