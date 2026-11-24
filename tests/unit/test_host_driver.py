from copy import deepcopy

import pytest

from mediaforge.application.contracts.local_ai import LocalAiError
from mediaforge.infrastructure.models.host_driver import (
    ROOT_2010_SHA256, ROOT_2010_SHA1, MANDATORY, OPTIONAL, catalog_choice, verify_chain,
)


def valid():
    return dict(verified=True, wintrust_status=0, state_close_status=0,
                signer_chain=dict(signer_error=0, certificates=[
                    dict(subject='Microsoft Windows Hardware Compatibility Publisher', error=0,
                         revoked_reason=0, test_certificate=False),
                    dict(subject='Microsoft Root Certificate Authority 2010', error=0,
                         revoked_reason=0, test_certificate=False, trusted_root=True,
                         der_sha256=ROOT_2010_SHA256, der_sha1=ROOT_2010_SHA1)]))


def test_purpose_roles_are_closed_cuda_set_not_a_driverstore_prefix():
    assert MANDATORY == ('nvcuda64.dll',)
    assert 'nvcamera64.dll' not in (*MANDATORY, *OPTIONAL)
    assert not any(n.lower().endswith('.exe') for n in (*MANDATORY, *OPTIONAL))
    assert all('..' not in n.split('/') and '\\' not in n and '*' not in n for n in (*MANDATORY, *OPTIONAL))


def test_exact_microsoft_hardware_chain_accepted_after_member_verification():
    verify_chain(valid())


@pytest.mark.parametrize('field,value', [('wintrust_status', 0x800B0109), ('state_close_status', 1),
                                        ('verified', False)])
def test_failed_member_or_trust_retirement_never_grants_authority(field, value):
    record = valid()
    record[field] = value
    with pytest.raises(LocalAiError):
        verify_chain(record)


def test_same_subject_fake_user_root_is_not_microsoft_authority():
    record = valid()
    record['signer_chain']['certificates'][-1]['der_sha256'] = '0' * 64
    with pytest.raises(LocalAiError, match='pinned'):
        verify_chain(record)


@pytest.mark.parametrize('field,value', [('revoked_reason', 1), ('error', 1), ('test_certificate', True)])
def test_revoked_test_or_error_chain_refused(field, value):
    record = valid()
    record['signer_chain']['certificates'][0][field] = value
    with pytest.raises(LocalAiError):
        verify_chain(record)


@pytest.mark.parametrize('selector', ['../other.cat', 'dir/other.cat', 'dir\\other.cat', 'a.cat:stream', 'file.exe'])
def test_catalog_selector_cannot_leave_bound_package(selector):
    with pytest.raises(LocalAiError):
        catalog_choice({'CatalogFile': [selector]})


def test_arch_specific_catalog_precedes_generic_and_ambiguity_refuses():
    assert catalog_choice({'CatalogFile': ['generic.cat'], 'CatalogFile.NTamd64': ['x64.cat']}) == 'x64.cat'
    with pytest.raises(LocalAiError):
        catalog_choice({'CatalogFile': ['one.cat', 'two.cat']})
