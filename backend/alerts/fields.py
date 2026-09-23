"""Alert editor metadata and types from canonical snapshots and pinned Pine."""
from functools import lru_cache

from backend.engine.parameters import input_schema
from backend.engine.runtime import SIGNALS
from backend.engine.syntax import load_program

SCALAR_OPS = ['==', '!=', 'IN', 'NOT IN', 'changed', 'changed_to']
NUMBER_OPS = ['==', '!=', '>', '>=', '<', '<=', 'IN', 'NOT IN', 'BETWEEN', 'changed', 'changed_to', 'crosses_above', 'crosses_below']
LIST_OPS = ['contains', 'contains_any', 'contains_all', 'changed']


def strings(expr):
    values = [expr.value] if expr.kind == 'literal' and isinstance(expr.value, str) else []
    for child in expr.args:
        values.extend(strings(child))
    return values


def finite_strings(expr):
    if expr.kind == 'literal' and isinstance(expr.value, str):
        return [expr.value]
    if expr.kind == 'ternary':
        a, b = finite_strings(expr.args[1]), finite_strings(expr.args[2])
        if a is not None and b is not None:
            return sorted(set(a + b))
    return None


def field(key, kind, options=None, description=''):
    operators = LIST_OPS if kind == 'list' else NUMBER_OPS if kind in ('number', 'integer') else SCALAR_OPS
    if kind == 'text': operators = [*operators, 'contains']
    result = dict(key=key, type=kind, operators=operators, description=description)
    if options is not None: result['options'] = options
    if kind == 'boolean': result['options'] = [True, False]
    return result


@lru_cache(maxsize=1)
def field_catalog():
    program = load_program()
    declarations = {s.meta['name']: s for s in program.statements if s.kind == 'assign'}
    input_specs = {s['name']: s for s in input_schema()}
    extra = {}
    for name, st in declarations.items():
        kind = {'bool': 'boolean', 'int': 'integer', 'float': 'number', 'string': 'text'}.get(st.meta.get('type'))
        if not kind: continue
        options = input_specs.get(name, {}).get('options')
        # A constant initializer is not a closed enum for a mutable string.
        if kind == 'text' and options is None and st.expr.kind == 'ternary':
            options = finite_strings(st.expr)
        if options and kind == 'text': kind = 'enum'
        key = 'metrics.' + name
        extra[key] = field(key, kind, options)
        if name.startswith('gate') and kind == 'boolean':
            extra['gates.'+name] = field('gates.'+name, kind)
    paths = finite_strings(declarations['entryPath'].expr)
    for name in ('entryPath', 'candidateEntryPath', 'riskPath', 'pathMemory', 'setupFsmPath'):
        extra['metrics.'+name] = field('metrics.'+name, 'enum', paths)
    extra['metrics.direction'] = field('metrics.direction', 'integer', [-1, 0, 1])
    extra['metrics.setupFsmState'] = field('metrics.setupFsmState', 'integer', list(range(9)))
    for name in ('riskPathCode',):
        extra['metrics.'+name] = field('metrics.'+name, 'integer', list(range(10)))
    for name in ('open','high','low','close','volume'):
        extra['bar.'+name] = field('bar.'+name, 'number')
    for name in ('start','end','received_at'):
        extra['bar.'+name] = field('bar.'+name, 'integer')
    extra['bar.confirmed'] = field('bar.confirmed', 'boolean')

    canonical = []
    def add(key, kind, options=None, description=''):
        canonical.append(field(key, kind, options, description))
    for name in ('symbol', 'setup_generation_id', 'parameter_set_id', 'engine_version'):
        add(name, 'text')
    add('exchange', 'enum', ['BYBIT'])
    add('timeframe', 'enum', ['1','3','5','15','30','60','120','240','360','720','D','W','M'], 'Строка: 30 = 30 минут, 60 = 1 час, D = день. Расчёт TF должен быть включён в ACTIVE_TIMEFRAMES сервера.')
    add('direction', 'enum', ['LONG', 'SHORT', 'NONE'])
    add('action', 'enum', finite_strings(declarations['actionText'].expr))
    add('setup_state', 'enum', finite_strings(declarations['setupState'].expr))
    add('family', 'enum', finite_strings(declarations['setupFamily'].expr))
    add('fsm', 'integer', list(range(9)), 'Код: 0 NONE, 1 FORMING, 2 WATCH, 3 APPROACH, 4 ARMED, 5 TRIGGERED, 6 RETEST, 7 READY, 8 ACTIVE. Для текста используйте setup_state.')
    for name in ('candidate_path','trigger_path','risk_path','fsm_path'): add(name, 'enum', paths)
    add('btc_regime', 'enum', ['BULL','BEAR','MIXED'])
    add('hard_gates', 'enum', [f'{i}/5' for i in range(6)], 'Текстовая сводка, а не число.')
    add('active_plan_health', 'enum', ['HEALTHY','DEGRADED','REDUCE','EXIT'], 'При отсутствии активного плана значение отсутствует.')
    add('target_freshness', 'enum', ['n/a','CONSUMED / UNRESOLVED','BROKEN/RETEST','FRESH'])
    add('data_health', 'enum', ['HEALTHY','RECOVERING','DEGRADED','STALE','FULL_REALTIME','KLINE_REALTIME'])
    add('data_quality', 'enum', ['FULL_REALTIME','KLINE_REALTIME'])
    add('parity_status', 'enum', ['UNVERIFIED'])
    for name in ('event', 'last_signal'): add(name, 'enum', list(SIGNALS.values()))
    add('signals', 'list', list(SIGNALS.values()), 'Список событий текущего snapshot. contains = одно; contains_any/all = несколько.')
    blockers = set()
    def scan(nodes):
        for st in nodes:
            if st.kind == 'assign' and st.meta['name'] == 'blockerText':
                for value in strings(st.expr): blockers.update(value.split())
            scan(st.body); scan(st.otherwise)
    scan(program.statements)
    add('blockers', 'list', sorted(blockers), 'Токены blockers из snapshot; несколько выбираются через contains_any/all.')
    for name in ('avg_setup','continuation','formation','execution','geometry','context','level','mae','exhaustion','btc_shock'):
        add(name, 'number', description='Шкала 0–100. Число без %, например 70 или 65.5.')
    add('approach', 'integer', description='Число 0–9.')
    for name in ('price','trigger_zone','distance','sl','t1','rr'): add(name, 'number')
    for name in ('setup_age','event_time','bar_start','received_at'): add(name, 'integer')
    for name in ('confirmed','fresh_trigger','addon_gate','in_play'): add(name, 'boolean')
    return dict(fields=canonical, extra_fields=extra)


def field_spec(key):
    catalog = field_catalog()
    return next((s for s in catalog['fields'] if s['key'] == key), catalog['extra_fields'].get(key))
