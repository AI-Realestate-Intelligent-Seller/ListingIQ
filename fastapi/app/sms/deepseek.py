"""DeepSeek chat-completions client — port of the config()/completion() pair in ai-runtime.js."""

import requests

from ..core.config import settings
from ..logger import get_logger

logger = get_logger(__name__)


class AiUnavailableError(RuntimeError):
    """Raised when the AI provider cannot be reached or is not configured."""


def is_configured() -> bool:
    return bool(settings['ai']['api_key'])


def completion(messages: list, **options) -> dict:
    """Return the assistant message dict from one chat-completions round."""
    cfg = settings['ai']
    api_key = cfg['api_key']
    if not api_key:
        raise AiUnavailableError('DEEPSEEK_API_KEY (or AI_API_KEY) is not configured')

    body = {
        'model': cfg['model'],
        'messages': messages,
        'thinking': {'type': 'disabled'},
        'stream': False,
        **options,
    }
    try:
        response = requests.post(
            f"{cfg['base_url']}/chat/completions",
            json=body,
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            timeout=cfg['timeout_seconds'],
        )
    except requests.RequestException as exc:
        raise AiUnavailableError(f'AI provider unreachable: {exc}') from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not response.ok:
        detail = (payload.get('error') or {}).get('message') or f'AI provider returned HTTP {response.status_code}'
        raise AiUnavailableError(detail)
    choices = payload.get('choices') or [{}]
    return choices[0].get('message') or {}
