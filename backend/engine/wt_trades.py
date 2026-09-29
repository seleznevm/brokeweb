"""Shared WT plan/research lifecycle. Prices and frozen signal context are JSON values.

OHLC is not a price path: active stop wins ties. If TP1 enables BE in the same
bar and that bar also contains entry, conservative mode banks TP1 then exits BE;
Exclude removes the whole ambiguous trade. Ordered ticks bypass that policy.
"""
from copy import deepcopy
from .wt_lifecycle import finite

TARGETS = ('tp1', 'tp2', 'tp3', 'tp4')
TERMINAL = {'SL_HIT', 'BE_HIT', 'COMPLETED', 'EXCLUDED', 'SUPERSEDED'}


def allocation(weights):
    if len(weights) != 4 or any(not finite(x) or x < 0 for x in weights) or sum(weights) <= 0:
        raise ValueError('Four nonnegative exit weights with a positive sum are required')
    return [x / sum(weights) for x in weights]


def create_trade(snapshot, entry, stop, timestamp, multiples, weights, entry_mode, trade_id=None):
    direction = snapshot['direction']
    sign = 1 if direction == 'LONG' else -1
    if direction not in ('LONG', 'SHORT') or not finite(entry) or not finite(stop) or sign * (entry - stop) <= 0:
        raise ValueError('Stop must be on the risk side of entry')
    risk = abs(entry - stop)
    if len(multiples) != 4 or not 0 < multiples[0] < multiples[1] < multiples[2] < multiples[3]:
        raise ValueError('Targets must increase in R')
    trade = {k: deepcopy(snapshot.get(k)) for k in (
        'exchange', 'symbol', 'timeframe', 'parameter_hash', 'setup_generation_id',
        'setups', 'setup_subtypes', 'setup_quality', 'aggregate_setup_q', 'score',
        'entry_quality', 'action', 'market_regime', 'volume_ratio', 'volume_percentile',
        'adx', 'adx_percentile', 'atr', 'atr_percentile', 'setup_diagnostics', 'risk_usdt')}
    trade.update(id=trade_id or snapshot['setup_generation_id'], direction=direction,
                 signal_timestamp=snapshot.get('plan_bar_start', timestamp), entry_timestamp=timestamp,
                 entry_bar_start=snapshot['bar_start'], entry=entry, initial_sl=stop, managed_sl=stop,
                 risk_r=risk, target_r=list(multiples), allocation=allocation(weights), entry_mode=entry_mode,
                 realized_r=0.0, highest_tp_reached=0, initial_sl_hit=False, be_hit=False,
                 excluded=False, ambiguous=False, state='OPEN', open=True, closed_timestamp=None,
                 mfe_r=0.0, mae_r=0.0, liquidity_target=snapshot.get('liquidity_target'))
    trade['levels'] = {name: {'price': value, 'start': timestamp, 'end': None, 'hit': False}
                       for name, value in [('entry', entry), ('initial_sl', stop),
                                          *[(name, entry + sign * risk * r) for name, r in zip(TARGETS, multiples)]]}
    for name in TARGETS:
        trade[name] = trade['levels'][name]['price']
    if finite(trade['liquidity_target']):
        trade['levels']['liquidity_target'] = dict(price=trade['liquidity_target'], start=timestamp, end=None, hit=False)
    return trade


def finish(trade, state, timestamp):
    trade.update(state=state, open=False, closed_timestamp=timestamp)
    for level in trade['levels'].values():
        if level['end'] is None:
            level['end'] = timestamp


def _hit(level, timestamp):
    level.update(hit=True, end=timestamp)


def _stop(trade, timestamp):
    be = trade['managed_sl'] == trade['entry']
    remainder = max(0, 1 - sum(trade['allocation'][:trade['highest_tp_reached']]))
    trade['realized_r'] += 0 if be else -remainder
    trade['be_hit' if be else 'initial_sl_hit'] = True
    _hit(trade['levels']['be' if be else 'initial_sl'], timestamp)
    finish(trade, 'BE_HIT' if be else 'SL_HIT', timestamp)


def _exclude(trade, timestamp):
    trade.update(excluded=True, realized_r=None)
    finish(trade, 'EXCLUDED', timestamp)


def advance_trade(trade, bar, policy='Stop first (conservative)', move_to_be=True, ticks=None):
    """Mutate one trade once per observation; callers retain state through restarts.

    Explicit ticks are chronological {time, price} observations. Without ticks,
    a signal at bar close is never tested against that candle's earlier extrema.
    """
    if not trade['open']:
        return trade
    if ticks is not None:
        for tick in ticks:
            if not finite(tick.get('price')) or tick['time'] <= max(trade['entry_timestamp'], trade.get('last_tick_time', -1)):
                continue
            trade['last_tick_time'] = tick['time']
            _advance_range(trade, tick['price'], tick['price'], tick['time'], policy, move_to_be, ordered=True)
            if not trade['open']:
                break
        return trade
    if bar['start'] <= trade['entry_bar_start']:
        return trade
    # Repeated kline messages contain the same historical high/low. Only newly
    # observed extremes plus the current price may act after a management change.
    prior = trade.get('last_observation')
    high, low = bar['high'], bar['low']
    if prior and prior['start'] == bar['start']:
        high = high if high > prior['high'] else bar['close']
        low = low if low < prior['low'] else bar['close']
    trade['last_observation'] = {k: bar[k] for k in ('start', 'high', 'low')}
    _advance_range(trade, high, low, bar['start'], policy, move_to_be)
    return trade


def _advance_range(trade, high, low, timestamp, policy, move_to_be, ordered=False):
    sign = 1 if trade['direction'] == 'LONG' else -1
    favorable, adverse = (high, low) if sign == 1 else (low, high)
    trade['mfe_r'] = max(trade['mfe_r'], sign * (favorable - trade['entry']) / trade['risk_r'])
    trade['mae_r'] = max(trade['mae_r'], -sign * (adverse - trade['entry']) / trade['risk_r'])
    stage = trade['highest_tp_reached']
    stop_hit = sign * (adverse - trade['managed_sl']) <= 0
    target_hit = stage < 4 and sign * (favorable - trade[TARGETS[stage]]) >= 0
    if stop_hit and target_hit and not ordered:
        trade['ambiguous'] = True
        if policy == 'Exclude trade':
            _exclude(trade, timestamp)
            return
    if stop_hit:
        _stop(trade, timestamp)
        return
    liq = trade['levels'].get('liquidity_target')
    if liq and liq['end'] is None and sign * (favorable - liq['price']) >= 0:
        _hit(liq, timestamp)
    for i in range(stage, 4):
        if sign * (favorable - trade[TARGETS[i]]) < 0:
            break
        trade['realized_r'] += trade['allocation'][i] * trade['target_r'][i]
        trade['highest_tp_reached'] = i + 1
        _hit(trade['levels'][TARGETS[i]], timestamp)
        if i == 0 and move_to_be:
            trade['managed_sl'] = trade['entry']
            trade['levels']['initial_sl']['end'] = timestamp
            trade['levels']['be'] = dict(price=trade['entry'], start=timestamp, end=None, hit=False)
            trade['state'] = 'BE_ACTIVE'
            if not ordered and sign * (adverse - trade['entry']) <= 0:
                trade['ambiguous'] = True
                if policy == 'Exclude trade':
                    _exclude(trade, timestamp)
                else:
                    _stop(trade, timestamp)
                return
        if i == 3:
            finish(trade, 'COMPLETED', timestamp)


def empty_bucket():
    return dict(closed_trades=0, profitable_trades=0, losing_trades=0, true_be_trades=0,
                total_r=0.0, gross_profit_r=0.0, gross_loss_r=0.0, tp1_hits=0, tp2_hits=0,
                tp3_hits=0, tp4_hits=0, initial_sl_hits=0, be_exits=0, excluded=0)


def count_trade(bucket, trade, sign=1):
    if trade['excluded']:
        bucket['excluded'] += sign
        return
    r = trade['realized_r']
    bucket['closed_trades'] += sign
    bucket['total_r'] += sign * r
    outcome = 'profitable_trades' if r > 1e-10 else 'losing_trades' if r < -1e-10 else 'true_be_trades'
    bucket[outcome] += sign
    bucket['gross_profit_r'] += sign * max(r, 0)
    bucket['gross_loss_r'] += sign * max(-r, 0)
    for i in range(trade['highest_tp_reached']):
        bucket[f'tp{i+1}_hits'] += sign
    bucket['initial_sl_hits'] += sign * int(trade['initial_sl_hit'])
    bucket['be_exits'] += sign * int(trade['be_hit'])


def summarize(bucket, active=False, risk_usdt=None):
    b = dict(bucket)
    n, wins, losses = b['closed_trades'], b['profitable_trades'], b['losing_trades']
    b.update(active=active, wr=100 * wins / n if n else None,
             expectancy_r=b['total_r'] / n if n else None,
             profit_factor=b['gross_profit_r'] / b['gross_loss_r'] if b['gross_loss_r'] > 1e-10 else None,
             profit_factor_infinite=b['gross_profit_r'] > 1e-10 and b['gross_loss_r'] <= 1e-10,
             average_win_r=b['gross_profit_r'] / wins if wins else None,
             average_loss_r=b['gross_loss_r'] / losses if losses else None)
    b['expected_usdt'] = b['expectancy_r'] * risk_usdt if n and finite(risk_usdt) else None
    return b
