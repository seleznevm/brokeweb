import pytest
from backend.alerts.rules import matches,validate_condition
from backend.alerts.notifier import render_message

@pytest.mark.parametrize('op,value,expected',[('==',70,True),('!=',70,False),('>',69,True),('>=',70,True),('<',71,True),('<=',70,True),('IN',[60,70],True),('NOT IN',[60],True),('BETWEEN',[65,75],True),('changed',None,True),('changed_to',70,True),('crosses_above',65,True),('crosses_below',75,False)])
def test_operators(op,value,expected):
    assert matches({'field':'avg_setup','op':op,'value':value},{'avg_setup':70},{'avg_setup':60}) is expected

def test_nested_missing_and_crossing():
    node={'op':'AND','conditions':[{'field':'metrics.score','op':'>=','value':65},{'op':'NOT','conditions':[{'field':'direction','op':'==','value':'SHORT'}]}]}
    validate_condition(node)
    assert matches(node,{'metrics':{'score':70},'direction':'LONG'})
    assert not matches(node,{'metrics':{'score':None},'direction':'LONG'})
    assert not matches({'field':'score','op':'crosses_above','value':60},{'score':70})
    assert matches({'field':'score','op':'crosses_below','value':60},{'score':59},{'score':60})
    assert matches({'field':'signals','op':'contains','value':'BRONZE'},{'signals':['BRONZE']})

def test_invalid_groups_and_template():
    with pytest.raises(ValueError): validate_condition({'op':'NOT','conditions':[]})
    with pytest.raises(ValueError): validate_condition({'op':'eval','field':'__import__'})
    message=render_message({'snapshot':{'symbol':'BTCUSDT','timeframe':'15','exchange':'BYBIT','signals':[]},'template':'{symbol} {price} {symbol.__class__}'})
    assert message=='BTCUSDT n/a {symbol.__class__}'
