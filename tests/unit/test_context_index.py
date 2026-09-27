"""Indexed requests must retain history, rollback and lower-TF semantics."""
import pytest
from backend.engine.context_bars import ContextBars
from backend.engine.values import encode
from backend.engine.syntax import Program
from tests.unit.test_engine_semantics import candle, provider_engine, run_bar
from tests.unit.test_worker_data import worker_shell


def test_worker_views_are_reused_and_old_view_survives_feed_changes():
    worker=worker_shell();worker.context_limit=2
    key='BYBIT:XUSDT.P|1';other='BYBIT:YUSDT.P|1';btc='BINANCE:BTCUSDT.P|1'
    for stream in (key,other,btc):worker.put_context(stream,candle(0))
    before=worker.context_view('XUSDT')
    assert set(before)=={key,btc}
    assert worker.context_view('XUSDT')[key] is before[key]
    worker.put_context(key,candle(1,close=20,confirmed=False))
    live=worker.context_view('XUSDT')[key]
    worker.put_context(key,candle(1,close=30))
    closed=worker.context_view('XUSDT')[key]
    worker.put_context(key,candle(2,close=40))
    after=worker.context_view('XUSDT')
    assert len(before[key])==1 and live[-1]['close']==20 and closed[-1]['close']==30
    assert [b['start'] for b in after[key]]==[60000,120000]
    assert after[btc] is before[btc]
    # A correction earlier than the current tail also invalidates the index.
    worker.put_context(key,candle(1,close=35))
    assert worker.context_view('XUSDT')[key][0]['close']==35
    assert after[key][0]['close']==30


def test_index_bounds_and_invalid_streams():
    bars=ContextBars([candle(i) for i in range(4)])
    assert list(bars.after_until(None,-1))==[]
    assert list(bars.after_until(60000,60000))==[]
    assert [b['start'] for b in bars.after_until(60000,120000)]==[120000]
    assert list(bars.after_until(180000,60000))==[]
    assert list(ContextBars([]).after_until(None,0))==[]
    for rows in ([candle(1),candle(0)],[candle(0),candle(0)]):
        with pytest.raises(ValueError,match='increasing'):ContextBars(rows)
    with pytest.raises(ValueError,match='interval'):ContextBars([{'start':1,'end':1}])


@pytest.mark.parametrize('lower',[False,True])
def test_indexed_context_matches_linear_traversal_through_restore_and_live_revisions(lower):
    key='BINANCE:BTCUSDT.P|1'
    call='request.security_lower_tf' if lower else 'request.security'
    program=Program(f'[prices, averages] = {call}("BINANCE:BTCUSDT.P", "1", [close, ta.ema(close, 3)])')
    linear=provider_engine(program,{},'5')
    indexed=provider_engine(program,{},'5')
    rows=[candle(i,close=10+i) for i in range(100)]
    # Far future history, a partially closed chart candle, repeated live
    # revisions, confirmation, missing context, and a gap after restore.
    steps=[(candle(0,tf=300000),rows,False),
           (candle(1,tf=300000,confirmed=False,received_at=350000),rows,True),
           (candle(1,tf=300000,confirmed=False,received_at=365000),
            rows[:6]+[candle(6,close=40,confirmed=False,received_at=365000)],True),
           (candle(1,tf=300000,confirmed=False,received_at=375000),
            rows[:6]+[candle(6,close=50,confirmed=False,received_at=375000)],True),
           (candle(1,tf=300000),rows,True),
           (candle(2,tf=300000),[],False),
           (candle(3,tf=300000),rows,False)]
    for position,(bar,context,realtime) in enumerate(steps):
        outputs=[];states=[]
        for mode,(ex,provider,engine) in enumerate((linear,indexed)):
            engine.contexts={key:ContextBars(context) if mode else context}
            outputs.append(encode(run_bar(ex,bar,realtime,commit=bar.get('confirmed',True))))
            states.append((encode(ex.export_state()),provider.export_state(),sorted(ex.missing)))
        assert outputs[0]==outputs[1]
        assert states[0]==states[1]
        if position==4:
            # Index is an ephemeral view; checkpoint format/state is unchanged.
            for ex,provider,_ in (linear,indexed):
                ex.restore_state(ex.export_state());provider.restore_state(provider.export_state())


def test_pinned_checkpoint_continuation_matches_full_and_shortened_context_history():
    from copy import deepcopy
    from backend.engine.runtime import PineEngine
    from backend.engine.interpreter import tf_seconds
    from backend.worker import restored_context_anchor
    from backend.models.checkpoints import pack_checkpoint
    def bar(tf,index,confirmed=True):
        value=candle(index,tf=int(tf_seconds(tf)*1000),close=100+index*.01,confirmed=confirmed)
        value.update(open=value['close']-.02,low=value['close']-1,high=value['close']+1,volume=100)
        return value
    own={f'BYBIT:TESTUSDT.P|{tf}':[bar(tf,i) for i in range(int(3*86400/tf_seconds(tf)))]
         for tf in ('5','15','60','240','D')}
    contexts={**own,**{f'BINANCE:BTCUSDT.P|{tf}':[bar(tf,i) for i in range(int(3*86400/tf_seconds(tf)))] for tf in ('15','30')}}
    original=PineEngine('TESTUSDT','30')
    indexed={k:ContextBars(rows) for k,rows in contexts.items()}
    for i in range(96):original.update(bar('30',i),indexed)
    checkpoint=original.export_state()
    full=PineEngine('TESTUSDT','30');full.restore_state(deepcopy(checkpoint))
    resumed=PineEngine('TESTUSDT','30');resumed.restore_state(deepcopy(checkpoint))
    anchor=restored_context_anchor([resumed],2*86400000,10*86400000)
    assert anchor==86400000
    short={k:ContextBars([b for b in rows if b['start']>=anchor-5*tf_seconds(k.split('|')[1])*1000]) for k,rows in contexts.items()}
    assert sum(map(len,short.values()))<sum(map(len,indexed.values()))
    updates=[bar('30',i) for i in range(96,100)]
    live=bar('30',100,False);live['received_at']=live['start']+60000
    updates.extend([live,{**live,'close':live['close']+.1,'received_at':live['received_at']+1000}])
    for update in updates:
        for engine,streams in ((full,indexed),(resumed,short)):
            snapshot=engine.update(update,streams,not update['confirmed'])
            snapshot.pop('calculation_timestamp');snapshot.pop('calculation_ms')
        assert full.snapshot==resumed.snapshot
        assert pack_checkpoint(full.export_state())==pack_checkpoint(resumed.export_state())
