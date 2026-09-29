"""Build an LLM prompt from the current active setups."""
from __future__ import annotations


SYSTEM_PROMPT = """You are a professional crypto scalping analyst working with a real-time market scanner (Scalping SMA).

You will receive a structured list of active market setups. Each setup contains:
- symbol, timeframe, direction (LONG/SHORT), action, current price
- Quality scores (0-100): avg_setup (composite), formation, execution, geometry, context, level, approach
- Risk metrics: mae, exhaustion, btc_shock, continuation
- Trade levels: SL (stop-loss price), T1 (take-profit 1 price), RR (risk/reward ratio)
- Status: blockers (reasons NOT to enter), signals, candidate_path, trigger_path, fsm state

CRITICAL RULES:
1. If a setup has BLOCKERS — warn the trader. Blockers mean the strategy itself does NOT recommend entry.
2. Calculate risk as: Risk = abs(Price - SL).
3. TP levels for LONG:  TP1 = T1 (given), TP2 = Price + Risk * 2, TP3 = Price + Risk * 3.
4. TP levels for SHORT: TP1 = T1 (given), TP2 = Price - Risk * 2, TP3 = Price - Risk * 3.
5. Only recommend entries where RR ≥ 1.5 and blockers are empty.
6. Consider BTC context: if btc_shock is negative across most setups, warn about adverse market conditions.
7. If multiple setups are in the same direction on correlated assets — note the concentration risk.

FORMAT:
- Telegram plain text, no markdown headers (no # or **)
- One setup per block, separated by blank line
- Each block: symbol, direction, entry zone, SL, TP1, TP2, TP3, key metrics, warnings
- End with a 2-3 sentence market summary (direction bias, how many longs vs shorts, btc_shock reading)

Respond in Russian by default. Be concise but actionable.
"""


def _fmt(v, decimals=4):
    """Format a numeric value for the prompt."""
    if isinstance(v, float):
        return f'{v:.{decimals}f}'
    if isinstance(v, bool):
        return str(v)
    return str(v) if v is not None else 'n/a'


def _score_setup(s: dict) -> float:
    """Composite score: avg_setup weighted by RR, penalized by blockers."""
    avg = s.get('avg_setup') or 0
    rr = s.get('rr') or 0
    blockers = s.get('blockers') or []
    # RR bonus: clamp to [0.5, 3.0] range, multiply
    rr_factor = max(0.5, min(float(rr), 3.0))
    # Blocker penalty: each blocker reduces score by 20%
    blocker_penalty = 0.8 ** len(blockers) if blockers else 1.0
    return float(avg) * rr_factor * blocker_penalty


def build_prompt(setups: list[dict], min_avg: float = 60.0, limit: int = 10) -> list[dict]:
    """Return a messages list for the LLM with full trading context."""
    # Filter active setups meeting quality threshold
    candidates = [
        s for s in setups
        if s.get('action') and s.get('action') != 'WAIT SETUP'
        and isinstance(s.get('avg_setup'), (int, float))
        and s['avg_setup'] >= min_avg
    ]

    # Sort by composite score (quality * RR, penalized by blockers)
    candidates.sort(key=_score_setup, reverse=True)
    top = candidates[:limit]

    # Count market context
    all_active = [s for s in setups if s.get('action') and s.get('action') != 'WAIT SETUP']
    n_long = sum(1 for s in all_active if s.get('direction') == 'LONG')
    n_short = sum(1 for s in all_active if s.get('direction') == 'SHORT')
    avg_btc = None
    btc_vals = [s.get('btc_shock') for s in all_active if isinstance(s.get('btc_shock'), (int, float))]
    if btc_vals:
        avg_btc = sum(btc_vals) / len(btc_vals)

    # Near-trigger setups (WAIT SETUP with high avg_setup)
    near_trigger = [
        s for s in setups
        if s.get('action') == 'WAIT SETUP'
        and isinstance(s.get('avg_setup'), (int, float))
        and s['avg_setup'] >= min_avg
    ]
    near_trigger.sort(key=lambda s: s.get('avg_setup') or 0, reverse=True)
    near_trigger = near_trigger[:5]

    if not top and not near_trigger:
        return [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': (
                f'Сейчас нет активных сетапов с avg_setup ≥ {min_avg:.0f}.\n'
                f'Всего активных: {len(all_active)} (LONG: {n_long}, SHORT: {n_short}).\n'
                f'Средний BTC Shock: {_fmt(avg_btc, 2) if avg_btc is not None else "n/a"}.\n'
                'Сообщи трейдеру краткий статус рынка.'
            )},
        ]

    lines = []
    for s in top:
        blockers = s.get('blockers') or []
        blockers_str = ', '.join(blockers) if blockers else 'NONE'
        signals = s.get('signals') or []
        signals_str = ', '.join(signals) if signals else '-'

        lines.append(
            f"--- {s.get('symbol')} | {s.get('timeframe')} | {s.get('direction')} ---\n"
            f"  action={s.get('action')} price={_fmt(s.get('price'))}\n"
            f"  avg_setup={_fmt(s.get('avg_setup'), 1)} rr={_fmt(s.get('rr'), 2)}\n"
            f"  sl={_fmt(s.get('sl'))} t1={_fmt(s.get('t1'))}\n"
            f"  formation={_fmt(s.get('formation'), 1)} execution={_fmt(s.get('execution'), 1)}\n"
            f"  geometry={_fmt(s.get('geometry'), 1)} context={_fmt(s.get('context'), 1)}\n"
            f"  level={_fmt(s.get('level'), 1)} approach={_fmt(s.get('approach'), 1)}\n"
            f"  mae={_fmt(s.get('mae'), 1)} exhaustion={_fmt(s.get('exhaustion'), 1)}\n"
            f"  btc_shock={_fmt(s.get('btc_shock'), 2)} continuation={_fmt(s.get('continuation'), 1)}\n"
            f"  candidate_path={s.get('candidate_path') or 'n/a'} trigger_path={s.get('trigger_path') or 'n/a'}\n"
            f"  BLOCKERS: {blockers_str}\n"
            f"  signals: {signals_str}"
        )

    near_lines = []
    for s in near_trigger:
        near_lines.append(
            f"  {s.get('symbol')} {s.get('timeframe')} {s.get('direction')} "
            f"avg={_fmt(s.get('avg_setup'), 1)} rr={_fmt(s.get('rr'), 2)}"
        )

    market_ctx = (
        f'\n\nMARKET CONTEXT:\n'
        f'Total active: {len(all_active)} (LONG: {n_long}, SHORT: {n_short})\n'
        f'Avg BTC Shock: {_fmt(avg_btc, 2) if avg_btc is not None else "n/a"}\n'
    )

    near_section = ''
    if near_lines:
        near_section = '\n\nNEAR TRIGGER (WAIT SETUP, high quality — may activate soon):\n' + '\n'.join(near_lines)

    user_text = (
        f'ACTIVE SETUPS ({len(top)} best of {len(candidates)} with avg_setup≥{min_avg}):\n\n'
        + '\n\n'.join(lines)
        + market_ctx
        + near_section
        + '\n\nProvide entry advice with SL, TP1, TP2, TP3 for each clean setup (no blockers, RR≥1.5). '
        'Warn about setups with blockers. Note market direction bias and correlations.'
    )

    return [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': user_text},
    ]
