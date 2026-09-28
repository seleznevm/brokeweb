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
}

_KEY = 'ai-agent-settings'


def get(repo: Repository) -> dict:
    with repo.session() as s:
        row = s.get(ServiceHealth, _KEY)
        stored = row.payload if row else {}
    # Merge: stored overrides defaults, but never loses new default keys
    result = {**DEFAULTS, **stored}
    return result


def save(repo: Repository, patch: dict) -> dict:
    current = get(repo)
    merged = {**current, **patch}
    with repo.session.begin() as s:
        row = s.get(ServiceHealth, _KEY)
        if row:
            row.payload = merged
            row.updated_at = now_ms()
        else:
            s.add(ServiceHealth(name=_KEY, updated_at=now_ms(), payload=merged))
    return merged
