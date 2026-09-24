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


def test_missing_prefix_allows_only_received_price_check():
    session, warmup = fixture()
    for row in session['rows']: row['seq'] += 1
    session['report'].update(sequence_contiguous=False,received_sequence_contiguous=True)
    _, report = check(session, warmup)
    assert report['status']=='PASS' and report['missing_prefix_updates']==1
    assert report['full_intrabar_status']=='UNVERIFIED'
    session['rows'][-1]['seq']+=1
    with pytest.raises(ValueError,match='discontinuous'): check(session,warmup)


@pytest.mark.parametrize('damage', ['warmup_gap', 'trace_gap', 'unclosed', 'index'])
def test_incomplete_or_misaligned_history_rejected(damage):
    session, warmup = fixture()
    if damage == 'warmup_gap': del warmup[5]
    elif damage == 'trace_gap': session['report']['sequence_contiguous'] = False
    elif damage == 'unclosed': session['rows'][2]['confirmed'] = False
    else: session['rows'][-1]['bar_index'] += 1
    with pytest.raises(ValueError): check(session, warmup)


def test_bounded_ta_matches_without_claiming_origin_parity():
    session,warmup=fixture();step=300000
    template=warmup[0]
    warmup=[dict(template,start=i*step,end=(i+1)*step) for i in range(1,1001)]
    for row in session['rows']:
        row['bar_index']+=981;row['bar_start']+=981*step;row['bar_end']+=981*step;row['event_time']+=981*step
    with pytest.raises(ValueError,match='exactly'):check(session,warmup)
    _,report=check(session,warmup,bounded=True)
    assert report['status']=='DIAGNOSTIC_MATCH'
    assert report['full_intrabar_status']=='UNVERIFIED'
    assert report['warmup_mode']=='bounded_native_diagnostic' and not report['exact_origin_covered']
    session['rows'][-1]['PARITY_ema_fast']+=1
    assert check(session,warmup,bounded=True)[1]['status']=='DIAGNOSTIC_MISMATCH'
    with pytest.raises(ValueError,match='20 times'):check(session,warmup[1:],bounded=True)


def test_bounded_mode_never_upgrades_to_pass_even_with_full_origin():
    session,warmup=fixture()
    report=check(session,warmup,bounded=True)[1]
    assert report['exact_origin_covered'] and report['status']=='DIAGNOSTIC_MATCH'


def test_bounded_ta_reports_unknown_historical_grid_without_rewriting_indices():
    session,warmup=fixture()
    for row in session['rows']:row['bar_index']-=1
    before=deepcopy(session)
    with pytest.raises(ValueError,match='origin/index'):check(session,warmup)
    report=check(session,warmup,bounded=True)[1]
    assert report['status']=='DIAGNOSTIC_MATCH' and not report['origin_grid_matches']
    assert session==before
    session['rows'][1]['bar_index']+=1
    with pytest.raises(ValueError,match='bar_index changed'):check(session,warmup,bounded=True)
