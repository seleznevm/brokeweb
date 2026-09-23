"""Offline diagnostic replay of recorder v3 request results and chart updates.

Native warmup is a hypothesis for the initial Pine state, not a TradingView
checkpoint. Even a complete match here cannot establish full intrabar parity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from backend.engine.interpreter import qualified, tf_seconds
from backend.engine.parameters import validate_parameters
from backend.engine.runtime import ENGINE_VERSION, PINE_HASH, SIGNALS, PineEngine
from backend.engine.syntax import load_program
from backend.engine.values import decode, encode
from .catalog import METRICS
from .compare import percentile
from .replay import finite_number, normalize_bar, normalized_contexts, numeric_columns
from .trace import messages, unpack
from .trace_contexts import REQUEST_FIELDS, validate_routes, decode_observation


def request_calls():
    """Bind by original assignment and call identity, never by timeframe alone."""
    fields_to_slot = {tuple(v): k for k, v in REQUEST_FIELDS.items()}
    calls = {}
    for st in load_program().statements:
        e = st.expr
        if not e or e.kind != 'call' or qualified(e.args[0]) not in (
                'request.security', 'request.security_lower_tf', 'request.currency_rate'):
            continue
        fields = tuple(v.strip() for v in st.meta['name'].strip('[]').split(','))
        if fields not in fields_to_slot:
            raise ValueError('Uncatalogued request in pinned source')
        calls[e.uid] = fields_to_slot[fields]
    if len(calls) != len(REQUEST_FIELDS) or set(calls.values()) != set(REQUEST_FIELDS):
        raise ValueError('Request catalog/source mismatch')
    return calls


class RecordedRequests:
    def __init__(self, metadata):
        validate_routes(metadata['request_routes'])
        self.routes = metadata['request_routes']
        self.calls = request_calls()
        self.values = None
        self.used = set()

    def begin(self, row):
        observations = row.get('request_observations')
        if not isinstance(observations, dict) or set(observations) != set(REQUEST_FIELDS):
            raise ValueError('Missing recorded request observations')
        arrays = []
        for slot, fields in REQUEST_FIELDS.items():
            group = observations[slot]
            if not isinstance(group, dict) or set(group) != set(fields):
                raise ValueError('Incomplete recorded request fields: ' + slot)
            arrays.append([group[f] for f in fields])
        self.values = decode_observation(arrays)
        self.used = set()

    def read(self, slot, route):
        if self.values is None:
            raise ValueError('Request provider has no current observation')
        if route != self.routes[slot]:
            raise ValueError('Recorded/calculated request route mismatch: ' + slot)
        if slot in self.used:
            raise ValueError('Request consumed more than once: ' + slot)
        self.used.add(slot)
        # A fresh decoded copy keeps Pine array operations off the reference.
        return decode([self.values[slot][f] for f in REQUEST_FIELDS[slot]])

    def __call__(self, parent, call, name, nodes):
        slot = self.calls.get(call.uid)
        if slot is None or slot == 'currency':
            raise ValueError('Unknown recorded request call')
        return self.read(slot, [parent.eval(nodes[0]), str(parent.eval(nodes[1]))])

    def currency_rate(self, parent, source, target):
        return self.read('currency', [source, target])[0]

    def finish(self):
        if self.used != set(REQUEST_FIELDS):
            raise ValueError('Not all recorded requests were consumed')


class TraceCursor:
    """Commit only on advancement; preserve varip across every received seq."""
    def __init__(self, runtime, origin, timeframe):
        self.runtime = runtime
        self.origin = origin
        self.timeframe = timeframe
        self.step = tf_seconds(timeframe) * 1000
        self.previous = None
        self.commits = 0

    def execute(self, row, provider=None):
        ex = self.runtime
        previous = self.previous
        if row['bar_end'] - row['bar_start'] != self.step or row['bar_start'] - self.origin != row['bar_index'] * self.step:
            raise ValueError('Trace origin/index/timeframe mismatch')
        if previous:
            if row['seq'] != previous['seq'] + 1 or row['event_time'] < previous['event_time']:
                raise ValueError('Discontinuous or unordered trace')
            if row['bar_start'] == previous['bar_start']:
                if row['is_new'] or row['bar_update'] != previous['bar_update'] + 1:
                    raise ValueError('Invalid within-bar update state')
                if previous['confirmed'] and not row['confirmed']:
                    raise ValueError('Cannot reopen a confirmed candle')
            else:
                if not previous['confirmed'] or row['bar_start'] != previous['bar_end']:
                    raise ValueError('Cannot advance past an unclosed or missing candle')
                if not row['is_new'] or row['bar_update'] != 1:
                    raise ValueError('Missing first execution of the next candle')
                ex.commit()
                self.commits += 1
        elif row['seq'] != 1 or row['bar_update'] != 1:
            raise ValueError('Stateful replay requires the first recorded execution')
        if ex.count != row['bar_index']:
            raise ValueError('Warmup runtime count differs from recorded bar_index')
        bar = normalize_bar(dict(start=row['bar_start'], end=row['bar_end'],
                                 received_at=row['event_time'], confirmed=row['confirmed'],
                                 volume=row['volume'],
                                 **{k: row['PARITY_' + k] for k in ('open', 'high', 'low', 'close')}),
                            ex.symbol.split(':')[-1].removesuffix('.P'), self.timeframe)
        if provider:
            provider.begin(row)
            ex.request_provider = provider
        ex.begin(bar, realtime=True)
        # The first recorder execution may be mid-bar. begin() alone would
        # incorrectly mark it new and reset the source's varip accumulators.
        ex.special['barstate.isnew'] = row['is_new']
        ex.execute(ex.program.statements)
        if provider:
            provider.finish()
        self.previous = row
        return ex.scopes[0]


SUPPLIED = {'open', 'high', 'low', 'close', 'return_1h', 'return_6h', 'return_24h', 'natr_1h'}


def compare_updates(rows, actual, tick):
    if not finite_number(tick) or tick <= 0:
        raise ValueError('Invalid comparison tick size')
    if len(rows) != len(actual) or not rows:
        raise ValueError('Expected one Python observation per captured execution')
    for ref, py in zip(rows, actual):
        if any(ref[k] != py[k] for k in ('seq', 'event_time', 'bar_start')):
            raise ValueError('Sequence comparison identity mismatch')
    metrics = []
    for name, (_, kind) in METRICS.items():
        errors, examples = [], []
        mismatches = both_na = 0
        for ref, py in zip(rows, actual):
            a, b = ref['PARITY_' + name], py['PARITY_' + name]
            if any(v is not None and not finite_number(v) for v in (a, b)):
                raise ValueError('Nonfinite or invalid comparison metric: ' + name)
            if a is None and b is None:
                both_na += 1
                continue
            delta = None if a is None or b is None else abs(a - b)
            if delta is not None:
                errors.append(delta)
            tolerance = (0 if kind in ('discrete', 'bool') else tick * 1e-8 if kind == 'ohlc'
                         else tick if kind == 'price' else max(abs(a or 0), 1e-12) * .0005 if kind == 'indicator'
                         else .05)
            if delta is None or delta > tolerance:
                mismatches += 1
                if len(examples) < 5:
                    examples.append(dict(seq=ref['seq'], event_time=ref['event_time'], pine=a, python=b))
        metrics.append(dict(metric=name, role='SUPPLIED_INPUT_CHECK' if name in SUPPLIED else 'DOWNSTREAM_CALCULATION',
                            status='MISMATCH' if mismatches else 'MATCH' if errors else 'BOTH_NA',
                            numeric_pairs=len(errors), both_na=both_na, mismatches=mismatches,
                            error_median=percentile(errors, .5), error_p95=percentile(errors, .95), examples=examples))
    signals = []
    for name in SIGNALS.values():
        tp = fp = fn = tn = 0
        examples = []
        for ref, py in zip(rows, actual):
            a, b = ref['PARITY_' + name], py['PARITY_' + name]
            if any(type(v) is not int or v not in (0, 1) for v in (a, b)):
                raise ValueError('Invalid comparison signal: ' + name)
            tp += a == b == 1
            tn += a == b == 0
            fp += a == 0 and b == 1
            fn += a == 1 and b == 0
            if a != b and len(examples) < 5:
                examples.append(dict(seq=ref['seq'], event_time=ref['event_time'], pine=a, python=b))
        signals.append(dict(signal=name, status='MISMATCH' if fp or fn else 'MATCH' if tp else 'NO_POSITIVE_EVENTS',
                            true_positive=tp, false_positive=fp, false_negative=fn, true_negative=tn, examples=examples))
    return metrics, signals


def check(session, fixture):
    metadata, rows = session['metadata'], session['rows']
    if metadata.get('recorder_revision') != 3:
        raise ValueError('Request replay requires recorder revision 3; old traces have no synchronized request results')
    if not rows or not session['report']['sequence_contiguous'] or session['report']['reported_dropped_updates']:
        raise ValueError('Stateful replay requires a complete sequence starting at 1, without dropped updates')
    if metadata['quote_currency'] != 'USDT' or metadata['volume_type'] != 'base':
        raise ValueError('Native warmup currently supports base volume and USDT quote only')
    p = metadata['parameters']
    if p['useOiDivergence'] or p['useCvdDivergence'] or p['lazyMode'] == 'LazyScalp plot':
        raise ValueError('Enabled external source bindings are not recorded')
    symbol, tf = metadata['symbol'].removesuffix('.P'), metadata['timeframe']
    if fixture.get('symbol') != symbol or str(fixture.get('timeframe')) != tf or fixture.get('tick_size') != metadata['tick_size']:
        raise ValueError('Warmup fixture chart identity mismatch')
    parameters = validate_parameters(fixture.get('parameters', {}))
    if any(parameters[k] != v for k, v in p.items()):
        raise ValueError('Warmup fixture parameters differ from capture')
    bars = [normalize_bar(b, symbol, tf) for b in fixture.get('bars', [])]
    if not bars or bars[0]['start'] != metadata['history_start'] or bars[-1]['end'] != rows[0]['bar_start']:
        raise ValueError('Warmup must cover exactly history_start through the first captured candle')
    if any(not b['confirmed'] for b in bars) or any(a['end'] != b['start'] for a, b in zip(bars, bars[1:])):
        raise ValueError('Warmup has unconfirmed candles or gaps')
    contexts = normalized_contexts(fixture.get('contexts', {}))
    provider = RecordedRequests(metadata)
    for row in rows:
        provider.begin(row)  # Fail before expensive warmup on malformed contexts.
    engine = PineEngine(symbol, tf, metadata['tick_size'], parameters, fixture.get('currency_rates'))
    ex = engine.runtime
    offsets, missing = {}, {}
    for bar in bars:
        # Incremental historical contexts retain their own history in provider;
        # future context bars are never offered to the warmup execution.
        visible = {}
        for key, stream in contexts.items():
            begin = end = offsets.get(key, 0)
            while end < len(stream) and stream[end]['confirmed'] and stream[end]['end'] <= bar['end']:
                end += 1
            visible[key] = stream[max(0, begin - 1):end]
            offsets[key] = end
        engine.contexts = visible
        ex.begin(bar)
        ex.execute(ex.program.statements)
        for key in ex.missing:
            missing[key] = missing.get(key, 0) + 1
        ex.commit()
        engine.chart_bars.append(bar)
        engine.chart_bars = engine.chart_bars[-ex.history_limit:]
    cursor = TraceCursor(ex, metadata['history_start'], tf)
    actual = []
    for row in rows:
        values = cursor.execute(row, provider)
        actual.append(dict(seq=row['seq'], event_time=row['event_time'], bar_start=row['bar_start'],
                           bar_index=ex.count, confirmed=row['confirmed'], is_new=row['is_new'],
                           **numeric_columns({'metrics': encode(values)})))
    metrics, signals = compare_updates(rows, actual, metadata['tick_size'])
    mismatch = any(m['status'] == 'MISMATCH' for m in [*metrics, *signals])
    report = dict(status='DIAGNOSTIC_MISMATCH' if mismatch else 'DIAGNOSTIC_MATCH',
                  parity_status='UNVERIFIED', full_intrabar_status='UNVERIFIED',
                  scope='Chart calculations with captured request results and native historical warmup',
                  engine_version=ENGINE_VERSION, pine_source_hash=PINE_HASH, session_id=session['session_id'],
                  matched_updates=len(actual), warmup_bars=len(bars), warmup_missing_context_bars=missing,
                  realtime_bar_commits=cursor.commits, metrics=metrics, signals=signals,
                  limitations=['Initial persistent and varip state is reconstructed from native history, not verified against TradingView.',
                               'Captured request results bypass native request calculations; supplied inputs are not independent parity evidence.',
                               'Repeated confirmed executions use prior-bar history and retain varip in received sequence order; this replay convention still requires TradingView validation.',
                               'The last candle is not committed; the unsent recorder tail is unknown.'])
    return actual, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--warmup-fixture', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output_dir.exists():
            raise ValueError('Choose a new output directory to preserve evidence')
        sessions, _ = unpack(messages(args.input))
        if len(sessions) != 1:
            raise ValueError('Expected exactly one capture session')
        fixture = json.loads(args.warmup_fixture.read_text(encoding='utf-8-sig'))
        actual, report = check(sessions[0], fixture)
        report.update(input_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
                      warmup_sha256=hashlib.sha256(args.warmup_fixture.read_bytes()).hexdigest(),
                      input_file=str(args.input), warmup_file=str(args.warmup_fixture))
        args.output_dir.mkdir(parents=True, exist_ok=False)
        (args.output_dir / 'python.jsonl').write_text(''.join(json.dumps(r, allow_nan=False) + '\n' for r in actual), encoding='utf-8')
        (args.output_dir / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
        print(json.dumps({k: report[k] for k in ('status', 'parity_status', 'matched_updates')}))
        return 0 if report['status'] == 'DIAGNOSTIC_MATCH' else 1
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps(dict(status='INVALID_INPUT', error=str(exc))))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
