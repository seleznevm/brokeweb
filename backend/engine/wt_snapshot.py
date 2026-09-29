"""Stable, explicit WT API fields backed by the source interpreter values."""
from .values import encode, is_na


def attributes(m, bar_start, index, span, parameters):
    def at(value):
        return None if is_na(value) else bar_start - (index - int(value)) * span
    def get(*names):
        return {name: encode(m.get(name)) for name in names}
    quality = {f'T{i}': {side: encode(m.get(f't{i}Q{side.title()}')) for side in ('LONG', 'SHORT')} for i in range(1, 5)}
    diagnostics = {}
    for side in ('LONG', 'SHORT'):
        s = side.title()
        diagnostics[side] = {
            'T1': dict(pullback_depth_atr=encode(m.get(f't1{s}PullbackDepthAtr')),
                       touch_time=at(index - m['barsFromTouch']) if not is_na(m.get('barsFromTouch')) else None,
                       touch_volume_percentile=encode(m.get('_pbTouchVolPct')),
                       touch_volume_ratio=encode(m.get('_pbTouchVolRatio')),
                       confirmation_volume_percentile=encode(m.get('volumePctRank'))),
            'T2': dict(state='WAIT_RETEST' if m.get(f't2{s}AwaitRetest') else 'WAIT_BREAKOUT',
                       mode=parameters['t2SignalMode'], break_level=encode(m.get(f't2{s}BreakLevel')),
                       break_timestamp=at(m.get(f't2{s}BreakBar')),
                       break_distance_atr=encode(m.get(f't2{s}BreakDistAtrStored')),
                       volume_percentile=encode(m.get(f't2{s}BreakVolPctStored')),
                       volume_ratio=encode(m.get(f't2{s}BreakVolRatioStored')),
                       had_compression=encode(m.get(f't2{s}BreakHadSqueeze')),
                       signal_kind=encode(m.get(f't2SignalKind{s}'))),
            'T3': dict(state={0:'WAIT_SWEEP',1:'WAIT_CHOCH',2:'WAIT_ZONE_RETEST'}.get(m.get(f't3{s}State'),'WAIT_SWEEP'),
                       sweep_level=encode(m.get(f't3{s}SweepLevel')), sweep_timestamp=at(m.get(f't3{s}SweepBar')),
                       choch_timestamp=at(m.get(f't3{s}ChochBar')),
                       zone_type={3:'BREAKER',2:'OB',1:'FVG'}.get(m.get(f't3{s}ZoneType')),
                       zone_low=encode(m.get(f't3{s}ZoneLo')), zone_high=encode(m.get(f't3{s}ZoneHi'))),
            'T4': dict(distance_ema_atr=encode(m.get('t4DistanceEmaAtr')), signal_candle_atr=encode(m.get('barRangeAtr')),
                       climax=encode(m.get('t4Climax')), directional_run=encode(m.get('t4BullRunBars' if side=='LONG' else 't4BearRunBars')),
                       rejection_wick_pct=encode(m.get('_t4UpperWickPct' if side=='LONG' else '_t4LowerWickPct')),
                       exhausted=encode(m.get(f't4{s}Exhausted')))}
    regime = m.get('marketRegime', 'LEGACY').replace(' ↑', '_UP').replace(' ↓', '_DOWN')
    return dict(market_regime=regime, volume_percentile=encode(m.get('volumePctRank')),
                adx_percentile=encode(m.get('adxPctRank')), atr_percentile=encode(m.get('atrPctRank')),
                bb_width_percentile=encode(m.get('bbWidthPctRank')), atr=encode(m.get('atr')),
                atr_pct_of_price=encode(m.get('atrPctOfPrice')), chart_state=1 if m.get('chartBull') else -1 if m.get('chartBear') else 0,
                liquidity_above=encode(m.get('liquidityAbove')), liquidity_below=encode(m.get('liquidityBelow')),
                setup_quality_directional=quality, setup_diagnostics=diagnostics,
                aggregate_setup_q_long=encode(m.get('setupAggregateQLong')), aggregate_setup_q_short=encode(m.get('setupAggregateQShort')),
                quality_components=get(*[name for name in m if name.startswith(('_t1Q', '_t2Q', '_t3Q', '_t4Q'))]))


def subtypes(setups, m, direction, parameters, captured=False):
    kinds = {'T1': 'ADAPTIVE_PULLBACK' if parameters['adaptiveCore'] and parameters['pbAdaptiveLogic'] else 'EMA_PULLBACK',
             'T2': m.get('lastT2Kind' if captured else f't2SignalKind{direction.title()}') or 'BREAKOUT',
             'T3': 'SWEEP>CHOCH>ZONE' if parameters['t3RequireSequence'] else 'ZONE_REACTION', 'T4': 'MOMENTUM'}
    return [kinds[s] for s in setups]
