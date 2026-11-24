"""Malformed upstream observations must refuse with numeric facts, not text."""
import json
from types import SimpleNamespace as NS
import pytest
import numpy as np
from mediaforge.infrastructure.models.final_dub_worker import word_evidence


def test_valid_word_preserves_native_values_and_rounding():
    value=word_evidence(NS(start=.1,end=.2,probability=.7,word='Private word'),3)
    assert value==dict(start=1600,end=3200,probability=.7,text='Private word')


@pytest.mark.parametrize('start,end,probability',[(.2,.1,.9),(-.1,.2,.9),(.1,.2,2),(.1,float('inf'),.9),(None,.2,.9)])
def test_invalid_observation_keeps_refusal_and_numeric_details_private(start,end,probability):
    with pytest.raises(ValueError) as failure:
        word_evidence(NS(start=start,end=end,probability=probability,word='Never log this text'),7)
    message=str(failure.value)
    assert 'Never log' not in message
    facts=json.loads(message.split(' ',1)[1]);assert facts['index']==7
    assert facts['start']==repr(start) and facts['end']==repr(end)


def test_native_numpy_scalars_and_point_estimates_are_preserved_not_inflated():
    value=word_evidence(NS(start=np.float64(.2),end=np.float64(.2),probability=np.float64(.99),word=' to'),0)
    assert value==dict(start=3200,end=3200,text=' to',probability=.99)
