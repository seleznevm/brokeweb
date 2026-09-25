"""Synthetic component expectations; not external parity evidence."""
from copy import deepcopy
import pytest
from test_trace_contexts import context_batch
from test_trace import set_row
from tools.pine_reference.trace import unpack
from tools.pine_reference.trace_components import check, source_statements
from backend.engine.syntax import load_program


def reference():
    e=context_batch()
    for name,value in dict(volume_24h=5.,mtf_trend=15.,htf_base=100.,btc_shock=87.).items():
        set_row(e,'PARITY_'+name,value)
    return e


def test_source_components_match_hand_calculated_outputs_without_state_or_warmup():
    s=unpack([reference()])[0][0];before=deepcopy(s)
    actual,report=check(s)
    assert s==before
    assert report['status']=='DIAGNOSTIC_MATCH'
    assert report['full_intrabar_status']=='UNVERIFIED'
    assert {m['metric'] for m in report['metrics']}=={'volume_24h','mtf_trend','htf_base','btc_shock'}
    assert all(m['numeric_pairs']==1 for m in report['metrics'])
    assert actual[0]['PARITY_btc_shock']==87.


def test_missing_prefix_and_changed_context_are_checked_independently_not_reconstructed():
    e=reference();e.update(batch_id=3,first_seq=10,last_seq=10)
    set_row(e,'seq',10)
    e['rows'][0][-1][2][0]=4.
    set_row(e,'PARITY_mtf_trend',23.75)
    s=unpack([e])[0][0]
    assert not s['report']['sequence_contiguous']
    actual,report=check(s)
    assert report['status']=='DIAGNOSTIC_MATCH' and actual[0]['seq']==10
    assert not s['report']['sequence_contiguous']


def test_wrong_observed_score_is_reported_not_used_as_an_input():
    e=reference();set_row(e,'PARITY_btc_shock',0.)
    _,report=check(unpack([e])[0][0])
    assert report['status']=='DIAGNOSTIC_MISMATCH'
    metric=next(m for m in report['metrics'] if m['metric']=='btc_shock')
    assert metric['mismatches']==1 and metric['examples'][0]['python']==87.


@pytest.mark.parametrize('quote,volume_type,rate,expected',[
    ('USDT','base',None,5.),('EUR','base',None,None),('EUR','quote',1.1,5.5),('USD','tick',1.,None)])
def test_recorded_currency_and_volume_type_control_conversion(quote,volume_type,rate,expected):
    e=reference();e.update(quote_currency=quote,volume_type=volume_type)
    e['request_routes']['currency']=[quote,'USD'];e['rows'][0][-1][1][0]=rate
    set_row(e,'PARITY_volume_24h',expected)
    actual,report=check(unpack([e])[0][0])
    assert report['status']=='DIAGNOSTIC_MATCH'
    assert actual[0]['PARITY_volume_24h']==expected


def test_non_v3_and_missing_request_fields_are_rejected():
    s=unpack([reference()])[0][0];s['metadata']['recorder_revision']=2
    with pytest.raises(ValueError,match='revision 3'):check(s)
    s['metadata']['recorder_revision']=3;s['rows'][0]['request_observations'].pop('shock')
    with pytest.raises(ValueError,match='Missing recorded'):check(s)


def test_component_catalog_rejects_future_stateful_helper():
    program=deepcopy(load_program())
    program.functions['f_clamp'].body[0].expr.kind='history'
    with pytest.raises(ValueError,match='history'):source_statements(program)
