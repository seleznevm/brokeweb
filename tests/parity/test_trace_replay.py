"""Synthetic replay tests with analytical expectations, not parity evidence."""
from copy import deepcopy
import json

import pytest

from backend.engine.interpreter import Execution
from backend.engine.syntax import Program
from backend.engine.values import is_na
from tools.pine_reference.trace import TRACE_COLUMNS, unpack
from tools.pine_reference.trace_replay import RecordedRequests, TraceCursor, check, compare_updates, main
from test_trace_contexts import context_batch


def capture():
    e = context_batch()
    e.update(history_start=0, run_start=6000001)
    e['request_routes'] = {
        'activity': ['BYBIT:ETHFIUSDT.P', '60'], 'currency': ['USDT', 'USD'],
        'mtf1': ['BYBIT:ETHFIUSDT.P', '15'], 'mtf2': ['BYBIT:ETHFIUSDT.P', '60'],
        'mtf3': ['BYBIT:ETHFIUSDT.P', '240'], 'mtf4': ['BYBIT:ETHFIUSDT.P', 'D'],
        'base': ['BYBIT:ETHFIUSDT.P', 'D'], 'micro': ['BYBIT:ETHFIUSDT.P', '1'],
        'btc': ['BINANCE:BTCUSDT.P', '5'], 'shock': ['BINANCE:BTCUSDT.P', '1'],
    }
    for name, value in dict(bar_start=6000000, bar_end=6300000, bar_index=20,
                            event_time=6000001, signal_mask=0, PARITY_open=10.,
                            PARITY_high=11., PARITY_low=9., PARITY_close=10.).items():
        e['rows'][0][TRACE_COLUMNS.index(name)] = value
    groups = e['rows'][0][-1]
    groups[0] = [2., 3., 6., 1.2, 100000000.]
    for i in range(2, 6): groups[i] = [10., 9., 8.]
    groups[6] = [100., .8, .3, 1., 10., 9., 1.]
    groups[9] = [1.25, 1., 1.5]
    session = unpack([e])[0][0]
    warmup = dict(symbol='ETHFIUSDT', timeframe='5', tick_size=.0001,
                  bars=[dict(start=i*300000, end=(i+1)*300000, open=10., high=11.,
                             low=9., close=10., volume=1., confirmed=True) for i in range(20)],
                  contexts={})
    return e, session, warmup


def test_full_pinned_detector_consumes_contexts_and_calculates_downstream_values():
    _, session, fixture = capture()
    before = deepcopy(session)
    actual, report = check(session, fixture)
    assert session == before
    row = actual[0]
    assert row['PARITY_atr'] == pytest.approx(2.)
    assert row['PARITY_ema_fast'] == pytest.approx(10.)
    assert row['PARITY_mtf_trend'] == 100.
    assert row['PARITY_htf_base'] == pytest.approx(32 + (1 - .3/.65)*30 + 20 + 10)
    assert row['PARITY_btc_shock'] == pytest.approx(92.8)
    assert row['PARITY_volume_24h'] == 100000000.  # Recorded null rate -> source USDT fallback.
    assert report['status'] == 'DIAGNOSTIC_MISMATCH'  # Deliberately incorrect reference scores.
    assert report['parity_status'] == 'UNVERIFIED'
    assert report['warmup_missing_context_bars']
    assert report['realtime_bar_commits'] == 0


def test_provider_routes_null_arrays_and_fresh_values_cannot_leak_between_updates():
    _, session, _ = capture()
    provider = RecordedRequests(session['metadata'])
    row = session['rows'][0]
    provider.begin(row)
    assert is_na(provider.currency_rate(None, 'USDT', 'USD'))
    with pytest.raises(ValueError, match='more than once'):
        provider.currency_rate(None, 'USDT', 'USD')
    values = provider.read('micro', provider.routes['micro'])
    values[0].clear()
    assert row['request_observations']['micro']['microLongStateArr'] == [True, False]
    provider.begin(row)
    assert provider.read('micro', provider.routes['micro'])[0] == [True, False]
    with pytest.raises(ValueError, match='route mismatch'):
        provider.read('shock', ['BINANCE:BTCUSDT.P', '15'])
    with pytest.raises(ValueError, match='Not all'):
        provider.finish()
    bad = deepcopy(row)
    del bad['request_observations']['activity']
    with pytest.raises(ValueError, match='Missing recorded'):
        provider.begin(bad)


def state_runtime():
    ex = Execution(Program('''
var int ordinary = 0
varip int ticks = 0
if barstate.isnew
    ticks := 0
ordinary += 1
ticks += 1
float prior = close[1]
'''), symbol='BYBIT:ETHFIUSDT.P', timeframe='5')
    ex.begin(dict(start=0, end=300000, open=10., high=10., low=10., close=10., volume=1.))
    ex.execute(ex.program.statements)
    ex.commit()
    return ex


def state_rows():
    rows = []
    for seq, (index, new, confirmed, close, update) in enumerate([
            (1, False, False, 11., 1), (1, False, True, 12., 2),
            (1, False, True, 13., 3), (2, True, False, 14., 1)], 1):
        rows.append(dict(seq=seq, event_time=900000, bar_start=index*300000,
                         bar_end=(index+1)*300000, bar_index=index, bar_update=update,
                         is_new=new, confirmed=confirmed, volume=1., PARITY_open=close,
                         PARITY_high=close, PARITY_low=close, PARITY_close=close))
    return rows


def test_cursor_rolls_back_var_keeps_varip_and_commits_last_close_once():
    ex = state_runtime()
    cursor = TraceCursor(ex, 0, '5')
    observed = []
    for row in state_rows():
        m = cursor.execute(row)
        observed.append((m['ordinary'], m['ticks'], m['prior'], m['bar_index']))
    assert observed == [(2, 2, 10., 1), (2, 3, 10., 1), (2, 4, 10., 1), (3, 1, 13., 2)]
    assert cursor.commits == 1
    assert ex.count == 2  # The last, still-open candle is not committed.


@pytest.mark.parametrize('damage', ['prefix', 'gap', 'timestamp', 'reopen', 'is_new', 'update', 'unclosed', 'index'])
def test_cursor_rejects_ambiguous_state_transitions(damage):
    rows = state_rows()
    if damage == 'prefix': rows = rows[1:]
    elif damage == 'gap': rows[1]['seq'] += 1
    elif damage == 'timestamp': rows[1]['event_time'] -= 1
    elif damage == 'reopen': rows[2]['confirmed'] = False
    elif damage == 'is_new': rows[1]['is_new'] = True
    elif damage == 'update': rows[1]['bar_update'] += 1
    elif damage == 'unclosed': rows[1]['confirmed'] = rows[2]['confirmed'] = False
    elif damage == 'index': rows[0]['bar_index'] += 1
    cursor = TraceCursor(state_runtime(), 0, '5')
    with pytest.raises(ValueError):
        for row in rows: cursor.execute(row)


@pytest.mark.parametrize('damage', ['revision', 'dropped', 'source', 'identity', 'parameters', 'warmup_gap', 'warmup_origin', 'volume_type'])
def test_invalid_replay_inputs_are_rejected_before_comparison(damage):
    _, session, fixture = capture()
    if damage == 'revision': session['metadata']['recorder_revision'] = 2
    elif damage == 'dropped': session['report']['reported_dropped_updates'] = 1
    elif damage == 'source': session['metadata']['parameters']['useOiDivergence'] = True
    elif damage == 'identity': fixture['timeframe'] = '30'
    elif damage == 'parameters': fixture['parameters'] = {'minMove24': 6.}
    elif damage == 'warmup_gap': del fixture['bars'][10]
    elif damage == 'warmup_origin': fixture['bars'] = fixture['bars'][1:]
    elif damage == 'volume_type': session['metadata']['volume_type'] = 'quote'
    with pytest.raises(ValueError): check(session, fixture)


def test_comparison_is_by_sequence_and_reports_positive_and_negative_events():
    _, session, fixture = capture()
    actual, _ = check(session, fixture)
    # Test the comparison layer only; copying Python results is NOT parity evidence.
    ref = deepcopy(actual)
    ref[0]['PARITY_PINE READY LONG'] = 1
    actual[0]['PARITY_LONG ARMED'] = 1
    ref[0]['PARITY_mtf_trend'] = 90.
    metrics, signals = compare_updates(ref, actual, .0001)
    by_name = {s['signal']: s for s in signals}
    assert by_name['PINE READY LONG']['false_negative'] == 1
    assert by_name['LONG ARMED']['false_positive'] == 1
    assert by_name['SHORT ARMED']['status'] == 'NO_POSITIVE_EVENTS'
    assert next(m for m in metrics if m['metric'] == 'mtf_trend')['examples'][0]['seq'] == 1
    assert next(m for m in metrics if m['metric'] == 'return_1h')['role'] == 'SUPPLIED_INPUT_CHECK'
    actual[0]['seq'] += 1
    with pytest.raises(ValueError, match='identity'): compare_updates(ref, actual, .0001)


def test_cli_writes_diagnostic_artifacts_and_refuses_overwrite(tmp_path):
    e, _, fixture = capture()
    trace, warmup, out = tmp_path/'trace.jsonl', tmp_path/'warmup.json', tmp_path/'out'
    trace.write_text(json.dumps(e))
    warmup.write_text(json.dumps(fixture))
    args = ['--input', str(trace), '--warmup-fixture', str(warmup), '--output-dir', str(out)]
    assert main(args) == 1
    report = json.loads((out/'report.json').read_text())
    assert report['parity_status'] == 'UNVERIFIED'
    assert len(report['input_sha256']) == len(report['warmup_sha256']) == 64
    before = {p: p.read_bytes() for p in out.iterdir()}
    assert main(args) == 2
    assert before == {p: p.read_bytes() for p in out.iterdir()}


@pytest.mark.parametrize('field,value', [('mtf_trend', float('nan')), ('mtf_trend', float('inf')), ('LONG ARMED', None)])
def test_comparator_cannot_hide_invalid_numbers_as_matches(field, value):
    _, session, fixture = capture()
    actual, _ = check(session, fixture)
    ref = deepcopy(actual)
    actual[0]['PARITY_' + field] = value
    with pytest.raises(ValueError, match='comparison'):
        compare_updates(ref, actual, .0001)


def test_even_diagnostic_match_does_not_claim_full_parity():
    _, session, fixture = capture()
    actual, _ = check(session, fixture)
    # A deliberately circular fixture tests status plumbing ONLY.
    for ref, py in zip(session['rows'], actual):
        ref.update({k: v for k, v in py.items() if k.startswith('PARITY_')})
    _, report = check(session, fixture)
    assert report['status'] == 'DIAGNOSTIC_MATCH'
    assert report['full_intrabar_status'] == report['parity_status'] == 'UNVERIFIED'
    assert all(s['status'] != 'PASS' for s in report['signals'])
