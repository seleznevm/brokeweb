"""Additional export-only measurements of the pinned detector's data contexts."""

PRELUDE = '''
// Diagnostic requests only. No value below feeds detector logic or signals.
// The lookahead_on series deliberately reveals historical future data so that
// an export mapping can be identified. NEVER use it for trading or parity PASS.
[parityHourOpenOff, parityHourEndOff, parityHourCloseOff, parityReturnOff] = request.security(
     syminfo.tickerid, "60", [time, time_close, close, (close / close[1] - 1.0) * 100.0],
     gaps = barmerge.gaps_off, lookahead = barmerge.lookahead_off)
[parityHourOpenOn, parityHourEndOn, parityHourCloseOn, parityReturnOn] = request.security(
     syminfo.tickerid, "60", [time, time_close, close, (close / close[1] - 1.0) * 100.0],
     gaps = barmerge.gaps_off, lookahead = barmerge.lookahead_on)
'''

FIELDS = {
    'chart_start': 'time', 'chart_end': 'time_close',
    'realtime': '(barstate.isrealtime ? 1 : 0)',
    'hour_start_off': 'parityHourOpenOff', 'hour_end_off': 'parityHourEndOff',
    'hour_close_off': 'parityHourCloseOff', 'return_1h_off': 'parityReturnOff',
    'hour_start_on': 'parityHourOpenOn', 'hour_end_on': 'parityHourEndOn',
    'hour_close_on': 'parityHourCloseOn', 'return_1h_on': 'parityReturnOn',
    'return_1h_original': 'return1h', 'return_6h_original': 'return6h',
    'return_24h_original': 'return24h', 'natr_1h_original': 'natr1h',
    'volume_24h_quote': 'volume24hQuoteNotional', 'volume_24h_usd': 'volume24hProxy',
    'quote_usd_requested': 'quoteToUsdRequested', 'quote_usd_used': 'quoteToUsdRate',
    'quote_usd_fallback': '(quoteToUsdFallback ? 1 : 0)',
    'volume_type_base': '(syminfo.volumetype == "base" ? 1 : 0)',
    'volume_type_quote': '(syminfo.volumetype == "quote" ? 1 : 0)',
    'mtf_close_1': 'mtfC1', 'mtf_close_2': 'mtfC2', 'mtf_close_3': 'mtfC3', 'mtf_close_4': 'mtfC4',
    'htf_close': 'htfBaseClose', 'htf_ema20': 'htfBaseEma20',
    'htf_position': 'htfBasePosition', 'htf_compression': 'htfBaseCompressionRatio',
    'htf_higher_low': 'htfBaseHigherLowFlag', 'htf_prior_high': 'openSkyPriorHigh',
    'micro_count': 'microIntrabarCount', 'btc_shock_move_atr': 'btcShockMoveAtr',
    'btc_shock_range_atr': 'btcShockRangeAtr', 'btc_shock_rvol': 'btcShockRvol',
    'micro_tf_seconds': 'timeframe.in_seconds(effectiveMicroTf)',
    'shock_tf_seconds': 'timeframe.in_seconds(effectiveBtcShockTf)',
    'mtf_1_seconds': 'timeframe.in_seconds(mtfTrendTf1)',
    'mtf_2_seconds': 'timeframe.in_seconds(mtfTrendTf2)',
    'mtf_3_seconds': 'timeframe.in_seconds(mtfTrendTf3)',
    'mtf_4_seconds': 'timeframe.in_seconds(mtfTrendTf4)',
    'htf_base_seconds': 'timeframe.in_seconds(htfBaseTf)',
    'auto_profile': '(tfProfileMode == "Auto" ? 1 : 0)',
    'use_micro': '(useMicroTf ? 1 : 0)', 'use_btc': '(useBtcContext ? 1 : 0)',
    'probe_revision': '1',
}


def context_source(base):
    plots = '\n'.join(f'plot({expr}, title = "PARITY_CTX_{name}", display = display.data_window)'
                      for name, expr in FIELDS.items())
    metadata = dict(history_start='parityContextHistoryStart', bar_index='bar_index',
                    tick_size='syminfo.mintick', confirmed='(barstate.isconfirmed ? 1 : 0)',
                    volume='volume', timeframe_seconds='timeframe.in_seconds()')
    assert len(FIELDS) + len(metadata) <= 64
    plots += '\n' + '\n'.join(f'plot({expr}, title = "PARITY_META_contexts_{name}", display = display.data_window)'
                              for name, expr in metadata.items())
    return base + PRELUDE + '\nvar int parityContextHistoryStart = time\n' + plots + '\n'
