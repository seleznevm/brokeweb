"""BROKE-PB: Strategy working from support and resistance levels with virtual position manager.

Entry:
- LONG: upper boundary of support (support_top) when 30m direction is LONG
- SHORT: lower boundary of resistance (resistance_bottom) when 30m direction is SHORT
- Virtual position size: nominal USDT configurable in settings (default 500 USDT)

Position Management:
- Structural SL
- TP1 (take 50% + move runner SL to BE)
- Runner Target (close remaining 50%)
- 30m Bias Flip exit
- Degraded recovery exit @ BE
- MAE Risk exit / Reduce
- Exhaustion profit-taking
"""
from __future__ import annotations
import math
import uuid
import logging
from sqlalchemy import select, and_
from backend.models.schema import BrokePBPosition, Delivery
from backend.alerts.outbox import digest

log = logging.getLogger(__name__)


def _format_price(val: float | None) -> str:
    if val is None or not math.isfinite(val):
        return 'n/a'
    if abs(val) >= 100:
        return f'{val:.2f}'
    if abs(val) >= 1:
        return f'{val:.4f}'
    return f'{val:.6f}'


def format_pb_setup_message(pos: BrokePBPosition, snapshot: dict) -> str:
    direction = pos.direction
    sym = pos.symbol
    entry = _format_price(pos.entry_price)
    sl = _format_price(pos.sl)
    tp1 = _format_price(pos.tp1)
    runner = _format_price(pos.runner)
    nominal = f'{pos.nominal_usdt:.0f}'
    avg_setup = snapshot.get('avg_setup', 'n/a')
    mae = snapshot.get('mae', 'n/a')

    level_type = 'ВЕРХНЯЯ ГРАНИЦА ПОДДЕРЖКИ' if direction == 'LONG' else 'НИЖНЯЯ ГРАНИЦА СОПРОТИВЛЕНИЯ'
    emoji = '🟢' if direction == 'LONG' else '🔴'

    return (
        f"{emoji} BROKE-PB {direction} SETUP\n"
        f"Монета: #{sym} | TF: 30m\n"
        f"Уровень: {level_type}\n"
        f"Виртуальный вход: {entry} USDT\n"
        f"Номинал позиции: {nominal} USDT\n"
        f"Structural SL: {sl}\n"
        f"TP1: {tp1} (50% закрытие + перенос в BE)\n"
        f"Runner Target: {runner}\n"
        f"AVG SETUP: {avg_setup} | MAE Risk: {mae}%\n"
        f"30m Bias: {direction}"
    )


def format_pm_message(action: str, pos: BrokePBPosition, snapshot: dict, detail: str = '') -> str:
    sym = pos.symbol
    direction = pos.direction
    entry = _format_price(pos.entry_price)
    curr_price = _format_price(snapshot.get('price'))
    pnl_str = ''
    if pos.pnl_usdt is not None:
        sign = '+' if pos.pnl_usdt >= 0 else ''
        pct_sign = '+' if (pos.pnl_pct or 0) >= 0 else ''
        pnl_str = f"\nPnL: {sign}{pos.pnl_usdt:.2f} USDT ({pct_sign}{pos.pnl_pct or 0:.2f}%)"

    emoji = '⚙️'
    if 'EXIT' in action or 'SL' in action:
        emoji = '🛑' if (pos.pnl_usdt or 0) < 0 else '⏹'
    elif 'TP1' in action or 'TAKE' in action or 'RUNNER' in action:
        emoji = '🎯'
    elif 'REDUCE' in action:
        emoji = '⚠️'

    return (
        f"{emoji} BROKE-PB POSITION MANAGER\n"
        f"Действие: {action}\n"
        f"Монета: #{sym} | Позиция: {direction}\n"
        f"Вход: {entry} | Текущая цена: {curr_price}\n"
        f"Статус: {pos.status}{pnl_str}\n"
        f"{detail}".strip()
    )


def _enqueue_delivery(session, text: str, token: str | None, chat_id: str | None, topic_id: str | None, now_ms: int):
    token = (token or '').strip()
    chat_id = (chat_id or '').strip()
    topic_id = (topic_id or '').strip()
    if not token or not chat_id:
        return

    thread_id = int(topic_id) if topic_id.lstrip('-').isdigit() else None
    dedupe = digest(['broke_pb', str(uuid.uuid4()), now_ms])
    payload = {
        'text': text,
        'telegram_bot_token': token,
        'telegram_chat_id': chat_id,
        'telegram_topic_id': topic_id,
        'message_thread_id': thread_id,
    }
    session.add(Delivery(
        dedupe_key=dedupe,
        rule_id='broke-pb',
        rule_version=1,
        created_at=now_ms,
        updated_at=now_ms,
        next_attempt=now_ms,
        status='pending',
        payload=payload
    ))


def process_broke_pb_snapshot(session, snapshot: dict, settings: dict, now_ms: int):
    """Evaluates BROKE-PB setups and open position management on each snapshot."""
    sym = snapshot.get('symbol')
    if not sym or snapshot.get('timeframe') != '30':
        return

    # Check for active open position on this symbol
    pos = session.scalar(
        select(BrokePBPosition)
        .where(BrokePBPosition.symbol == sym, BrokePBPosition.status == 'OPEN')
        .with_for_update()
    )

    bar = snapshot.get('bar', {})
    price = snapshot.get('price') or bar.get('close')
    if price is None:
        return
    high = bar.get('high', price)
    low = bar.get('low', price)
    close = bar.get('close', price)
    atr = snapshot.get('atr') or (price * 0.015)
    direction_30m = snapshot.get('direction', 'NONE')

    # Notification settings
    pb_token = settings.get('broke_pb_telegram_bot_token') or settings.get('telegram_bot_token')
    pb_chat = settings.get('broke_pb_telegram_chat_id') or settings.get('telegram_chat_id')
    pb_topic = settings.get('broke_pb_telegram_topic_id')

    pm_enabled = settings.get('broke_pb_pm_telegram_enabled', True)
    pm_token = settings.get('broke_pb_pm_telegram_bot_token') or settings.get('telegram_bot_token')
    pm_chat = settings.get('broke_pb_pm_telegram_chat_id') or settings.get('telegram_chat_id')
    pm_topic = settings.get('broke_pb_pm_telegram_topic_id')

    if pos is not None:
        # -------------------------------------------------------------
        # 1. POSITION MANAGEMENT FOR ACTIVE OPEN POSITION
        # -------------------------------------------------------------
        is_long = pos.direction == 'LONG'

        # Check adverse excursion for underwater tracking
        adverse_atr = ((pos.entry_price - low) if is_long else (high - pos.entry_price)) / max(atr, 1e-6)
        if adverse_atr >= 0.25:
            pos.underwater = True

        # Check TP1
        tp1_condition = (high >= pos.tp1) if is_long else (low <= pos.tp1)
        if tp1_condition and not pos.tp1_hit:
            pos.tp1_hit = True
            pos.runner_be = pos.entry_price * (1.0005 if is_long else 0.9995)
            pos.updated_at = now_ms
            # 50% partial profit realized
            half_nominal = pos.nominal_usdt * 0.5
            pnl_half = half_nominal * ((pos.tp1 - pos.entry_price) / pos.entry_price if is_long else (pos.entry_price - pos.tp1) / pos.entry_price)
            pos.payload['realized_tp1_usdt'] = pnl_half
            if pm_enabled:
                msg = format_pm_message(
                    "TAKE 50% + MOVE RUNNER SL -> BE",
                    pos, snapshot,
                    f"TP1 {_format_price(pos.tp1)} достигнут! Фиксация 50% прибыли (+{pnl_half:.2f} USDT). Runner SL перенесён в BE ({_format_price(pos.runner_be)})."
                )
                _enqueue_delivery(session, msg, pm_token, pm_chat, pm_topic, now_ms)

        # Check Runner Target
        runner_condition = pos.runner is not None and ((high >= pos.runner) if is_long else (low <= pos.runner))
        if pos.tp1_hit and runner_condition and not pos.runner_hit:
            pos.runner_hit = True
            pos.status = 'CLOSED'
            pos.close_price = pos.runner
            pos.close_time = now_ms
            pos.close_reason = 'RUNNER_TARGET'
            # Full PnL: first 50% at TP1 + second 50% at runner
            half_nominal = pos.nominal_usdt * 0.5
            pnl_tp1 = pos.payload.get('realized_tp1_usdt', 0.0)
            pnl_runner = half_nominal * ((pos.runner - pos.entry_price) / pos.entry_price if is_long else (pos.entry_price - pos.runner) / pos.entry_price)
            pos.pnl_usdt = pnl_tp1 + pnl_runner
            pos.pnl_pct = (pos.pnl_usdt / pos.nominal_usdt) * 100.0
            pos.updated_at = now_ms
            if pm_enabled:
                msg = format_pm_message(
                    "RUNNER TARGET HIT (FULL EXIT)",
                    pos, snapshot,
                    f"Цель Runner {_format_price(pos.runner)} достигнута! Позиция полностью закрыта с прибылью."
                )
                _enqueue_delivery(session, msg, pm_token, pm_chat, pm_topic, now_ms)
            return

        # Check Stop Loss / BE Stop
        effective_sl = pos.runner_be if pos.tp1_hit else pos.sl
        sl_condition = (low <= effective_sl) if is_long else (high >= effective_sl)
        if sl_condition:
            pos.status = 'CLOSED'
            pos.close_price = effective_sl
            pos.close_time = now_ms
            if pos.tp1_hit:
                pos.close_reason = 'EXIT_RUNNER_BE'
                # Second half closed at BE
                pnl_tp1 = pos.payload.get('realized_tp1_usdt', 0.0)
                half_nominal = pos.nominal_usdt * 0.5
                pnl_be = half_nominal * ((effective_sl - pos.entry_price) / pos.entry_price if is_long else (pos.entry_price - effective_sl) / pos.entry_price)
                pos.pnl_usdt = pnl_tp1 + pnl_be
                pos.pnl_pct = (pos.pnl_usdt / pos.nominal_usdt) * 100.0
            else:
                pos.close_reason = 'STRUCTURAL_SL'
                pos.pnl_usdt = pos.nominal_usdt * ((effective_sl - pos.entry_price) / pos.entry_price if is_long else (pos.entry_price - effective_sl) / pos.entry_price)
                pos.pnl_pct = (pos.pnl_usdt / pos.nominal_usdt) * 100.0
            pos.updated_at = now_ms
            if pm_enabled:
                action_text = "EXIT @ BE (RUNNER STOP)" if pos.tp1_hit else "EXIT / STRUCTURAL SL"
                detail_text = f"Стоп-лосс на уровне {_format_price(effective_sl)} сработал. Позиция закрыта."
                msg = format_pm_message(action_text, pos, snapshot, detail_text)
                _enqueue_delivery(session, msg, pm_token, pm_chat, pm_topic, now_ms)
            return

        # Check 30m Bias Flip
        bias_flip = (direction_30m != 'NONE' and direction_30m != pos.direction)
        if bias_flip:
            pos.status = 'CLOSED'
            pos.close_price = price
            pos.close_time = now_ms
            pos.close_reason = '30M_BIAS_FLIP'
            pnl_tp1 = pos.payload.get('realized_tp1_usdt', 0.0)
            remaining_fraction = 0.5 if pos.tp1_hit else (0.5 if pos.reduced else 1.0)
            nominal_rem = pos.nominal_usdt * remaining_fraction
            pnl_rem = nominal_rem * ((price - pos.entry_price) / pos.entry_price if is_long else (pos.entry_price - price) / pos.entry_price)
            pos.pnl_usdt = pnl_tp1 + pnl_rem
            pos.pnl_pct = (pos.pnl_usdt / pos.nominal_usdt) * 100.0
            pos.updated_at = now_ms
            if pm_enabled:
                msg = format_pm_message(
                    "EXIT / 30m BIAS FLIP",
                    pos, snapshot,
                    f"Направление 30m сменилось на {direction_30m}. Позиция экстренно закрыта по рынку."
                )
                _enqueue_delivery(session, msg, pm_token, pm_chat, pm_topic, now_ms)
            return

        # Check PM Degraded Recovery @ BE
        recovered_to_entry = (close >= pos.entry_price) if is_long else (close <= pos.entry_price)
        mae = float(snapshot.get('mae') or 0.0)
        execution = float(snapshot.get('execution') or 100.0)
        gate_struct = snapshot.get('gates', {}).get('gateStructure', True)
        structure_degraded = (not gate_struct) or (execution <= 60.0) or (mae >= 65.0)

        if pos.underwater and not pos.tp1_hit and recovered_to_entry and structure_degraded:
            pos.status = 'CLOSED'
            pos.close_price = pos.entry_price
            pos.close_time = now_ms
            pos.close_reason = 'EXIT_AT_BE'
            pos.pnl_usdt = 0.0
            pos.pnl_pct = 0.0
            pos.updated_at = now_ms
            if pm_enabled:
                msg = format_pm_message(
                    "EXIT @ BE / DEGRADED RECOVERY",
                    pos, snapshot,
                    f"Цена вернулась к точке входа после просадки, структура/исполнение деградировали. Позиция закрыта в безубыток."
                )
                _enqueue_delivery(session, msg, pm_token, pm_chat, pm_topic, now_ms)
            return

        # Check PM Risk Exit
        if mae >= 80.0:
            pos.status = 'CLOSED'
            pos.close_price = price
            pos.close_time = now_ms
            pos.close_reason = 'PM_RISK_EXIT'
            pos.pnl_usdt = pos.nominal_usdt * ((price - pos.entry_price) / pos.entry_price if is_long else (pos.entry_price - price) / pos.entry_price)
            pos.pnl_pct = (pos.pnl_usdt / pos.nominal_usdt) * 100.0
            pos.updated_at = now_ms
            if pm_enabled:
                msg = format_pm_message(
                    "EXIT / MAE RISK",
                    pos, snapshot,
                    f"MAE Risk превысил 80% ({mae:.1f}%). Риск-менеджер закрыл позицию."
                )
                _enqueue_delivery(session, msg, pm_token, pm_chat, pm_topic, now_ms)
            return

        # Check PM Reduce (50%)
        if not pos.reduced and not pos.tp1_hit and mae >= 65.0 and structure_degraded:
            pos.reduced = True
            pos.updated_at = now_ms
            if pm_enabled:
                msg = format_pm_message(
                    "REDUCE 50% / RISK DEGRADATION",
                    pos, snapshot,
                    f"MAE Risk повышен ({mae:.1f}%), структура ослаблена. Рекомендовано сократить позицию на 50%."
                )
                _enqueue_delivery(session, msg, pm_token, pm_chat, pm_topic, now_ms)

        # Check PM Take Exhaustion
        exh = float(snapshot.get('exhaustion') or 0.0)
        in_profit = (close > pos.entry_price) if is_long else (close < pos.entry_price)
        if in_profit and exh >= 75.0 and not pos.tp1_hit and not pos.exhaustion_taken:
            pos.exhaustion_taken = True
            pos.updated_at = now_ms
            if pm_enabled:
                msg = format_pm_message(
                    "TAKE 50% / EXHAUSTION",
                    pos, snapshot,
                    f"Exhaustion Risk достиг {exh:.1f}% в прибыли. Рекомендовано зафиксировать 50% прибыли."
                )
                _enqueue_delivery(session, msg, pm_token, pm_chat, pm_topic, now_ms)

    else:
        # -------------------------------------------------------------
        # 2. ENTRY EVALUATION (NO CURRENT OPEN POSITION)
        # -------------------------------------------------------------
        nominal = float(settings.get('broke_pb_position_usdt', 500.0))

        if direction_30m == 'LONG':
            support_top = snapshot.get('support_top')
            support_bottom = snapshot.get('support_bottom')
            if support_top is not None and math.isfinite(support_top):
                # Entry condition: low penetrated or touched upper boundary of support
                # and price is not broken far below support
                min_acceptable = (support_bottom - atr * 0.20) if support_bottom is not None else (support_top - atr * 1.5)
                if low <= support_top and close >= min_acceptable:
                    entry_price = support_top
                    sl = (support_bottom - atr * 0.20) if (support_bottom and math.isfinite(support_bottom)) else (entry_price - atr * 1.5)
                    opposing_res = snapshot.get('resistance_bottom')
                    if opposing_res and math.isfinite(opposing_res) and opposing_res > entry_price:
                        tp1 = min(opposing_res - atr * 0.05, entry_price * 1.02)
                    else:
                        tp1 = entry_price * 1.02
                    runner = entry_price + 2.0 * max(tp1 - entry_price, sym_mintick_risk(entry_price))

                    new_pos = BrokePBPosition(
                        symbol=sym,
                        exchange=snapshot.get('exchange', 'BYBIT'),
                        timeframe='30',
                        direction='LONG',
                        status='OPEN',
                        nominal_usdt=nominal,
                        entry_price=entry_price,
                        entry_time=now_ms,
                        bar_start=bar.get('start', now_ms),
                        sl=sl,
                        tp1=tp1,
                        runner=runner,
                        tp1_hit=False,
                        runner_hit=False,
                        underwater=False,
                        reduced=False,
                        exhaustion_taken=False,
                        payload={'snapshot': snapshot, 'metrics': snapshot.get('metrics', {})},
                        updated_at=now_ms
                    )
                    session.add(new_pos)
                    session.flush()

                    # Send PB Setup Telegram Alert
                    setup_msg = format_pb_setup_message(new_pos, snapshot)
                    _enqueue_delivery(session, setup_msg, pb_token, pb_chat, pb_topic, now_ms)

                    # Send PM Entry Notification if PM alerts enabled
                    if pm_enabled:
                        pm_entry_msg = format_pm_message(
                            "ENTRY C1 (VIRTUAL 500 USDT)",
                            new_pos, snapshot,
                            f"Вход от верхней границы поддержки: {_format_price(entry_price)}. SL: {_format_price(sl)} | TP1: {_format_price(tp1)}"
                        )
                        _enqueue_delivery(session, pm_entry_msg, pm_token, pm_chat, pm_topic, now_ms)

        elif direction_30m == 'SHORT':
            resistance_bottom = snapshot.get('resistance_bottom')
            resistance_top = snapshot.get('resistance_top')
            if resistance_bottom is not None and math.isfinite(resistance_bottom):
                # Entry condition: high penetrated or touched lower boundary of resistance
                # and price is not broken far above resistance
                max_acceptable = (resistance_top + atr * 0.20) if resistance_top is not None else (resistance_bottom + atr * 1.5)
                if high >= resistance_bottom and close <= max_acceptable:
                    entry_price = resistance_bottom
                    sl = (resistance_top + atr * 0.20) if (resistance_top and math.isfinite(resistance_top)) else (entry_price + atr * 1.5)
                    opposing_sup = snapshot.get('support_top')
                    if opposing_sup and math.isfinite(opposing_sup) and opposing_sup < entry_price:
                        tp1 = max(opposing_sup + atr * 0.05, entry_price * 0.98)
                    else:
                        tp1 = entry_price * 0.98
                    runner = entry_price - 2.0 * max(entry_price - tp1, sym_mintick_risk(entry_price))

                    new_pos = BrokePBPosition(
                        symbol=sym,
                        exchange=snapshot.get('exchange', 'BYBIT'),
                        timeframe='30',
                        direction='SHORT',
                        status='OPEN',
                        nominal_usdt=nominal,
                        entry_price=entry_price,
                        entry_time=now_ms,
                        bar_start=bar.get('start', now_ms),
                        sl=sl,
                        tp1=tp1,
                        runner=runner,
                        tp1_hit=False,
                        runner_hit=False,
                        underwater=False,
                        reduced=False,
                        exhaustion_taken=False,
                        payload={'snapshot': snapshot, 'metrics': snapshot.get('metrics', {})},
                        updated_at=now_ms
                    )
                    session.add(new_pos)
                    session.flush()

                    # Send PB Setup Telegram Alert
                    setup_msg = format_pb_setup_message(new_pos, snapshot)
                    _enqueue_delivery(session, setup_msg, pb_token, pb_chat, pb_topic, now_ms)

                    # Send PM Entry Notification if PM alerts enabled
                    if pm_enabled:
                        pm_entry_msg = format_pm_message(
                            "ENTRY C1 (VIRTUAL 500 USDT)",
                            new_pos, snapshot,
                            f"Вход от нижней границы сопротивления: {_format_price(entry_price)}. SL: {_format_price(sl)} | TP1: {_format_price(tp1)}"
                        )
                        _enqueue_delivery(session, pm_entry_msg, pm_token, pm_chat, pm_topic, now_ms)


def sym_mintick_risk(price: float) -> float:
    return max(price * 0.005, 0.0001)
