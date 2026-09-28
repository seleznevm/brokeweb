import pytest
from backend.engine.wt_lifecycle import apply_stop_guard
from backend.wt import present
from backend.engine.wt import WTEngine

def plan(direction='LONG',generation='one'):
    return dict(strategy='WT_SETUPS',signal_source='engine',direction=direction,sl=90 if direction=='LONG' else 110,price=100,
                plan_bar_start=0,bar_start=1800000,setup_generation_id=generation,parameter_hash='p',action='ENTER NOW',signals=['T1','READY TO ENTER'],data_health='HEALTHY',event_time=2000000)

@pytest.mark.parametrize('direction,low,high',[('LONG',90,105),('SHORT',95,110)])
def test_wick_touch_stops_plan_even_after_price_recovers(direction,low,high):
    s=plan(direction)
    hit=apply_stop_guard(s,[dict(start=1800000,low=low,high=high)])
    assert hit['action']=='SL HIT' and hit['pine_action']=='ENTER NOW' and not hit['signals']
    assert apply_stop_guard(s,[],hit)['action']=='SL HIT'
    assert apply_stop_guard(plan(direction,'new'),[],hit)['action']=='ENTER NOW'

def test_no_stop_from_pre_entry_extremes_or_future_bars():
    s=plan()
    assert apply_stop_guard(s,[dict(start=0,low=50),dict(start=3600000,low=50)])['action']=='ENTER NOW'
    s.update(bar_start=0,price=90)
    assert apply_stop_guard(s,[])['action']=='SL HIT'

def test_stale_and_recovering_recommendations_are_not_current_entries():
    s=plan()
    assert present(s,2000000,2000001)['action']=='ENTER NOW'
    assert present(s,2000000,2090001)['action']=='WAIT DATA'
    assert present(s,3000000,3000001)['action']=='WAIT DATA'  # Recent DB write, old candle.
    assert present({**s,'data_health':'RECOVERING'},2000000,2000001)['action']=='WAIT DATA'
    assert present({**s,'action':'SL HIT'},2000000,3000000)['action']=='SL HIT'
    assert present({**s,'signal_source':'tradingview'},2000000,3000000)['action']=='ENTER NOW' # Frozen reference.

def test_stop_memory_survives_checkpoint_and_is_not_mutated_by_later_updates():
    one=WTEngine('TESTUSDT','30',.01)
    one.stopped_plans['one']=apply_stop_guard(plan(),[dict(start=1800000,low=89)])
    state=one.export_state()
    one.stopped_plans['two']={'stop_hit':True}
    two=WTEngine('TESTUSDT','30',.01);two.restore_state(state)
    assert 'two' not in two.stopped_plans
    assert apply_stop_guard(plan(),[],two.stopped_plans['one'])['action']=='SL HIT'

def test_engine_intrabar_stop_stays_closed_after_restart_and_rebound(monkeypatch):
    from backend.engine import wt
    from backend.engine.syntax import Program
    source=Program('lastDir = 1\nlastEntry = 100.0\nlastSL = 90.0\nlastSignalBar = 0\n_signalAge = bar_index\nentryAction = "ENTER NOW"\nreadyToEnterEvent = true\nlastSetupT1 = true')
    wt.input_schema()  # Keep the real parameter schema while isolating lifecycle behavior.
    monkeypatch.setattr(wt,'program',lambda:source)
    one=WTEngine('TESTUSDT','30',.01)
    bar=dict(start=0,end=1800000,open=100,high=105,low=80,close=100,volume=100,confirmed=True)
    assert one.update(bar,{})['action']=='ENTER NOW' # Low preceded the plan.
    bar.update(start=1800000,end=3600000,low=90,confirmed=False)
    hit=one.update(bar,{},True)
    assert hit['action']=='SL HIT' and not hit['signals']
    two=WTEngine('TESTUSDT','30',.01);two.restore_state(one.export_state())
    bar.update(low=99,close=101)
    assert two.update(bar,{},True)['action']=='SL HIT'
