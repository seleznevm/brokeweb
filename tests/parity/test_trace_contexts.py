"""Synthetic transport validation, never evidence of TradingView parity."""
from copy import deepcopy
import csv
import json

import pytest

from backend.engine.syntax import load_program
from tools.pine_reference.trace import CONTEXT_TRACE_COLUMNS, messages, unpack
from tools.pine_reference.trace_contexts import MAX_MICRO_INTRABARS, REQUEST_FIELDS, REQUEST_ROUTES
from test_trace import batch


def context_batch(batch_id=1, seq=1):
    e = batch(batch_id, seq)
    e.update(recorder_revision=3, quote_currency='USDT', volume_type='base',
             request_routes={k: ['BYBIT:ETHFIUSDT.P', '5'] for k in REQUEST_ROUTES})
    e['request_routes']['currency'] = ['USDT', 'USD']
    observations = [[float(i + 1) for i in range(len(fields))] for fields in REQUEST_FIELDS.values()]
    observations[list(REQUEST_FIELDS).index('currency')] = [None]
    observations[list(REQUEST_FIELDS).index('micro')] = [
        [True, False], [False, True], [1., 2.], [3., 4.], [.5, 1.], [2., 3.], [5., 6.]]
    e['rows'][0].append(observations)
    return e


def test_catalog_covers_exact_original_request_assignments():
    actual = []
    for statement in load_program().statements:
        e = statement.expr
        if not e or e.kind != 'call':
            continue
        fn = e.args[0]
        if fn.kind == 'attr' and fn.args[0].kind == 'name' and fn.args[0].value == 'request':
            actual.append([v.strip() for v in statement.meta['name'].strip('[]').split(',')])
    assert actual == list(REQUEST_FIELDS.values())


def test_context_csv_roundtrip_preserves_sequence_arrays_and_null_currency(tmp_path):
    a, b = context_batch(), context_batch(2, 2)
    b['rows'][0][1] = a['rows'][0][1]  # Equal timestamp, different request observation.
    b['rows'][0][-1][0][0] = -7.5
    before = deepcopy([a, b])
    path = tmp_path / 'capture.csv'
    with path.open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Message'])
        for e in (b, a):
            writer.writerow([json.dumps(e)])
    s = unpack(messages(path))[0][0]
    assert [a, b] == before
    assert s['report']['same_timestamp_updates'] == 1
    assert s['report']['request_boundary_updates'] == 2
    assert s['report']['parity_status'] == 'UNVERIFIED'
    assert not s['report']['comparison_ready']
    r0, r1 = [r['request_observations'] for r in s['rows']]
    assert r0['currency']['quoteToUsdRequested'] is None
    assert r0['micro']['microLongStateArr'] == [True, False]
    assert r1['activity']['return1h'] == -7.5
    assert s['metadata']['request_routes'] == a['request_routes']
    assert len(a['rows'][0]) == len(CONTEXT_TRACE_COLUMNS)


@pytest.mark.parametrize('change', [
    lambda e: e.pop('request_routes'),
    lambda e: e['request_routes'].pop('btc'),
    lambda e: e['request_routes'].update(btc=['BINANCE:BTCUSDT.P']),
    lambda e: e.update(volume_type='unknown'),
    lambda e: e.update(quote_currency=''),
    lambda e: e['rows'][0].pop(),
    lambda e: e['rows'][0][-1].pop(),
    lambda e: e['rows'][0][-1][0].pop(),
    lambda e: e['rows'][0][-1][0].__setitem__(0, True),
    lambda e: e['rows'][0][-1][0].__setitem__(0, float('inf')),
    lambda e: e['rows'][0][-1][7][0].__setitem__(0, 1),
    lambda e: e['rows'][0][-1][7][2].pop(),
    lambda e: e['rows'][0][-1].__setitem__(7, [[True] * (MAX_MICRO_INTRABARS + 1)] * 7),
])
def test_incomplete_or_invalid_context_rejected(change):
    e = context_batch()
    change(e)
    with pytest.raises(ValueError):
        unpack([e])


def test_context_metadata_change_cannot_silently_mix_sessions():
    a, b = context_batch(), context_batch(2, 2)
    b['request_routes']['shock'][1] = '15'
    with pytest.raises(ValueError, match='metadata changed'):
        unpack([a, b])


def test_empty_lower_tf_arrays_are_available_observations_not_missing_fields():
    e = context_batch()
    e['rows'][0][-1][7] = [[] for _ in range(7)]
    s = unpack([e])[0][0]
    assert s['rows'][0]['request_observations']['micro']['microCloseArr'] == []


def test_dropped_only_batch_is_accounted_for_without_fabricating_rows():
    a, b = context_batch(), context_batch(2, 2)
    b.update(rows=[], dropped=1)
    s = unpack([a, b])[0][0]
    assert len(s['rows']) == 1
    assert s['report']['reported_dropped_updates'] == 1
    assert not s['report']['sequence_contiguous']
    with pytest.raises(ValueError, match='all recorded updates were dropped'):
        unpack([b])


@pytest.mark.parametrize('revision', [1, 2])
def test_legacy_traces_do_not_gain_synthetic_contexts(revision):
    e = batch()
    e['recorder_revision'] = revision
    s = unpack([e])[0][0]
    assert s['report']['request_boundary_updates'] == 0
    assert 'request_observations' not in s['rows'][0]
