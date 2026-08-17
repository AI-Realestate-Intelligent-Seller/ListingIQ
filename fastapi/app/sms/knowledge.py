"""Approved Bobbie/RE-MAX knowledge lookup — port of bobbie-knowledge.js.

Calls the /rag/search endpoint served by Simulation/outbound.py.
"""

import re

import requests

from ..core.config import settings
from .knowledge_index import bobbie_knowledge

NEEDS_KNOWLEDGE = re.compile(
    r'\b(?:Bobbie|RE/MAX|broker|brokerage|company|office|license|experience|years|buyers?|sellers?'
    r'|sell(?:ing|s)?|sold|commission|fees?|rate|sales|listings?|process|services?'
    r'|areas?|serve|cover|market|team|agents?|credentials?|qualified|who)\b',
    re.I,
)


def needs_bobbie_knowledge(text: str = '') -> bool:
    """Whether the owner's message warrants an approved-knowledge lookup.

    Any direct question qualifies: retrieval is local and cheap now, and an
    ungrounded answer to a question about Bobbie is exactly the failure the
    policy layer exists to prevent.
    """
    value = str(text or '')
    return bool(NEEDS_KNOWLEDGE.search(value) or '?' in value)


def search_bobbie_knowledge(query: str, limit: int = 4) -> dict:
    """Search approved knowledge in-process, unless BOBBIE_RAG_URL delegates it."""
    if not settings['sms']['rag_url']:
        return bobbie_knowledge.search(query, limit)

    response = requests.post(
        settings['sms']['rag_url'],
        json={'query': str(query or '')[:500], 'limit': limit},
        timeout=10,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not response.ok:
        raise RuntimeError(payload.get('error') or f'Bobbie knowledge service returned HTTP {response.status_code}')
    return payload
