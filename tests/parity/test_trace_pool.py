import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from backend.engine.runtime import SIGNALS
from tools.pine_reference.trace_pool import signal_coverage, audit, diagnose, zero_volume_hypothesis
from tools.pine_reference.trace import PARAMETERS


def row(seq,positive):
    return {'seq':seq,'bar_start':1800000,'bar_index':1,**{'PARITY_'+name:int(positive and name=='AVG SETUP >= 70') for name in SIGNALS.values()}}


def test_latched_flags_unknown_prefix_and_gaps_are_not_new_events():
    rows=[row(5,True),row(6,True),row(7,False),row(8,True),row(10,True),row(11,True)]
    before=deepcopy(rows)
    assert signal_coverage(rows)=={'AVG SETUP >= 70':{'positive_updates':5,'observed_rising_edges':1,'positive_after_unknown_prefix_or_gap':2}}
    assert rows==before


def session(symbol,run=1):
    report=dict(session_id=symbol+str(run),symbol=symbol,timeframe='30',recorder_revision=2,rows=2,batches=1,
        closed_bars=0,complete_bars=0,reported_dropped_updates=0,missing_prefix_updates=0,
        received_sequence_contiguous=True,sequence_contiguous=True,active_setup_updates=2,
        event_start_utc='2026-09-24T01:00:00+00:00',event_end_utc='2026-09-24T01:01:00+00:00',
        duration_seconds=60,nondefault_parameters={})
    return {'session_id':report['session_id'],'report':report,'metadata':{'symbol':symbol,'run_start':run,
        'history_start':0,'label':'same-label','recorder_revision':2,'timeframe':'30'},'rows':[row(1,True),row(2,True)]}


def test_pool_keeps_symbols_runs_and_repeated_flags_separate():
    report=audit([session('X'),session('Y'),session('X',2)],1)
    assert report['sessions']==3 and report['symbols']==2 and report['duplicate_batches']==1
    assert report['rows']==6 and report['closed_bars']==0 and report['full_intrabar_status']=='UNVERIFIED'
    assert report['signals']['AVG SETUP >= 70']=={'positive_updates':6,'observed_rising_edges':0,'positive_after_unknown_prefix_or_gap':3,'sessions':3}
    assert all('REQUEST_CONTEXTS_NOT_CAPTURED' in r['stateful_replay_blockers'] for r in report['items'])


@pytest.mark.parametrize('gap', [True,False])
def test_ineligible_trace_never_fetches_or_fabricates_warmup(tmp_path,gap):
    value=session('X');value['report'].update(received_sequence_contiguous=not gap,reported_dropped_updates=0 if gap else 1)
    adapter=SimpleNamespace(backfill=AsyncMock())
    report=asyncio.run(diagnose(value,adapter,tmp_path/'test'))
    assert report['status']=='NOT_ELIGIBLE'
    adapter.backfill.assert_not_awaited()
    assert not (tmp_path/'test').exists()


def test_zero_volume_hypothesis_is_separate_and_preserves_original_inputs():
    step=1800000
    bars=[dict(start=i*step,end=(i+1)*step,open=10.,high=11.,low=9.,close=10.,volume=1.,confirmed=True) for i in range(20)]
    bars[-1].update(volume=0.,high=10.,low=10.)
    value=session('XUSDT.P');value['metadata'].update(tick_size=.01,parameters={s['name']:s['default'] for s in PARAMETERS})
    value['rows']=[dict(seq=1,bar_start=20*step,bar_end=21*step,event_time=20*step+1,confirmed=False,volume=1.,
        PARITY_open=10.,PARITY_high=12.,PARITY_low=9.,PARITY_close=11.,PARITY_atr=29/14,
        PARITY_ema_fast=10+2/21,PARITY_ema_slow=10+2/51)]
    before=deepcopy((value,bars));report=zero_volume_hypothesis(value,bars)
    assert report['status']=='HYPOTHESIS_MATCH' and report['removed_bars']==1
    assert (value,bars)==before
    assert 'no TradingView historical OHLCV proof' in report['scope']


def test_pool_cli_reports_diagnostic_mismatch_as_failure(monkeypatch,tmp_path):
    import tools.pine_reference.trace_pool as module
    monkeypatch.setattr('sys.argv',['trace_pool','--input',str(tmp_path/'input.csv'),'--output-dir',str(tmp_path/'output'),'--native-ta'])
    async def mismatch(args):return {'ta_diagnostic_status_counts':{'DIAGNOSTIC_MATCH':281,'DIAGNOSTIC_MISMATCH':12}}
    monkeypatch.setattr(module,'run',mismatch)
    assert module.main()==1
