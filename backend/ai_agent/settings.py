"""AI Agent settings – persisted in service_health table under key 'ai-agent-settings'."""
from __future__ import annotations
import os
from backend.models.repository import Repository, now_ms
from backend.models.schema import ServiceHealth

DEFAULTS: dict = {
    'enabled': True,
    'tg_bot_token': os.getenv('AI_AGENT_TG_BOT_TOKEN', ''),
    'primary_ai': 'NVIDIA NIM',
    'primary_api_token': os.getenv('AI_AGENT_PRIMARY_TOKEN', ''),
    'primary_model': 'meta/llama-3.1-70b-instruct',
    'secondary_ai': 'NVIDIA NIM',
    'secondary_api_token': os.getenv('AI_AGENT_SECONDARY_TOKEN', ''),
    'secondary_model': 'meta/llama-3.1-70b-instruct',
    'nim_base_url': 'https://integrate.api.nvidia.com/v1',
    'min_avg_setup': 60.0,
    'max_setups_in_report': 10,
    'allowed_chat_ids': os.getenv('AI_AGENT_ALLOWED_CHAT_IDS', ''),
}

_KEY = 'ai-agent-settings'
_SENSITIVE_FIELDS = ('tg_bot_token', 'primary_api_token', 'secondary_api_token')


def _mask(value: str) -> str:
    """Return a masked version of a token: show first 4 and last 4 chars."""
    if not value or len(value) < 12:
        return '***' if value else ''
    return value[:4] + '…' + value[-4:]


def get(repo: Repository) -> dict:
    with repo.session() as s:
        row = s.get(ServiceHealth, _KEY)
        stored = row.payload if row else {}
    # Merge: stored overrides defaults, but never loses new default keys
    result = {**DEFAULTS, **stored}
    return result


def get_masked(repo: Repository) -> dict:
    """Return settings with sensitive tokens masked for the frontend."""
    raw = get(repo)
    masked = dict(raw)
    for field in _SENSITIVE_FIELDS:
        masked[field] = _mask(str(raw.get(field, '')))
    return masked


def save(repo: Repository, patch: dict) -> dict:
    current = get(repo)
    # If a masked value is sent back unchanged, ignore it (user didn't edit it)
    for field in _SENSITIVE_FIELDS:
        sent = patch.get(field)
        if sent and '…' in sent:
            patch.pop(field, None)
    merged = {**current, **patch}
    with repo.session.begin() as s:
        row = s.get(ServiceHealth, _KEY)
        if row:
            row.payload = merged
            row.updated_at = now_ms()
        else:
            s.add(ServiceHealth(name=_KEY, updated_at=now_ms(), payload=merged))
    return get_masked(repo)
