from copy import deepcopy
import pytest
from tools.capacity_probe import assess


def samples():
    return [{'captured_at':now,'health':{'status':'HEALTHY','services':{'engine':{
        'updated_at':now,'status':'HEALTHY','selected':2,'initialized':2,'healthy_engines':2,
        'timeframes':['30'],'shard_count':1,'shard_index':0,'universe':2,'max_symbols':0,'market_data_lag_ms':500,
        'pipeline_lag_ms_p95':500,'pending_calculations':0,'recovering_symbols':0,
        'streams':[{'connected':True,'last_market_event':now-500}],
        'btc_stream':{'connected':True},'btc_recovering':False,'btc_timeframe_events':{'30':now},
        'calculations':count}}}} for now,count in ((200000,10),(215000,20))]


def test_short_successful_probe_does_not_certify_capacity():
    result=assess(samples())
    assert result['readiness']=='READY_FOR_SOAK'
    assert result['capacity_status']=='UNVERIFIED'
    assert result['calculation_delta']==10 and result['observed_seconds']==15


@pytest.mark.parametrize('changes',[
    {'initialized':1},{'healthy_engines':1},{'updated_at':0},{'market_data_lag_ms':None},
    {'market_data_lag_ms':float('nan')},{'market_data_lag_ms':90001},{'shard_count':2},
    {'max_symbols':4},{'streams':[]},{'btc_recovering':True},{'btc_timeframe_events':{'30':0}},
    {'universe':3},{'shard_index':1},
    {'pipeline_lag_ms_p95':None},{'pipeline_lag_ms_p95':5001},{'recovering_symbols':1},{'pending_calculations':None},
    {'calculations':1},{'selected':0,'initialized':0,'healthy_engines':0},
])
def test_partial_stale_or_limited_runtime_is_not_ready(changes):
    rows=deepcopy(samples());rows[-1]['health']['services']['engine'].update(changes)
    assert assess(rows)['readiness']=='NOT_READY'


def test_failed_request_and_missing_heartbeats_are_not_ready():
    assert assess([{'captured_at':1,'error':'offline'},{'captured_at':2,'health':{}}])['readiness']=='NOT_READY'


def test_liquidity_exclusions_are_not_missing_shards():
    rows=samples()
    for row in rows:
        row['health']['services']['engine'].update(universe=782,liquidity_excluded=780)
    assert assess(rows)['readiness']=='READY_FOR_SOAK'
    rows[-1]['health']['services']['engine']['liquidity_excluded']=779
    assert assess(rows)['readiness']=='NOT_READY'


def test_shards_must_agree_on_liquidity_exclusions():
    rows=samples()
    for row in rows:
        engine=row['health']['services']['engine']
        engine.update(universe=782,liquidity_excluded=778,shard_count=2)
        row['health']['services']['engine:1']=deepcopy(engine)
        row['health']['services']['engine:1']['shard_index']=1
    assert assess(rows)['readiness']=='READY_FOR_SOAK'
    rows[-1]['health']['services']['engine:1']['liquidity_excluded']=779
    assert assess(rows)['readiness']=='NOT_READY'
