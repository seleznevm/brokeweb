"""NVIDIA NIM (OpenAI-compatible) LLM client with primary/secondary fallback."""
from __future__ import annotations
import logging
import httpx

log = logging.getLogger(__name__)

NIM_URL = 'https://integrate.api.nvidia.com/v1/chat/completions'


async def call_nim(
    client: httpx.AsyncClient,
    api_token: str,
    model: str,
    messages: list[dict],
    base_url: str = 'https://integrate.api.nvidia.com/v1',
    max_tokens: int = 2048,
    temperature: float = 0.4,
) -> str:
    """Call NVIDIA NIM (OpenAI-compatible) chat completions. Returns assistant content."""
    url = base_url.rstrip('/') + '/chat/completions'
    headers = {'Authorization': f'Bearer {api_token}', 'Content-Type': 'application/json'}
    payload = {
        'model': model,
        'messages': messages,
        'max_tokens': max_tokens,
        'temperature': temperature,
        'stream': False,
    }
    response = await client.post(url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()
    return data['choices'][0]['message']['content']


async def call_with_fallback(
    client: httpx.AsyncClient,
    cfg: dict,
    messages: list[dict],
) -> str:
    """Try primary AI first; on any error fall back to secondary."""
    nim_url = cfg.get('nim_base_url', 'https://integrate.api.nvidia.com/v1')
    try:
        return await call_nim(
            client,
            api_token=cfg['primary_api_token'],
            model=cfg.get('primary_model', 'meta/llama-3.1-70b-instruct'),
            messages=messages,
            base_url=nim_url,
        )
    except Exception as exc:
        log.warning('Primary AI failed (%s), trying secondary.', exc)
        return await call_nim(
            client,
            api_token=cfg['secondary_api_token'],
            model=cfg.get('secondary_model', 'meta/llama-3.1-70b-instruct'),
            messages=messages,
            base_url=nim_url,
        )
