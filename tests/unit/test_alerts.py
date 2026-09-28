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

@pytest.mark.parametrize('field',['avg_setup','formation','execution','geometry','context','level','mae','btc_shock','continuation','exhaustion'])
def test_primary_attributes_and_signal_timeframes(field):
    node={'op':'AND','conditions':[{'field':'timeframe','op':'IN','value':['30','5']},{'field':field,'op':'>=','value':65}]}
    assert matches(node,{'timeframe':'30',field:65.001})
    assert matches(node,{'timeframe':'5',field:65.001})
    assert not matches(node,{'timeframe':'15',field:65.001})
    assert not matches(node,{'timeframe':'30',field:64.999})


def test_notification_rounds_scores_without_changing_prices_or_inputs():
    snapshot={'symbol':'ETHFIUSDT','timeframe':'30','exchange':'BYBIT','avg_setup':65.123456,'price':.00012345,'sl':.00011234,'signals':[]}
    message=render_message({'snapshot':snapshot,'template':'{avg_setup} {price} {sl}'})
    assert message=='65.12 0.00012345 0.00011234'
    assert snapshot['avg_setup']==65.123456


@pytest.mark.parametrize('op,values,expected',[
    ('contains_any',['BRONZE','STRONG'],True),('contains_all',['BRONZE','STRONG'],False),
    ('contains_all',['BRONZE'],True),('contains_any',['STRONG'],False),
])
def test_list_presets_have_any_all_semantics(op,values,expected):
    node={'field':'signals','op':op,'value':values}
    validate_condition(node)
    assert matches(node,{'signals':['BRONZE']}) is expected
    assert not matches(node,{'signals':None})
    assert not matches(node,{'signals':'BRONZE'})


@pytest.mark.parametrize('node',[
    {'field':'timeframe','op':'IN','value':[30,5]},
    {'field':'direction','op':'==','value':'"LONG"'},
    {'field':'direction','op':'>','value':'LONG'},
    {'field':'confirmed','op':'==','value':'true'},
    {'field':'metrics.gateStructure','op':'==','value':1},
    {'field':'avg_setup','op':'>=','value':'70'},
    {'field':'avg_setup','op':'>=','value':True},
    {'field':'avg_setup','op':'BETWEEN','value':[80,60]},
    {'field':'fsm','op':'==','value':'WATCH'},
    {'field':'signals','op':'IN','value':['BRONZE']},
    {'field':'signals','op':'contains_any','value':[]},
])
def test_known_field_type_or_preset_mistakes_rejected(node):
    with pytest.raises(ValueError):validate_condition(node)


def test_field_catalog_uses_actual_source_values_and_keeps_fsm_numeric():
    from backend.alerts.fields import field_catalog,field_spec
    from backend.engine.runtime import SIGNALS
    from backend.engine.wt import COMBINATIONS
    assert field_spec('event')['options']==[*SIGNALS.values(),*COMBINATIONS,'READY TO ENTER']
    assert field_spec('fsm')['type']=='integer'
    assert field_spec('fsm')['options']==list(range(9))
    assert 'ACTIVE / INVALIDATED' in field_spec('setup_state')['options']
    assert 'CHECK DOM/TAPE → ENTRY' in field_spec('action')['options']
    assert field_spec('metrics.gateStructure')['options']==[True,False]
    assert field_spec('metrics.formationQuality')['type']=='number'
    assert len(field_catalog()['fields'])==len({s['key'] for s in field_catalog()['fields']})
    validate_condition({'field':'metrics.custom_score','op':'>=','value':60})
