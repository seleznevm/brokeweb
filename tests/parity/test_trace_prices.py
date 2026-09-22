"""Independent closed-form TA expectations, not TradingView parity evidence."""
from copy import deepcopy

import pytest

from tools.pine_reference.trace import PARAMETERS
from tools.pine_reference.trace_prices import check


def fixture():
    step = 300000
    warmup = [dict(start=i*step, end=(i+1)*step, open=10., high=11.,
                   low=9., close=10., volume=1., confirmed=True) for i in range(20)]
    rows = []
    for seq, (index, close, high, low, confirmed) in enumerate([
        (20, 11., 12., 9., False), (20, 12., 13., 9., True),
        (20, 12., 13., 9., True), (21, 13., 14., 12., True),
    ], 1):
        rows.append(dict(seq=seq, bar_index=index, bar_start=index*step,
                         bar_end=(index+1)*step, event_time=22*step,
                         confirmed=confirmed, volume=1., PARITY_open=close,
                         PARITY_high=high, PARITY_low=low, PARITY_close=close))
    for row in rows:
        delta = row['PARITY_close']-10
        row.update(PARITY_atr=(26+delta+2)/14,
                   PARITY_ema_fast=10+delta*2/21,
                   PARITY_ema_slow=10+delta*2/51)
    rows[-1].update(PARITY_atr=((30/14)*13+2)/14,
                    PARITY_ema_fast=(10+4/21)*19/21+13*2/21,
                    PARITY_ema_slow=(10+4/51)*49/51+13*2/51)
    return dict(session_id='synthetic', metadata=dict(symbol='ETHFIUSDT.P', timeframe='5',
                history_start=0, tick_size=.0001,
                parameters={s['name']:s['default'] for s in PARAMETERS}),
                report=dict(sequence_contiguous=True), rows=rows), warmup


def test_duplicate_closes_commit_once_and_same_timestamp_updates_survive():
    session, warmup = fixture()
    actual, report = check(session, warmup)
    assert report['status'] == 'PASS'
    assert report['full_intrabar_status'] == 'UNVERIFIED'
    assert len(actual) == 4
    assert actual[1]['PARITY_atr'] == actual[2]['PARITY_atr']
    assert actual[-1]['PARITY_ema_fast'] == pytest.approx(session['rows'][-1]['PARITY_ema_fast'])


def test_bad_reference_fails_without_changing_inputs():
    session, warmup = fixture()
    session['rows'][-1]['PARITY_ema_fast'] += 1
    original = deepcopy(session)
    _, report = check(session, warmup)
    assert session == original
    assert report['status'] == 'FAIL'
    assert {m['metric'] for m in report['metrics'] if m['status']=='FAIL'} == {'ema_fast'}


@pytest.mark.parametrize('damage', ['warmup_gap', 'trace_gap', 'unclosed', 'index'])
def test_incomplete_or_misaligned_history_rejected(damage):
    session, warmup = fixture()
    if damage == 'warmup_gap': del warmup[5]
    elif damage == 'trace_gap': session['report']['sequence_contiguous'] = False
    elif damage == 'unclosed': session['rows'][2]['confirmed'] = False
    else: session['rows'][-1]['bar_index'] += 1
    with pytest.raises(ValueError): check(session, warmup)
