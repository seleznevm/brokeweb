"""Telegram bot for the AI Agent – long-polls for /ai_now commands."""
from __future__ import annotations
import asyncio
import logging
import os
import httpx
from sqlalchemy import select
from backend.models.repository import Repository, now_ms
from backend.models.schema import Current
from backend.ai_agent import settings as cfg_store
from backend.ai_agent.llm import call_with_fallback
from backend.ai_agent.prompt import build_prompt

log = logging.getLogger(__name__)

TG_API = 'https://api.telegram.org'
POLL_TIMEOUT = 30  # long-poll seconds
# Limit concurrent LLM requests to avoid exhausting API quotas
_SEMAPHORE = asyncio.Semaphore(2)


async def tg_get(client: httpx.AsyncClient, token: str, method: str, **params):
    url = f'{TG_API}/bot{token}/{method}'
    r = await client.get(url, params=params, timeout=POLL_TIMEOUT + 5)
    r.raise_for_status()
    return r.json()


async def tg_post(client: httpx.AsyncClient, token: str, method: str, **body):
    url = f'{TG_API}/bot{token}/{method}'
    r = await client.post(url, json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def _parse_allowed_chat_ids(raw: str) -> set[int]:
    """Parse comma-separated chat IDs. Empty string → allow all (open mode)."""
    if not raw or not raw.strip():
        return set()
    result = set()
    for part in raw.split(','):
        part = part.strip()
        if part.lstrip('-').isdigit():
            result.add(int(part))
    return result


def _get_all_setups(repo: Repository) -> list[dict]:
    """Return all setup payloads (active + waiting) for the AI agent to analyze."""
    with repo.session() as s:
        rows = s.scalars(select(Current)).all()
    return [r.payload for r in rows]


def _build_market_report(setups: list[dict]) -> str:
    """Build a quick market overview report (no LLM needed)."""
    active = [s for s in setups if s.get('action') and s.get('action') != 'WAIT SETUP']
    waiting = [s for s in setups if s.get('action') == 'WAIT SETUP']

    n_long = sum(1 for s in active if s.get('direction') == 'LONG')
    n_short = sum(1 for s in active if s.get('direction') == 'SHORT')

    # BTC shock average
    btc_vals = [s.get('btc_shock') for s in active if isinstance(s.get('btc_shock'), (int, float))]
    avg_btc = sum(btc_vals) / len(btc_vals) if btc_vals else None

    # Top 5 by avg_setup
    ranked = sorted(active, key=lambda s: s.get('avg_setup') or 0, reverse=True)[:5]

    # Clean setups (no blockers, RR >= 1.5)
    clean = [s for s in active
             if not s.get('blockers')
             and isinstance(s.get('rr'), (int, float)) and s['rr'] >= 1.5
             and isinstance(s.get('avg_setup'), (int, float)) and s['avg_setup'] >= 60]

    lines = ['📊 MARKET REPORT\n']
    lines.append(f'Активных сетапов: {len(active)} (LONG: {n_long} / SHORT: {n_short})')
    lines.append(f'В ожидании (WAIT): {len(waiting)}')

    if avg_btc is not None:
        btc_emoji = '🟢' if avg_btc > 0 else '🔴' if avg_btc < -10 else '🟡'
        lines.append(f'BTC Shock (средний): {btc_emoji} {avg_btc:.1f}')

    bias = 'нейтрально'
    if n_long > n_short * 2:
        bias = '📈 сильный LONG bias'
    elif n_long > n_short * 1.3:
        bias = '📈 умеренный LONG bias'
    elif n_short > n_long * 2:
        bias = '📉 сильный SHORT bias'
    elif n_short > n_long * 1.3:
        bias = '📉 умеренный SHORT bias'
    lines.append(f'Направление рынка: {bias}')

    lines.append(f'\nЧистых сетапов (без блокеров, RR≥1.5): {len(clean)}')

    if ranked:
        lines.append('\n🏆 ТОП 5 по avg_setup:')
        for s in ranked:
            blockers = s.get('blockers') or []
            b_str = f' ⚠️ {", ".join(blockers)}' if blockers else ' ✅'
            lines.append(
                f'  {s.get("symbol")} {s.get("timeframe")} '
                f'{s.get("direction")} · avg={s.get("avg_setup", 0):.0f} '
                f'rr={s.get("rr", 0):.1f}{b_str}'
            )

    return '\n'.join(lines)


async def handle_update(
    client: httpx.AsyncClient,
    token: str,
    update: dict,
    repo: Repository,
    cfg: dict,
    allowed_ids: set[int],
):
    msg = update.get('message') or update.get('edited_message') or {}
    text = (msg.get('text') or '').strip()
    chat_id = msg.get('chat', {}).get('id')
    if not chat_id or not text:
        return

    # Auth: if allowed_chat_ids is configured, reject unknown chats
    if allowed_ids and chat_id not in allowed_ids:
        log.warning('Rejected message from unauthorized chat_id=%s', chat_id)
        return

    cmd = text.split()[0].lower().split('@')[0]

    if cmd == '/ai_now':
        async with _SEMAPHORE:
            await tg_post(client, token, 'sendMessage',
                          chat_id=chat_id,
                          text='⏳ Анализирую активные сетапы…')
            try:
                setups = await asyncio.to_thread(_get_all_setups, repo)
                min_avg = float(cfg.get('min_avg_setup', 60))
                max_s = int(cfg.get('max_setups_in_report', 10))
                messages = build_prompt(setups, min_avg=min_avg, limit=max_s)
                answer = await call_with_fallback(client, cfg, messages)
                # Telegram max message length = 4096; split if needed
                for i in range(0, len(answer), 4000):
                    await tg_post(client, token, 'sendMessage',
                                  chat_id=chat_id,
                                  text=answer[i:i + 4000])
            except Exception:
                log.exception('AI agent error')
                await tg_post(client, token, 'sendMessage',
                              chat_id=chat_id,
                              text='❌ Не удалось получить ответ от AI. Попробуйте позже.')

    elif cmd == '/ai_report':
        await tg_post(client, token, 'sendMessage',
                      chat_id=chat_id,
                      text='📊 Готовлю обзор рынка…')
        try:
            setups = await asyncio.to_thread(_get_all_setups, repo)
            report = _build_market_report(setups)
            await tg_post(client, token, 'sendMessage',
                          chat_id=chat_id,
                          text=report)
        except Exception:
            log.exception('AI report error')
            await tg_post(client, token, 'sendMessage',
                          chat_id=chat_id,
                          text='❌ Ошибка при формировании отчёта.')

    elif cmd == '/start' or cmd == '/help':
        await tg_post(client, token, 'sendMessage',
                      chat_id=chat_id,
                      text=(
                          '🤖 AI Agent · Scalping SMA\n\n'
                          '/ai_now — AI-анализ лучших сетапов '
                          'с рекомендациями входа, SL, TP1–TP3\n'
                          '/ai_report — обзор рынка без AI '
                          '(LONG/SHORT баланс, BTC shock, топ сетапы)\n'
                          '/help — справка'
                      ))


async def poll_loop(repo: Repository):
    """Main loop: reload settings on every cycle so UI changes apply without restart."""
    offset = 0

    async with httpx.AsyncClient() as client:
        while True:
            cfg = await asyncio.to_thread(cfg_store.get, repo)
            if not cfg.get('enabled', True):
                await asyncio.to_thread(repo.heartbeat, 'ai-agent', {'status': 'DISABLED', 'telegram': 'off'})
                await asyncio.sleep(5)
                continue

            token = cfg.get('tg_bot_token', '').strip()
            if not token:
                await asyncio.to_thread(repo.heartbeat, 'ai-agent', {'status': 'NO_TOKEN', 'telegram': 'unconfigured'})
                await asyncio.sleep(10)
                continue

            allowed_ids = _parse_allowed_chat_ids(cfg.get('allowed_chat_ids', ''))

            try:
                data = await tg_get(
                    client, token, 'getUpdates',
                    offset=offset,
                    timeout=POLL_TIMEOUT,
                    allowed_updates='["message"]',
                )
                updates = data.get('result', [])
                await asyncio.to_thread(repo.heartbeat, 'ai-agent', {
                    'status': 'HEALTHY',
                    'telegram': 'polling',
                    'pending_updates': len(updates),
                })
                for upd in updates:
                    offset = upd['update_id'] + 1
                    asyncio.create_task(
                        handle_update(client, token, upd, repo, cfg, allowed_ids)
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning('Telegram poll error: %s', exc)
                await asyncio.to_thread(repo.heartbeat, 'ai-agent', {'status': 'DEGRADED', 'error': str(exc)})
                await asyncio.sleep(5)


async def main():
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
    repo = Repository()
    await asyncio.to_thread(repo.initialize)
    log.info('AI Agent started')
    await poll_loop(repo)


if __name__ == '__main__':
    asyncio.run(main())
