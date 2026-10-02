"""Level Campaign notification outbox and compatibility entry point."""
from __future__ import annotations
import uuid
import logging
import os
from backend.models.schema import Delivery
from backend.alerts.outbox import digest

log = logging.getLogger(__name__)


def _enqueue_delivery(session, text: str, token: str | None, chat_id: str | None, topic_id: str | None, now_ms: int, metadata: dict | None = None):
    token = (token or os.getenv('TELEGRAM_BOT_TOKEN') or '').strip()
    chat_id = (chat_id or os.getenv('TELEGRAM_CHAT_ID') or '').strip()
    topic_id = (topic_id or '').strip()
    if not token or not chat_id:
        log.warning('broke_pb_notification_not_configured: Telegram bot token or chat is missing')
        return None

    thread_id = int(topic_id) if topic_id.lstrip('-').isdigit() else None
    dedupe = digest(['broke_pb', str(uuid.uuid4()), now_ms])
    payload = {
        **(metadata or {}),
        'text': text,
        'telegram_bot_token': token,
        'telegram_chat_id': chat_id,
        'telegram_topic_id': topic_id,
        'message_thread_id': thread_id,
    }
    delivery = Delivery(
        dedupe_key=dedupe,
        rule_id='broke-pb',
        rule_version=1,
        created_at=now_ms,
        updated_at=now_ms,
        next_attempt=now_ms,
        status='pending',
        payload=payload
    )
    session.add(delivery)
    return delivery


def process_broke_pb_snapshot(session, snapshot: dict, settings: dict, now_ms: int):
    """Compatibility entry point; all execution is owned by Level Campaign."""
    from backend.engine.level_campaign import process_campaign_snapshot
    return process_campaign_snapshot(session, snapshot, settings, now_ms)
