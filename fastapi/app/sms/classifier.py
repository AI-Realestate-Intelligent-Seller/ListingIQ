"""Deterministic lead classification — port of lead-classifier.js.

Used as the safety net when the DeepSeek disposition call is unavailable, and to
detect opt-outs before any model call is made.
"""

import re

TERMINAL_STATUSES = {'dnc', 'not_interested', 'no_response'}
STATUS_PRIORITY = {'processing': 0, 'want_more_info': 1, 'interested': 2, 'ready_to_sell': 3}

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
READY_TO_SELL = re.compile(r'\b(?:need to sell|ready to sell|sell soon|as soon as possible|immediately)\b', re.I)
INTERESTED = re.compile(
    r"\b(?:open to|might sell|consider(?:ing)? selling"
    r"|(?:i(?:['’]m| am)|we(?:['’]re| are)) (?:still )?interested|right price)\b",
    re.I,
)
CALL_OPENNESS = re.compile(r'\b(?:quick call (?:could|would|might) work|open to a (?:quick )?(?:call|meeting))\b', re.I)
WANT_MORE_INFO = re.compile(
    r'\b(?:email|send (?:me )?(?:info|information|details)|more info|recent sales|comps?|ballpark'
    r'|estimate|price range|commission|fees?|listing terms?|strategy|plan|options?|what are you proposing)\b',
    re.I,
)


def is_opt_out(text: str = '') -> bool:
    return bool(OPT_OUT.search(str(text or '').strip()))


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
    # A seller can reject relisting while still expressing a concrete condition
    # under which they would engage. Do not let the words "not interested"
    # erase that stronger, actionable signal.
    if _has_immediate_buyer_condition(value):
        return {'lead_status': 'interested'}
    if DIRECT_REJECTION.search(value) and not QUALIFIED_PROCESS_OBJECTION.search(value):
        return {'lead_status': 'not_interested', 'terminal': True}
    if READY_TO_SELL.search(value):
        return {'lead_status': 'ready_to_sell'}
    if INTERESTED.search(value) or CALL_OPENNESS.search(value):
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
