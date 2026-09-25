"""Stateless downstream checks using recorder v3 request results as supplied inputs.

No warmup or reconstructed FSM is used. This does not verify native requests,
stateful scores, alerts or the initial state of the chart.
"""
from backend.engine.interpreter import Execution, qualified
from backend.engine.runtime import PINE_HASH, ENGINE_VERSION
from backend.engine.syntax import load_program
from backend.engine.values import decode, encode
from .catalog import METRICS
from .replay import finite_number
from .trace_replay import RecordedRequests

FIELDS = ('volume_24h', 'mtf_trend', 'htf_base', 'btc_shock')
ASSIGNMENTS = {
    'quoteCurrencyUsdLike', 'quoteToUsdRate', 'tvVolumeTypeSupported',
    'tvDollarVolumeKnown', 'volume24hProxy', 'mtfTrendQualityLong',
    'htfBaseQualityLong', 'btcShockMoveScore', 'btcShockRangeScore',
    'btcShockVolumeScore', 'btcShockScore',
}


def source_statements(program):
    statements = [s for s in program.statements if s.kind == 'assign' and s.meta['name'] in ASSIGNMENTS]
    if len(statements) != len(ASSIGNMENTS):
        raise ValueError('Stateless component catalog/source mismatch')
    # Fail closed if the selected expressions acquire history or side effects.
    visited = set()
    def pure(expr):
        if expr.kind == 'history':
            raise ValueError('Component unexpectedly depends on history')
        if expr.kind == 'call':
            name = qualified(expr.args[0])
            if name in program.functions:
                if name not in visited:
                    visited.add(name)
                    for statement in program.functions[name].body:
                        if statement.kind != 'assign' and statement.kind != 'expression':
                            raise ValueError('Component helper is not a pure scalar expression')
                        pure(statement.expr)
            elif name not in ('na', 'nz', 'math.min', 'math.max', 'math.abs'):
                raise ValueError('Unsupported stateless component call: ' + str(name))
        for child in expr.args:
            pure(child)
    for statement in statements:
        pure(statement.expr)
    return statements


def check(session):
    metadata = session['metadata']
    if metadata.get('recorder_revision') != 3:
        raise ValueError('Stateless request components require recorder revision 3')
    if not session['rows']:
        raise ValueError('No observations')
    program = load_program()
    statements = source_statements(program)
    provider = RecordedRequests(metadata)
    ex = Execution(program, metadata['parameters'], 'BYBIT:' + metadata['symbol'],
                   metadata['timeframe'], metadata['tick_size'])
    stats = {name:dict(metric=name, numeric_pairs=0, both_na=0, mismatches=0,
                       error_max=0., relative_error_max=0., examples=[]) for name in FIELDS}
    actual = []
    for row in session['rows']:
        provider.begin(row)  # Validate all recorded request fields, including nulls/arrays.
        inputs = decode({name:value for group in provider.values.values() for name,value in group.items()})
        ex.begin(dict(start=row['bar_start'], end=row['bar_end'], volume=row['volume'],
                      **{key:row['PARITY_'+key] for key in ('open','high','low','close')}),
                 realtime=True, outer=inputs)
        ex.special.update({'syminfo.currency':metadata['quote_currency'],
                           'syminfo.volumetype':metadata['volume_type']})
        ex.execute(statements)
        out = {key:row[key] for key in ('seq','event_time','bar_start')}
        for name in FIELDS:
            value = encode(ex.lookup(METRICS[name][0])); ref = row['PARITY_'+name]
            out['PARITY_'+name] = value
            st = stats[name]
            if ref is None and value is None:
                st['both_na'] += 1
                continue
            if any(v is not None and not finite_number(v) for v in (ref,value)):
                raise ValueError('Nonfinite component comparison')
            difference = None if ref is None or value is None else abs(ref-value)
            if difference is not None:
                st['numeric_pairs'] += 1
                st['error_max'] = max(st['error_max'],difference)
                st['relative_error_max'] = max(st['relative_error_max'],difference/max(abs(ref),1e-12))
            tolerance = max(abs(ref or 0),1e-12)*.0005 if METRICS[name][1]=='indicator' else .05
            if difference is None or difference > tolerance:
                st['mismatches'] += 1
                if len(st['examples']) < 5:
                    st['examples'].append(dict(seq=row['seq'],pine=ref,python=value))
        actual.append(out)
    for st in stats.values():
        st['status'] = 'MISMATCH' if st['mismatches'] else 'MATCH' if st['numeric_pairs'] else 'BOTH_NA'
    return actual, dict(status='DIAGNOSTIC_MISMATCH' if any(s['mismatches'] for s in stats.values()) else 'DIAGNOSTIC_MATCH',
        full_intrabar_status='UNVERIFIED', pine_source_hash=PINE_HASH, engine_version=ENGINE_VERSION,
        compared_updates=len(actual), metrics=list(stats.values()),
        scope='Four pure source expressions evaluated independently on each recorded update using captured request results.',
        limitations=['Request results are supplied inputs; native request calculations are not verified.',
            'No historical warmup, persistent state, FSM, signals or remaining scores are verified.',
            'Missing prefix or gaps remain unreconstructed; stateless checks do not repair the session.'])
