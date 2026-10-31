"""Authorized agent knowledge retrieval.

The model supplies only a query. Ownership and active-version scope always come
from a trusted :class:`AgentRuntimeContext` resolved by server code.
"""

import re

import requests

from ..core.config import settings
from ..vector_store import vector_store
from .classifier import seller_signals
from .knowledge_index import bobbie_knowledge
from .runtime_context import AgentRuntimeContext

NEEDS_KNOWLEDGE = re.compile(
    r'\b(?:broker|brokerage|company|office|license|experience|years|buyers?|sellers?'
    r'|sell(?:ing|s)?|sold|commission|fees?|rate|sales|listings?|process|services?'
    r'|areas?|serve|cover|market|team|agents?|credentials?|qualified|who'
    r'|price|pricing|value|worth|guarantee|negotia\w*|charges?|costs?)\b',
    re.I,
)

# The owner is talking about the price or value of their property. Generic
# "sell"/"sold" wording is left out so questions about the agent's own track
# record ("how many homes have you sold?") still retrieve biography.
SELLER_TOPIC = re.compile(
    r'\b(?:price|pricing|priced|value|valuation|worth|comps?|comparables?|apprais\w*|list price|asking price)\b'
    r'|\$\s?\d|\d\s?\$|\b\d{2,4}k\b',
    re.I,
)
SELLER_TOPIC_EXPANSION = (
    'seller pricing approach; how I assess home value; listing and marketing strategy; '
    'selling quickly; seller consultation, property visit and appointment preferences'
)
# Excerpts that actually speak to that topic. Generic selling words ("sold",
# "homes") are left out on purpose: a sales-count achievement is biography,
# not pricing or listing advice.
SELLER_ADVICE_TERMS = re.compile(
    r'\b(?:pric\w*|valu\w*|worth|apprais\w*|comps?|comparables?|cma|market analysis|market data'
    r'|strateg\w*|marketing|listing (?:plan|process|strategy|approach)|consult\w*|walk-?through'
    r'|visit|appointment|meet(?:ing)?|negotiat\w*|days on market|staging|prepar\w*|timeline|quick(?:ly)?'
    r'|fast|net proceeds|seller(?:s)? (?:guide|process|plan))\b',
    re.I,
)
# The owner asks how the agent works: approach, process, marketing, or what
# they would do differently. Only excerpts about how the agent sells answer it.
APPROACH_TOPIC = re.compile(
    r'\b(?:approach|process|strateg\w*|marketing|differently|different from|game ?plan|your plan'
    r'|what (?:would|will|do) (?:you|u) do|how (?:would|will|do) (?:you|u) (?:sell|market|price|list|handle|get))\b',
    re.I,
)
# No price words here: they would route the query to the seller-pricing filter.
APPROACH_TOPIC_EXPANSION = 'my listing process and marketing approach for sellers; how I work with sellers'
# Excerpts that describe how the agent lists, prices or markets a home. Plain
# licensing or biography text never qualifies, even when it is similar.
APPROACH_TERMS = re.compile(
    r'\b(?:listing (?:plan|process|strategy|approach)|marketing (?:plan|strategy|approach|process)'
    r'|pricing (?:approach|strategy|process)|my (?:process|approach)|our (?:process|approach)'
    r'|seller(?:s)? (?:guide|process|plan|consultation)|comparative market analysis|cma|comparables?|comps'
    r'|walk-?through|net proceeds|days on market|how (?:i|we) (?:market|price|sell|list)'
    r'|(?:i|we) (?:market|advertise|promote)\b[^.]{0,40}\b(?:home|house|property|listing))\b',
    re.I,
)
# The owner asks about the agent's experience or track record, as a question,
# statement or request ("Tell me about your experience selling homes like mine").
EXPERIENCE_TOPIC = re.compile(
    r"\b(?:you|your|u|ur)\b[^.?!]{0,40}\b(?:experience\w*|track record|background|sold|closed|sales history"
    r"|done (?:this|it) before|how many (?:homes?|houses?|properties|deals?))\b"
    r"|\b(?:experience|track record|sales history)\b[^.?!]{0,30}\b(?:selling|sold|with|in|listing|homes?|houses?)\b"
    r"|\b(?:homes?|houses?|properties|condos?|places?)\b[^.?!]{0,30}\b(?:you|u)(?:['\u2019]ve| have)?\s+(?:sold|listed|closed)\b",
    re.I,
)
EXPERIENCE_TOPIC_EXPANSION = ('my experience and track record; residential homes I have sold; '
                              'business and commercial sales are a separate category')
# Excerpts by the kind of experience they describe. Business or commercial
# sales are never evidence of residential home sales, and the reverse.
BUSINESS_SCOPE = re.compile(
    r'\b(?:business(?:es)?(?:[- ]sales?| brokerage)?|laundr\w*|coin laundr\w*|commercial|investment propert\w*'
    r'|retail|restaurants?|salons?|SBA|SDE|NOI|cap rate|business sales?)\b', re.I)
RESIDENTIAL_SCOPE = re.compile(
    r'\b(?:residential|homes?|houses?|single[- ]family|condos?|townhomes?|townhouses?|homeowners?|primary residence)\b',
    re.I)


def experience_scope(text: str = '') -> str:
    """'residential', 'business_or_commercial', 'mixed' or 'general' for one excerpt."""
    value = str(text or '')
    business, residential = bool(BUSINESS_SCOPE.search(value)), bool(RESIDENTIAL_SCOPE.search(value))
    if business and residential:
        return 'mixed'
    return 'business_or_commercial' if business else 'residential' if residential else 'general'


def is_experience_question(text: str = '') -> bool:
    return bool(EXPERIENCE_TOPIC.search(str(text or '')))


EXPERIENCE_EVIDENCE_NOTE = (
    'Experience question. Each match has an experience_scope. Only residential-scope excerpts support claims about '
    'selling homes; business_or_commercial excerpts (businesses, laundromats, commercial or investment property) '
    'are a different kind of sale and must never be presented as residential home-sale experience or figures. '
    'Mixed excerpts support only their residential part; never mention the business part. If no residential '
    'excerpt is present, say plainly that verified residential details are not available here and optionally '
    'offer to share specific verified examples directly. Never mention business, laundromat or commercial '
    'experience in the reply.'
)


# Cosine distance (lower is closer) at which an excerpt is kept on similarity alone.
STRONG_MATCH_DISTANCE = 0.35


def knowledge_query(latest_message: str = '') -> str:
    """Retrieval query for the owner's latest message.

    A raw message such as "sell it quickly at best price like 675000$" embeds
    close to generic biography text. When the owner is talking about selling
    or price, the query names the advice topics that would actually answer it.
    """
    text = ' '.join(str(latest_message or '').split())
    if EXPERIENCE_TOPIC.search(text):
        # Checked first: "selling homes like mine" is about the agent, not the owner's price.
        return f'{text} | {EXPERIENCE_TOPIC_EXPANSION}'[:500]
    if seller_signals(text)['selling_intent'] or SELLER_TOPIC.search(text):
        return f'{text} | {SELLER_TOPIC_EXPANSION}'[:500]
    if APPROACH_TOPIC.search(text):
        return f'{text} | {APPROACH_TOPIC_EXPANSION}'[:500]
    return text[:500]


def _relevant_to_topic(match: dict) -> bool:
    score = match.get('score')
    strong = isinstance(score, (int, float)) and score <= STRONG_MATCH_DISTANCE
    return strong or bool(SELLER_ADVICE_TERMS.search(str(match.get('text') or '')))


# A question about the agent: experience, track record, brokerage, credentials,
# services, fees or approach. These need eligible agent knowledge.
AGENT_KNOWLEDGE_QUESTION = re.compile(
    r"\b(?:you|your|yourself|u|ur)\b[^.?!]{0,50}\b(?:experience\w*|years?|background|licen[cs]\w*|credentials?"
    r"|brokerage|company|firm|office|team|speciali[sz]\w*|achievements?|awards?|track record|sold|closed|sales"
    r"|listings?|commission|fees?|rates?|services?|serve|cover|areas?|markets?|approach|process|strateg\w*"
    r"|work with)\b"
    r"|\b(?:experience|background|licen[cs]\w*|credentials?|brokerage|commission|fees?|track record"
    r"|achievements?|awards?|speciali[sz]\w*)\b[^.?!]{0,40}\b(?:you|your)\b"
    r"|\bwho (?:are|r) (?:you|u)\b|\btell me about (?:yourself|you)\b|\bhow long have (?:you|u)\b",
    re.I,
)
# Requests phrased as statements or commands, without a question mark.
REQUEST_PHRASE = (r'^\s*(?:tell me|explain|share|show me|send me|walk me through|describe|give me|let me know)\b'
                  r"|\bi(?:['\u2019]d| would)? (?:like|want|wanna) to (?:know|hear|see|understand)\b|\bcurious (?:about|how|what)\b")
# "How much can I sell / get / list it for?"
SELL_FOR_QUESTION = re.compile(r'\bhow much\b[^.?!]{0,40}\b(?:sell|get|list)\b', re.I)


def is_agent_knowledge_question(text: str = '') -> bool:
    value = str(text or '')
    return bool(AGENT_KNOWLEDGE_QUESTION.search(value) or EXPERIENCE_TOPIC.search(value))


def is_seller_pricing_turn(text: str = '') -> bool:
    """The owner talks about selling, their hoped-for price, or what the home could sell for."""
    value = str(text or '')
    signals = seller_signals(value)
    return bool(signals['selling_intent'] or signals['target_price']
                or SELLER_TOPIC.search(value) or SELL_FOR_QUESTION.search(value))


def _agent_knowledge_relevant(text: str = '') -> bool:
    value = str(text or '')
    if is_agent_knowledge_question(value):
        return True  # mixed messages ("what's your commission at $675k?") keep RAG
    if is_seller_pricing_turn(value):
        return False
    # A business keyword alone is not a request for agent facts: "I had it
    # listed but it didn't sell" is a statement and gets no biography lookup.
    return bool(NEEDS_KNOWLEDGE.search(value)) and _asks_something(value)


ASKS_SOMETHING = re.compile(
    r'\?|^\s*(?:what|how|who|why|where|when|which|do|does|did|can|could|are|is|will|would)\b|' + REQUEST_PHRASE,
    re.I)


def _asks_something(text: str = '') -> bool:
    return bool(ASKS_SOMETHING.search(str(text or '')))


def agent_knowledge_allowed(text: str = '') -> bool:
    """Whether the model is offered the search_agent_knowledge tool this turn.

    Same rule as the preload: questions about the agent or the business.
    Greetings, acknowledgments, property questions ("Is the roof included?")
    and seller pricing turns are answered from the conversation, the property
    context and the Persona, so the tool is not offered for them.
    """
    return _agent_knowledge_relevant(text)


def needs_agent_knowledge(text: str = '') -> bool:
    """Whether the owner's message warrants an approved-knowledge preload."""
    return _agent_knowledge_relevant(text)


def search_agent_knowledge(
    query: str, context: AgentRuntimeContext, limit: int = 4, *, relevance_filter: bool = False
) -> dict:
    """Search only the resolved owner's eligible active-version knowledge.

    With ``relevance_filter`` (the SMS reply pipeline), a price/value query
    drops excerpts that do not speak to pricing, valuation or listing approach.
    """
    query = str(query or '').strip()[:500]
    limit = max(1, min(int(limit), 6))
    base = {
        'query': query,
        'collection': settings['sms']['agent_knowledge_collection'],
        'owner_user_id': context.user_id,
        'brokerage_id': context.brokerage_id,
        'published_version_id': context.active_persona_version_id,
        'document_status': 'Eligible owner-scoped factual knowledge only.',
    }
    if not query or context.active_persona_version_id is None:
        return {**base, 'retrieval': 'none', 'matches': [],
                'reason': 'No active published Persona knowledge scope is available.'}
    filters = {
        'owner_user_id': context.user_id,
        'brokerage_id': context.brokerage_id,
        'published_version_id': context.active_persona_version_id,
        'approval_status': 'eligible',
    }
    experience_topic = relevance_filter and bool(EXPERIENCE_TOPIC.search(query))
    seller_topic = relevance_filter and not experience_topic and bool(SELLER_TOPIC.search(query))
    approach_topic = relevance_filter and not seller_topic and bool(APPROACH_TOPIC.search(query))
    # Over-fetch for a topical query so off-topic excerpts can be dropped
    # without leaving the draft with nothing relevant.
    hits = vector_store.search_text(
        settings['sms']['agent_knowledge_collection'], query,
        k=min(limit * 3, 12) if (seller_topic or approach_topic) else limit, filters=filters,
    )
    matches = []
    for hit in hits:
        metadata = dict(hit.get('metadata') or {})
        # Defense in depth: an adapter or remote-store regression must not turn a
        # filtered query into cross-owner evidence.
        if any(metadata.get(key) != value for key, value in filters.items()):
            continue
        matches.append({
            'id': hit.get('id'),
            'score': hit.get('score'),
            'text': hit.get('text') or '',
            'source_document_id': metadata.get('source_document_id'),
            'source_type': metadata.get('source_type'),
            'chunk_index': metadata.get('chunk_index'),
            'content_hash': metadata.get('content_hash'),
            'owner_user_id': metadata.get('owner_user_id'),
            'brokerage_id': metadata.get('brokerage_id'),
            'published_version_id': metadata.get('published_version_id'),
            'approval_status': metadata.get('approval_status'),
        })
    # Owner conversations are about a residential property. Excerpts that only
    # describe business or commercial sales are never shown to the model.
    business_only = [match for match in matches if experience_scope(match.get('text')) == 'business_or_commercial']
    matches = [match for match in matches if match not in business_only]
    result = {
        **base,
        'business_excerpts_dropped': len(business_only),
        'retrieval': vector_store.backend if vector_store.available else 'unavailable',
        'embedding_model': vector_store.embedding_name if vector_store.available else None,
    }
    if experience_topic:
        for match in matches:
            match['experience_scope'] = experience_scope(match.get('text'))
        scopes = [match['experience_scope'] for match in matches[:limit]]
        # Business-only excerpts were dropped above; the rest are labelled.
        result['experience_evidence'] = {'residential_matches': sum(scope in ('residential', 'mixed') for scope in scopes)}
        result['evidence_note'] = EXPERIENCE_EVIDENCE_NOTE
        if not any(scope in ('residential', 'mixed') for scope in scopes):
            result['reason'] = ('No approved residential home-sale experience was found. Do not claim residential '
                                'sales, counts or results; answer transparently with what is known.')
    elif seller_topic:
        relevant = [match for match in matches if _relevant_to_topic(match)]
        result['relevance_filter'] = {'topic': 'seller_pricing_listing',
                                      'retrieved': len(matches), 'dropped': len(matches) - len(relevant)}
        matches = relevant
        if not matches:
            result['reason'] = ('No approved knowledge about pricing, valuation or listing approach was found. '
                                'Answer safely without agent-specific claims; do not use unrelated biography.')
    elif approach_topic:
        relevant = [match for match in matches if APPROACH_TERMS.search(str(match.get('text') or ''))]
        result['relevance_filter'] = {'topic': 'agent_approach',
                                      'retrieved': len(matches), 'dropped': len(matches) - len(relevant)}
        matches = relevant
        if not matches:
            result['reason'] = ('No approved knowledge about my listing, pricing or marketing approach was found. '
                                'Explain the standard process without specific marketing or capability claims; '
                                'licensing and biography are not evidence of marketing services.')
    result['matches'] = matches[:limit]
    return result


# Compatibility for existing non-runtime tests and the isolated legacy Bobbie
# diagnostics. The live generic pipeline must not call this wrapper.
needs_bobbie_knowledge = needs_agent_knowledge


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
