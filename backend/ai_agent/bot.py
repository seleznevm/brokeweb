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


def _get_active_setups(repo: Repository) -> list[dict]:
    with repo.session() as s:
        rows = s.scalars(select(Current)).all()
    return [
        r.payload for r in rows
        if r.payload.get('action') and r.payload['action'] != 'WAIT SETUP'
    ]


async def handle_update(
    client: httpx.AsyncClient,
    token: str,
    update: dict,
    repo: Repository,
    cfg: dict,
):
    msg = update.get('message') or update.get('edited_message') or {}
    text = (msg.get('text') or '').strip()
    chat_id = msg.get('chat', {}).get('id')
    if not chat_id or not text:
        return

    cmd = text.split()[0].lower().split('@')[0]

    if cmd == '/ai_now':
        await tg_post(client, token, 'sendMessage',
                      chat_id=chat_id,
                      text='⏳ Анализирую активные сетапы…')
        try:
            setups = _get_active_setups(repo)
            min_avg = float(cfg.get('min_avg_setup', 60))
            max_s = int(cfg.get('max_setups_in_report', 10))
            messages = build_prompt(setups, min_avg=min_avg, limit=max_s)
            answer = await call_with_fallback(client, cfg, messages)
            # Telegram max message length = 4096; split if needed
            for i in range(0, len(answer), 4000):
                await tg_post(client, token, 'sendMessage',
                              chat_id=chat_id,
                              text=answer[i:i + 4000])
        except Exception as exc:
            log.exception('AI agent error')
            await tg_post(client, token, 'sendMessage',
                          chat_id=chat_id,
                          text=f'❌ Ошибка AI агента: {exc}')

    elif cmd == '/start' or cmd == '/help':
        await tg_post(client, token, 'sendMessage',
                      chat_id=chat_id,
                      text=(
                          '🤖 AI Agent · Scalping SMA\n\n'
                          '/ai_now — анализ текущих активных сетапов '
                          'с рекомендациями по входу, SL, TP1, TP2, TP3\n'
                          '/help — справка'
                      ))


async def poll_loop(repo: Repository):
    """Main loop: reload settings on every cycle so UI changes apply without restart."""
    offset = 0

    async with httpx.AsyncClient() as client:
        while True:
            cfg = cfg_store.get(repo)
            if not cfg.get('enabled', True):
                repo.heartbeat('ai-agent', {'status': 'DISABLED', 'telegram': 'off'})
                await asyncio.sleep(5)
                continue

            token = cfg.get('tg_bot_token', '').strip()
            if not token:
                repo.heartbeat('ai-agent', {'status': 'NO_TOKEN', 'telegram': 'unconfigured'})
                await asyncio.sleep(10)
                continue

            try:
                data = await tg_get(
                    client, token, 'getUpdates',
                    offset=offset,
                    timeout=POLL_TIMEOUT,
                    allowed_updates='["message"]',
                )
                updates = data.get('result', [])
                repo.heartbeat('ai-agent', {
                    'status': 'HEALTHY',
                    'telegram': 'polling',
                    'pending_updates': len(updates),
                })
                for upd in updates:
                    offset = upd['update_id'] + 1
                    asyncio.create_task(
                        handle_update(client, token, upd, repo, cfg)
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning('Telegram poll error: %s', exc)
                repo.heartbeat('ai-agent', {'status': 'DEGRADED', 'error': str(exc)})
                await asyncio.sleep(5)


async def main():
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
    repo = Repository()
    repo.initialize()
    log.info('AI Agent started')
    await poll_loop(repo)


if __name__ == '__main__':
    asyncio.run(main())
