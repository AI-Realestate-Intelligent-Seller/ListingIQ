"""Output safety guards for Bobbie's SMS replies.

Direct port of the prototype's bobbie-policy.js. These are deterministic checks
applied to every generated draft; conversation intent and calendar state are
decided by DeepSeek in bobbie.py.
"""

import re

EMAIL_ADDRESS = re.compile(r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', re.I)
PHONE_NUMBER = re.compile(r'(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b')
DELIVERY_PROMISE = re.compile(
    r"\b(?:i['’]?ll|i will|i can(?!['’]?t|\s+not)|we['’]?ll|we will|have Bobbie)\b[^.!?]{0,90}"
    r"\b(?:email|send|pull|get|prepare|share)\b[^.!?]{0,90}"
    r"\b(?:email|comps?|comparables?|recent sales|sales data|details|breakdown|range|estimate|report)\b"
    r"|\b(?:i['’]?ll|i will) (?:send|get) (?:that|those|them|it) (?:over|to you)\b"
    r"|\b(?:i['’]?ll|i will) (?:be in touch|reach out|get back|follow up)\b"
    r"|\b(?:pull|review|handle)\b[^.!?]{0,80}\b(?:on my end|separately|get back to you|be in touch)\b",
    re.I,
)
UNSUPPORTED_FEE = re.compile(
    r'(?:\b\d+(?:\.\d+)?\s*%|\bno upfront fees?\b|\bcommission only\b|\btypically (?:charge|work).{0,30}\bcommission\b)',
    re.I,
)
UNSUPPORTED_BUYER = re.compile(
    r'\b(?:active|specific|ready|qualified|cash) buyers?\b|\bnetwork of buyers?\b'
    r'|\bbuyers? (?:actively )?(?:looking|searching)\b',
    re.I,
)
BUYER_DENIAL = re.compile(
    r"\b(?:i|we)\s+(?:do not|don['’]?t)\s+(?:currently\s+)?have\s+(?:a\s+)?"
    r'(?:verified|specific|ready|qualified|cash|active)?\s*buyer\b'
    r'|\bthere (?:is|are) no\s+(?:verified|specific|ready|qualified|cash|active)?\s*buyers?\b',
    re.I,
)
UNSUPPORTED_PRODUCTION = re.compile(
    r"\b(?:my|our) recent sales\b|\b(?:i|we)(?:['’]?ve| have) (?:sold|closed|handled)\b", re.I
)
UNSUPPORTED_LOCAL_EXPERIENCE = re.compile(
    r"\b(?:i['’]?ve (?:worked|helped)|i (?:work|have worked) with|i know the [^.?!]{0,35} market"
    r'|my local experience)\b',
    re.I,
)
PRECISE_TENURE = re.compile(r'\b(?:several years|since \d{4}|(?:for|over) \d+ years)\b', re.I)
EXAMPLE_FACT_LEAK = re.compile(
    r"\b(?:sliding glass doors?|100 showings?|investor['’]?s note|27620 E Lakeview|Morgan|Grant|Dolby Haas)\b",
    re.I,
)
UNSUPPORTED_OUTCOME = re.compile(
    r'\b(?:get(?:ting)? homes? sold efficiently|generate real offers? quickly'
    r'|sell for the right price|attract the right buyer quickly)\b',
    re.I,
)
IDENTITY_ASSISTANT = re.compile(r"\bBobbie Fisher['’]?s (?:AI )?assistant\b", re.I)
IDENTITY_THIRD_PERSON = re.compile(
    r'\bBobbie\s+(?:would|will|can|needs?|has|focuses|is)\b'
    r'|\b(?:she|her)\s+(?:would|will|can|needs?|has|focuses|is)\b',
    re.I,
)
PHONE_SOURCE_CLAIM_SUBJECT = re.compile(r'\b(?:number|contact info|contact information)\b', re.I)
PHONE_SOURCE_CLAIM_ORIGIN = re.compile(r'\b(?:public|property|listing|county|tax)\s+(?:record|records|data)\b', re.I)

STOP_WORDS = {
    'a', 'about', 'and', 'are', 'at', 'be', 'for', 'have', 'i', 'if', 'in', 'is', 'it', 'me',
    'my', 'of', 'on', 'or', 'the', 'this', 'to', 'we', 'what', 'with', 'would', 'you', 'your',
}


def clean_reply(value: str = '') -> str:
    text = re.sub(r'^(?:["\'“”])|(?:["\'“”])$', '', str(value or ''))
    return re.sub(r'\s+', ' ', text).strip()


def fit_complete_sms(value: str, max_length: int = 240) -> str:
    """Trim to a complete sentence rather than cutting a word in half."""
    text = clean_reply(value)
    if len(text) <= max_length:
        return text
    prefix = text[:max_length]
    sentence_end = max(prefix.rfind('.'), prefix.rfind('!'), prefix.rfind('?'))
    if sentence_end >= 80:
        return prefix[:sentence_end + 1].strip()
    word_end = prefix.rfind(' ')
    return re.sub(r'[,:;\s-]+$', '', prefix[:max(1, word_end)]) + '…'


def _normalized_tokens(value: str = '') -> list:
    text = re.sub(r'[^a-z0-9\s]', ' ', clean_reply(value).lower())
    return [token for token in text.split() if len(token) > 1 and token not in STOP_WORDS]


def similarity(left: str = '', right: str = '') -> float:
    a, b = set(_normalized_tokens(left)), set(_normalized_tokens(right))
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), len(b))


def _questions(value: str = '') -> list:
    return [match.group(1) for match in re.finditer(r'(?:^|[.!])\s*([^.!?]*\?)', clean_reply(value))]


def _question_intent(value: str = '') -> str:
    text = str(value).lower()
    if re.search(r'\b(?:call|phone|meet|meeting|appointment|visit|walk-?through|stop by|come by|swing by)\b', text):
        return 'scheduled_meeting'
    if re.search(r'\b(?:chat|talk|conversation)\b', text):
        return 'conversation'
    if re.search(r'\b(?:timeline|when|how soon|timeframe)\b', text):
        return 'timeline'
    if re.search(r'\b(?:goal|priority|important|matter most)\b', text):
        return 'goals'
    if re.search(r'\b(?:fee|commission|rate|percentage)\b', text):
        return 'fees'
    if re.search(r'\b(?:price|pricing|value|worth)\b', text):
        return 'pricing'
    if re.search(r'\b(?:buyer|offer)\b', text):
        return 'buyer'
    return ''


def find_conversation_repetition(reply: str, history: list | None = None) -> str:
    """Bobbie must progress the conversation, never re-ask an answered question."""
    prior = [clean_reply(item.get('text') or '') for item in (history or [])
             if item.get('direction') == 'outbound']
    prior = [item for item in prior if item]
    current = clean_reply(reply)
    current_questions = _questions(current)
    for previous in prior:
        if current.lower() == previous.lower():
            return 'repeated_reply'
        if len(_normalized_tokens(current)) >= 6 and similarity(current, previous) >= 0.86:
            return 'repeated_reply'
        for current_question in current_questions:
            for previous_question in _questions(previous):
                intent = _question_intent(current_question)
                same_intent = bool(intent) and intent == _question_intent(previous_question)
                if (similarity(current_question, previous_question) >= 0.72
                        or (same_intent and intent == 'scheduled_meeting')):
                    return 'repeated_question'
    return ''


def find_reply_policy_violations(reply: str, phone_source_known: bool | None = None,
                                 max_length: int = 240, history: list | None = None) -> list:
    text = clean_reply(reply)
    violations = []
    if len(text) > max_length:
        violations.append('over_length')
    if IDENTITY_ASSISTANT.search(text) or IDENTITY_THIRD_PERSON.search(text):
        violations.append('identity_switch')
    if EMAIL_ADDRESS.search(text):
        violations.append('invented_email')
    if PHONE_NUMBER.search(text):
        violations.append('invented_phone')
    if DELIVERY_PROMISE.search(text):
        violations.append('unsupported_delivery')
    if UNSUPPORTED_FEE.search(text):
        violations.append('unsupported_fee')
    # Honest denials such as "I don't have a ready buyer" are safe. Remove only
    # those clauses, then inspect the remainder for an unsupported positive claim.
    if UNSUPPORTED_BUYER.search(BUYER_DENIAL.sub(' ', text)):
        violations.append('unsupported_buyer')
    if UNSUPPORTED_PRODUCTION.search(text):
        violations.append('unsupported_production')
    if UNSUPPORTED_LOCAL_EXPERIENCE.search(text):
        violations.append('unsupported_local_experience')
    if UNSUPPORTED_OUTCOME.search(text):
        violations.append('unsupported_outcome')
    if PRECISE_TENURE.search(text):
        violations.append('unsupported_tenure')
    if EXAMPLE_FACT_LEAK.search(text):
        violations.append('example_fact_leak')
    if (phone_source_known is False
            and PHONE_SOURCE_CLAIM_SUBJECT.search(text)
            and PHONE_SOURCE_CLAIM_ORIGIN.search(text)):
        violations.append('unsupported_phone_source')
    repetition = find_conversation_repetition(text, history or [])
    if repetition:
        violations.append(repetition)
    return list(dict.fromkeys(violations))


def safe_grounded_fallback(latest_inbound: str = '', violations: list | None = None,
                           history: list | None = None, plan: dict | None = None) -> str:
    """Last-resort reply that never claims anything outside approved context."""
    violations = violations or []
    plan = plan or {}
    latest = str(latest_inbound or '')
    candidates = []

    if plan.get('next_step') == 'offer_call':
        candidates.append('Thanks—that gives me enough to understand what you need. '
                          'Would you be open to me stopping by to take a quick look at the property?')
    if ('unsupported_phone_source' in violations
            or re.search(r'\b(?:how did you (?:find|get)|where did you get|got|found)\b[^?]{0,45}'
                         r'\b(?:my |the )?(?:number|contact info)|\bmy (?:number|contact info)\b', latest, re.I)):
        candidates.append('I have a property lead record, but it doesn’t show how your phone number '
                          'was sourced, so I don’t want to guess.')
    if ('invented_phone' in violations
            or re.search(r'\b(?:your (?:number|phone)|call you|reach you)\b', latest, re.I)):
        candidates.append('I don’t have a verified callback number to provide in this chat; '
                          'scheduling has to use the live calendar.')
    if ('unsupported_fee' in violations
            or re.search(r'\b(?:commission|fee|percentage|rate|listing term)\b', latest, re.I)):
        candidates.append('Fees and listing terms depend on the service and written agreement, '
                          'so I don’t have approved numbers to quote here.')
    if ('unsupported_delivery' in violations
            or re.search(r'\b(?:email|comps?|recent sales|sales data|estimate|report)\b', latest, re.I)):
        candidates.append('This chat can’t retrieve verified comps or perform a later follow-up; '
                          'those records need a separate human review.')
    if 'unsupported_buyer' in violations or re.search(r'\bbuyers?\b', latest, re.I):
        candidates.append('I don’t have a verified buyer ready today. I understand you won’t repeat '
                          'the same listing process; would you only consider a direct buyer with proof of funds?')
    if (re.search(r'\b(?:fresh approach|strategy|plan|options?|different|highlights?)\b', latest, re.I)
            or 'example_fact_leak' in violations):
        candidates.append('I’d review the prior pricing, presentation, and feedback to identify what '
                          'kept buyers from acting before suggesting a new approach.')
    if re.search(r'\b(?:why|what made|what brought|think of me|specific (?:place|property)|reach out)\b', latest, re.I):
        candidates.append('That expired-listing signal is the only verified reason I have; '
                          'I don’t have another property-specific trigger to claim.')
    if re.search(r'\b(?:price|pricing|value|worth)\b', latest, re.I):
        candidates.append('I’d compare the prior price with current competition and buyer feedback '
                          'before recommending a position.')
    if re.search(r'\b(?:timeline|timeframe|how long)\b', latest, re.I):
        candidates.append('Timing depends on price, condition, and current demand, so I don’t have '
                          'verified data to promise a timeframe.')
    if re.search(r'\bnext step\b', latest, re.I):
        candidates.append('The next step would be a human review of the prior listing and current '
                          'market data; this chat can’t retrieve those records.')
    candidates.append('I’ve shared what I can verify here, and I don’t want to repeat myself or invent details.')

    for candidate in candidates:
        if not find_conversation_repetition(candidate, history or []):
            return candidate
    return 'I don’t have anything new and verified to add, so I’ll leave it there.'
