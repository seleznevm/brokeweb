import csv
import pytest
from tools.pine_reference.native import compare_plots, plot_specs, plot_values, read_export
from backend.engine.runtime import PineEngine


def test_plot_values_preserve_visibility_and_absolute_price():
    engine=PineEngine('TESTUSDT','30',.01)
    engine.update({'start':0,'end':1800000,'open':100.,'high':101.,'low':99.,'close':100.,'volume':100.},{})
    scope=engine.runtime.scopes[0]
    scope.update(scaleR1Visible=False,resistance1=105.,showEma=False,emaFast=101.,
                 direction=0,tradePlanActive=True,displayedTradePlanT1=120.,
                 showTargetLabel=True,directionLong=True,directionShort=False,
                 showSignals=False,newLongWatch=True)
    values=plot_values(engine.runtime)
    assert values['R1 — цена на шкале'] is None
    assert values['EMA Fast'] is None
    assert values['Forecast T1']==120.
    assert values['LONG T1 label']==120. # a price, never a TP-hit boolean
    assert values['L WATCH label']==0
    assert 'direction' not in values
    assert 'ALERT_DIR' in values


def test_native_comparison_missing_na_events_and_unknown_columns():
    title='R1 — цена на шкале'
    refs=[{'time':'1800',title:'', 'L WATCH label':'1','direction':'-1'},
          {'time':'3600',title:'10','L WATCH label':'0','direction':'1'}]
    actual=[{'bar_start':1800000,'source_plots':{title:1.,'L WATCH label':0}},
            {'bar_start':3600000,'source_plots':{title:10.005,'L WATCH label':0}}]
    r=compare_plots(refs,actual,.01)
    assert r['status']=='UNVERIFIED' and r['observed_status']=='FAIL'
    assert len(r['metrics'])==1
    assert r['metrics'][0]['na_mismatches']==1
    assert r['metrics'][0]['match_percent']==50
    assert r['signals'][0]['pine_events']==1
    assert r['signals'][0]['python_events']==0
    assert r['signals'][0]['first_mismatches'][0]['time']==1800000
    with pytest.raises(ValueError,match='Duplicate'):compare_plots(refs,actual+actual,.01)
    assert compare_plots(refs,actual[:1],.01)['missing_records']==1


def test_empty_hidden_ema_is_not_numerical_validation():
    r=compare_plots([{'time':'1800','EMA Fast':''}], [{'bar_start':1800000,'source_plots':{'EMA Fast':None}}],.01)
    assert r['metrics'][0]['status']=='NOT_OBSERVED'
    r=compare_plots([{'time':'1800','EMA Fast':''}], [{'bar_start':1800000,'source_plots':{}}],.01)
    assert r['metrics'][0]['status']=='FAIL'


def test_csv_rejects_duplicate_time_and_invalid_plot(tmp_path):
    p=tmp_path/'BYBIT_TESTUSDT.P, 30_abc.csv'
    header='time,open,high,low,close,Volume,Forecast T1\n'
    row='1800,10,11,9,10,100,12\n'
    p.write_text(header+row+row)
    with pytest.raises(ValueError,match='Duplicate'):read_export(p)
    p.write_text(header+row.replace(',12\n',',broken\n'))
    with pytest.raises(ValueError,match='Invalid plot'):read_export(p)
