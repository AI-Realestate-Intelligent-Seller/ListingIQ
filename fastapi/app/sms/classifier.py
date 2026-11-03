"""Deterministic lead classification — port of lead-classifier.js.

Used as the safety net when the AI disposition call is unavailable, and to
detect opt-outs before any model call is made.
"""

import re

TERMINAL_STATUSES = {'dnc', 'not_interested', 'wrong_info', 'realtor', 'no_response'}
STATUS_PRIORITY = {
    'processing': 0, 'want_more_info': 1, 'interested': 2, 'ready_to_sell': 3, 'location_discussion': 3}

OPT_OUT = re.compile(
    r"\b(stop|stopall|unsubscribe|remove me|do not contact|don['’]?t contact|don['’]?t message"
    r"|leave me alone|take me off|done here)\b",
    re.I,
)
NO_REPLY = re.compile(r'^\(?no reply\)?$', re.I)
IMMEDIATE_BUYER_CONDITION = (
    re.compile(r'\b(?:do you|you|someone)\b.{0,45}\b(?:have|bring|present|provide)\b.{0,25}\b(?:a\s+)?(?:ready\s+)?buyer\b', re.I),
    re.compile(r'\b(?:need|want)\b.{0,45}\b(?:present|bring|provide|find)\b.{0,25}\b(?:a\s+)?buyer\b', re.I),
    re.compile(r'\b(?:buyer|offer)\b.{0,35}\b(?:ready|immediate(?:ly)?|move now|right now)\b', re.I),
)
QUALIFIED_PROCESS_OBJECTION = re.compile(
    r'\bnot interested\b\s+(?:in|with)\s+(?:(?:repeating|restarting|reliving)\s+(?:that|the|this)\s+process'
    r'|going through (?:that|the|this) process|listing again|relisting)\b',
    re.I,
)
DIRECT_REJECTION = re.compile(
    r"\bnot interested\b(?!\s+(?:in|with)\s+(?:a\s+)?(?:call|meeting|phone conversation))"
    r"|\b(?:have to pass|i(?:['’]ll| will) pass|already sold|wrong number)\b",
    re.I,
)
# The text reached the wrong person: wrong number, they don't live there, or they don't own it.
_NOT = r"(?:don['’]?t|dont|do\s+not|doesn['’]?t|does\s+not|never|no\s+longer)"
WRONG_INFO = re.compile(
    r"\bwrong\s+(?:#|(?:number|person|guy|lady|contact|house|address|property|info(?:rmation)?)\b)"
    r"|\b(?:you\s+have|you['’]?ve\s+got|you\s+got|got)\s+the\s+wrong\b"
    rf"|\b(?:i|we|they|he|she|nobody|no\s+one)\s+{_NOT}\s+(?:live|lives|lived|reside|resides)\s+(?:there|here|at)\b"
    r"|\b(?:i\s+am|i['’]m|im|we\s+are|we['’]re)\s+not\s+the\s+(?:owners?|homeowners?|person)\b"
    r"|\bnot\s+the\s+(?:owner|homeowner)\s+of\b"
    rf"|\b(?:i|we)\s+{_NOT}\s+own\s+(?:that|this|the|it|any)\b"
    r"|\b(?:not|isn['’]?t|never\s+been)\s+(?:my|our)\s+(?:property|house|home|place|address)\b"
    r"|\b(?:no\s*one|nobody)\s+(?:here\s+)?(?:by|with|named)\s+that\s+name\b"
    r"|\bdon['’]?t\s+know\s+(?:who|that\s+person|anyone\s+(?:by|named))\b",
    re.I,
)
# Someone who still owns the place can say "we don't live there"; that is not wrong info.
OWNER_CONTEXT = re.compile(
    r"\b(?:i|we)\s+(?:still\s+)?own\s+(?:it|that|this|the)\b|\brent(?:al|ed|ing)?\b|\btenants?\b"
    r"|\bmy\s+rental\b|\b(?:might|would|could|want\s+to|looking\s+to)\s+sell\b",
    re.I,
)
# The person says they are a realtor themselves (not that they have or work with one).
_I_AM = r"(?:i\s+am|i['’]?m|im|we\s+are|we['’]re|i\s+work\s+as|i['’]ve\s+been)"
_PRO = (r"(?:realtors?|real\s*estate\s+(?:agents?|brokers?|professionals?|salesperson|licensees?)"
        r"|listing\s+agents?|licensed\s+(?:agents?|brokers?|realtors?))")
REALTOR = re.compile(
    rf"\b{_I_AM}\s+(?:also\s+|actually\s+|myself\s+)?(?:an?\s+|the\s+)?(?:licensed\s+)?{_PRO}\b"
    rf"|\b{_I_AM}\s+(?:also\s+|actually\s+)?(?:an?\s+)?(?:agent|broker)\s+myself\b"
    rf"|\b{_I_AM}\s+(?:also\s+|actually\s+)?in\s+real\s*estate\s+(?:myself|too|also|as\s+well)\b"
    r"|\bi\s+(?:am\s+|['’]m\s+)?(?:work|working)\s+in\s+real\s*estate\b"
    r"|\b(?:i|we)\s+(?:am|are|['’]m|['’]re)?\s*(?:a\s+)?licensed\s+(?:in\s+real\s*estate|to\s+sell\s+real\s*estate)\b",
    re.I,
)
READY_TO_SELL = re.compile(r'\b(?:need to sell|ready to sell|sell soon|as soon as possible|immediately)\b', re.I)
INTERESTED = re.compile(
    r"\b(?:open to|might sell|consider(?:ing)? selling"
    r"|(?:i(?:['’]m| am)|we(?:['’]re| are)) (?:still )?interested|right price)\b",
    re.I,
)
CALL_OPENNESS = re.compile(
    r'\b(?:quick (?:call|visit|look) (?:could|would|might) work'
    r'|open to a (?:quick )?(?:call|meeting|visit|walk-?through)'
    r'|(?:you can|feel free to) (?:stop|come|swing|drop) by)\b',
    re.I,
)
WANT_MORE_INFO = re.compile(
    r'\b(?:email|send (?:me )?(?:info|information|details)|more info|recent sales|comps?|ballpark'
    r'|estimate|price range|commission|fees?|listing terms?|strategy|plan|options?|what are you proposing)\b',
    re.I,
)


# Owner says they want to sell ("I just want to sell it quickly", "looking to sell").
SELL_INTENT = re.compile(
    r"\b(?:want|wanna|need|looking|trying|hoping|plan(?:ning)?|ready|going|like|prefer)\s+to\s+sell\b"
    r"|\bwanna\s+sell\b"
    r"|\bsell\s+(?:it|this|the\s+\w+|my\s+\w+)\s+(?:quickly|quick|fast|asap|soon|now)\b",
    re.I,
)
SELL_NEGATION = re.compile(
    r"\b(?:don['’]?t|do\s+not|not|never|no\s+longer|won['’]?t|isn['’]?t|aren['’]?t)\b[^.?!]{0,25}$", re.I)
SELL_URGENCY = re.compile(
    r'\b(?:quick(?:ly)?|fast|asap|as soon as possible|soon|immediately|right away|urgent(?:ly)?'
    r'|this (?:week|month)|next (?:week|month))\b',
    re.I,
)
# A money amount: "$675,000", "675000$", "675k", "$1.2M", "675 thousand".
MONEY = re.compile(
    r'(?<![\w.])(\$\s?)?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(k|m|mm|million|thousand)?\b\s?(\$|dollars|usd)?',
    re.I,
)
PRICE_CONTEXT = re.compile(
    r'\b(?:price|asking|ask|list|sell|get|want|for|around|about|like|at|offer|worth|value|guarantee|target'
    r'|hoping|expect(?:ing)?)\b[^.?!]{0,20}$',
    re.I,
)
_MULTIPLIERS = {'k': 1_000, 'thousand': 1_000, 'm': 1_000_000, 'mm': 1_000_000, 'million': 1_000_000}


def _stated_price(text: str) -> int | None:
    """The owner's stated dollar figure, only when it reads as a property price."""
    for match in MONEY.finditer(text):
        dollar_before, number, suffix, dollar_after = match.groups()
        try:
            amount = float(number.replace(',', '')) * _MULTIPLIERS.get((suffix or '').lower(), 1)
        except ValueError:
            continue
        explicit = bool(dollar_before or dollar_after or suffix)
        if not explicit and not PRICE_CONTEXT.search(text[:match.start()]):
            continue
        if 10_000 <= amount <= 100_000_000:
            return round(amount)
    return None


def seller_signals(text: str = '') -> dict:
    """Deterministic read of selling intent, urgency and the owner's own target price.

    A stated price is the owner's expectation, never a verified valuation.
    """
    value = str(text or '')
    intent_match = next((match for match in SELL_INTENT.finditer(value)
                         if not SELL_NEGATION.search(value[:match.start()])), None)
    selling_intent = bool(intent_match) and not DIRECT_REJECTION.search(value)
    price = _stated_price(value)
    urgency = 'high' if SELL_URGENCY.search(value) else None
    return {
        'selling_intent': selling_intent,
        'urgency': urgency if selling_intent else None,
        'target_price': price,
        'target_price_text': f'${price:,}' if price else None,
        # Enough to stop qualifying and offer a call or visit.
        'strong': selling_intent and bool(price or urgency),
    }


def is_opt_out(text: str = '') -> bool:
    return bool(OPT_OUT.search(str(text or '').strip()))


def is_wrong_info(text: str = '') -> bool:
    value = str(text or '').strip()
    return bool(WRONG_INFO.search(value)) and not OWNER_CONTEXT.search(value)


def is_realtor(text: str = '') -> bool:
    """True when the person says they are a realtor or real estate agent themselves."""
    return bool(REALTOR.search(str(text or '')))


def _has_immediate_buyer_condition(text: str = '') -> bool:
    value = str(text or '').strip()
    return any(pattern.search(value) for pattern in IMMEDIATE_BUYER_CONDITION)


def classify_lead_message(text: str = '') -> dict | None:
    value = str(text or '').strip()
    if not value:
        return None
    if NO_REPLY.match(value):
        return {'lead_status': 'no_response', 'terminal': True}
    if is_opt_out(value):
        return {'lead_status': 'dnc', 'dnc_alert': True, 'terminal': True}
    if is_wrong_info(value):
        return {'lead_status': 'wrong_info', 'terminal': True}
    # A seller can reject relisting while still expressing a concrete condition
    # under which they would engage. Do not let the words "not interested"
    # erase that stronger, actionable signal.
    if _has_immediate_buyer_condition(value):
        return {'lead_status': 'interested'}
    if DIRECT_REJECTION.search(value) and not QUALIFIED_PROCESS_OBJECTION.search(value):
        return {'lead_status': 'not_interested', 'terminal': True}
    signals = seller_signals(value)
    if READY_TO_SELL.search(value) or signals['strong']:
        return {'lead_status': 'ready_to_sell'}
    if INTERESTED.search(value) or CALL_OPENNESS.search(value) or signals['selling_intent']:
        return {'lead_status': 'interested'}
    if WANT_MORE_INFO.search(value):
        return {'lead_status': 'want_more_info'}
    return None


def merge_lead_status(current: str = 'processing', nxt: str | None = None) -> str:
    if not nxt:
        return current or 'processing'
    if nxt == 'dnc':
        return nxt
    if current in TERMINAL_STATUSES:
        return current
    if nxt in TERMINAL_STATUSES:
        return nxt
    return nxt if STATUS_PRIORITY.get(nxt, 0) >= STATUS_PRIORITY.get(current, 0) else current


def finalized_lead_status(status: str = 'processing') -> str:
    return 'not_interested' if not status or status == 'processing' else status
