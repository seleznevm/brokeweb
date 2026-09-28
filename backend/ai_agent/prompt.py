"""Build an LLM prompt from the current active setups."""
from __future__ import annotations


SYSTEM_PROMPT = """You are a professional crypto scalping analyst.
You receive a JSON list of active market setups from a real-time scanner.
Each setup contains: symbol, timeframe, direction, action, price, avg_setup score (0-100),
SL (stop-loss), T1 (first take-profit), RR (risk/reward ratio) and other quality metrics.

Your job when the trader asks /ai_now:
1. Filter to the highest-quality setups (highest avg_setup, good RR ≥ 1.5, clear direction).
2. For each selected setup output clear entry advice with SL, TP1, TP2, TP3.
   Derive TP2 = entry ± (T1 - SL) * 2, TP3 = entry ± (T1 - SL) * 3 if not present.
3. Keep it concise. Use Telegram-friendly plain text. No markdown headers.
4. End with a brief general market summary.

Respond in the same language the user writes. Default to Russian.
"""


def build_prompt(setups: list[dict], min_avg: float = 60.0, limit: int = 10) -> list[dict]:
    """Return a messages list for the LLM."""
    # Filter active setups meeting quality threshold
    candidates = [
        s for s in setups
        if s.get('action') and s.get('action') != 'WAIT SETUP'
        and isinstance(s.get('avg_setup'), (int, float))
        and s['avg_setup'] >= min_avg
    ]
    # Sort by avg_setup desc, then RR desc
    candidates.sort(key=lambda s: (s.get('avg_setup', 0), s.get('rr', 0)), reverse=True)
    top = candidates[:limit]

    if not top:
        return [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': (
                'Сейчас нет активных сетапов с avg_setup ≥ {:.0f}. '
                'Сообщи об этом трейдеру одной строкой.'.format(min_avg)
            )},
        ]

    lines = []
    for s in top:
        def fmt(v, decimals=4):
            if isinstance(v, float):
                return f'{v:.{decimals}f}'
            return str(v) if v is not None else 'n/a'

        lines.append(
            f"symbol={s.get('symbol')} tf={s.get('timeframe')} dir={s.get('direction')} "
            f"action={s.get('action')} price={fmt(s.get('price'))} "
            f"avg_setup={fmt(s.get('avg_setup'),1)} sl={fmt(s.get('sl'))} "
            f"t1={fmt(s.get('t1'))} rr={fmt(s.get('rr'),2)}"
        )

    user_text = (
        f'Active setups ({len(top)} of {len(candidates)} filtered, min avg_setup={min_avg}):\n'
        + '\n'.join(lines)
        + '\n\nProvide entry advice with SL, TP1, TP2, TP3 for each.'
    )

    return [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': user_text},
    ]
