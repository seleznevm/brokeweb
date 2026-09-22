"""Versioned request-boundary observations, not independently verified inputs.

These are the actual values returned to the chart by the pinned Pine requests.
They can isolate downstream chart calculations; they cannot prove that Python
calculates those request expressions correctly from native market data.
"""
import math

MAX_MICRO_INTRABARS = 128
REQUEST_FIELDS = {
    'activity': ['return1h', 'return6h', 'return24h', 'natr1h', 'volume24hQuoteNotional'],
    'currency': ['quoteToUsdRequested'],
    **{f'mtf{i}': [f'mtfC{i}', f'mtfF{i}', f'mtfS{i}'] for i in range(1, 5)},
    'base': ['openSkyPriorHigh', 'htfBasePosition', 'htfBaseCompressionRatio',
             'htfBaseHigherLowFlag', 'htfBaseClose', 'htfBaseEma20', 'htfBaseAgeProxy'],
    'micro': ['microLongStateArr', 'microShortStateArr', 'microOpenArr',
              'microHighArr', 'microLowArr', 'microCloseArr', 'microVolumeArr'],
    'btc': ['btcClose', 'btcEmaFast', 'btcEmaSlow'],
    'shock': ['btcShockMoveAtr', 'btcShockRangeAtr', 'btcShockRvol'],
}

# Evaluated in Pine, so Auto TF routing is captured rather than guessed.
REQUEST_ROUTES = {
    'activity': ('syminfo.tickerid', '"60"'),
    'currency': ('syminfo.currency', '"USD"'),
    **{f'mtf{i}': ('syminfo.tickerid', f'mtfTrendTf{i}') for i in range(1, 5)},
    'base': ('syminfo.tickerid', 'htfBaseTf'),
    'micro': ('syminfo.tickerid', 'effectiveMicroTf'),
    'btc': ('btcSymbol', 'timeframe.period'),
    'shock': ('btcSymbol', 'effectiveBtcShockTf'),
}

ARRAY_HELPERS = '''
f_bwrNumbers(array<float> values) =>
    string result = "["
    if array.size(values) > 0
        for i = 0 to array.size(values) - 1
            result += (i > 0 ? "," : "") + f_bwrNumber(array.get(values, i))
    result + "]"
f_bwrBools(array<bool> values) =>
    string result = "["
    if array.size(values) > 0
        for i = 0 to array.size(values) - 1
            result += (i > 0 ? "," : "") + f_bwrBool(array.get(values, i))
    result + "]"
'''


def pine_array(expressions):
    return '"[" + ' + ' + "," + '.join(expressions) + ' + "]"'


def pine_observation():
    groups = []
    for name, fields in REQUEST_FIELDS.items():
        values = []
        for i, field in enumerate(fields):
            fn = ('f_bwrBools' if i < 2 else 'f_bwrNumbers') if name == 'micro' else 'f_bwrNumber'
            values.append(f'{fn}({field})')
        groups.append(pine_array(values))
    return pine_array(groups)


def validate_routes(routes):
    if not isinstance(routes, dict) or set(routes) != set(REQUEST_ROUTES):
        raise ValueError('Incomplete request routes')
    for route in routes.values():
        if not isinstance(route, list) or len(route) != 2 or any(not isinstance(v, str) or not v for v in route):
            raise ValueError('Invalid request route')


def decode_observation(value):
    if not isinstance(value, list) or len(value) != len(REQUEST_FIELDS):
        raise ValueError('Incomplete request observation')
    result = {}
    for (name, fields), group in zip(REQUEST_FIELDS.items(), value):
        if not isinstance(group, list) or len(group) != len(fields):
            raise ValueError('Invalid request observation width: ' + name)
        if name == 'micro':
            if any(not isinstance(a, list) for a in group) or len({len(a) for a in group}) != 1:
                raise ValueError('Unequal lower-TF array lengths')
            if len(group[0]) > MAX_MICRO_INTRABARS:
                raise ValueError('Lower-TF observation exceeds recorder bound')
            if any(type(v) is not bool for a in group[:2] for v in a):
                raise ValueError('Invalid lower-TF boolean')
            numbers = [v for a in group[2:] for v in a]
        else:
            numbers = group
        if any(v is not None and (type(v) not in (int, float) or not math.isfinite(v)) for v in numbers):
            raise ValueError('Invalid request observation number')
        result[name] = dict(zip(fields, group))
    return result
