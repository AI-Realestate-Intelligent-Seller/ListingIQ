"""Output safety guards for Personal AI SMS replies.

These are deterministic checks
applied to every generated draft; conversation intent and calendar state are
decided by the configured AI provider in bobbie.py.
"""

import contextvars
import os
import re

from ..core.config import settings
from .classifier import seller_signals


# ---------------------------------------------------------------------------
# Local debugging prints ([AI DEBUG]). Simulation mode only; set
# AI_DEBUG_PRINTS=0 to silence them. Never prints keys or phone numbers.
_AI_DEBUG = contextvars.ContextVar('ai_debug', default=None)


def ai_debug_enabled() -> bool:
    mode = str((settings.get('telnyx') or {}).get('mode') or '').strip().lower()
    flag = os.getenv('AI_DEBUG_PRINTS', 'true').strip().lower()
    return mode == 'simulation' and flag not in ('0', 'false', 'no', 'off')


def ai_debug_begin(conversation_id=None, inbound_message_id=None) -> None:
    _AI_DEBUG.set({'conversation_id': conversation_id, 'inbound_message_id': inbound_message_id,
                   'phase': 'original', 'source': 'original'})


def ai_debug_state() -> dict:
    return _AI_DEBUG.get() or {}


def ai_debug_set(**fields) -> None:
    state = _AI_DEBUG.get()
    if state is not None:
        state.update(fields)


def ai_debug(_stage: str, _message: str = '', /, **fields) -> None:
    """Print one debug line. Never raises: debugging must not change the reply."""
    try:
        if not ai_debug_enabled():
            return
        state = ai_debug_state()
        ids = f"conv={state.get('conversation_id', '-')} inbound_msg={state.get('inbound_message_id', '-')}"
        details = ' '.join(f'{key}={value!r}' for key, value in fields.items())
        print(f'[AI DEBUG][{_stage}] {ids} {_message} {details}'.rstrip(), flush=True)
    except Exception:  # pragma: no cover - debug output only
        pass


def ai_debug_safely(function):
    """Wrap a debug-only helper so it can never raise into the SMS pipeline."""
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception:  # pragma: no cover - debug output only
            return None
    return wrapper

EMAIL_ADDRESS = re.compile(r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', re.I)
PHONE_NUMBER = re.compile(r'(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b')
DELIVERY_PROMISE = re.compile(
    r"\b(?:i['’]?ll|i will|i can(?!['’]?t|\s+not)|we['’]?ll|we will|have (?:the )?agent)\b[^.!?]{0,90}"
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
    r"\b(?:my|our) recent sales\b|\b(?:i|we)(?:['’]?ve| have) (?:sold|closed)\b"
    r"|\b(?:i|we)(?:['’]?ve| have) handled\s+(?:\w+\s+){0,2}?(?:sales|transactions|listings|deals|closings|homes|properties)\b",
    re.I
)
# First-person work history is allowed when authorized evidence supports it;
# an unsupported local track record or client count is not.
UNSUPPORTED_LOCAL_EXPERIENCE = re.compile(
    r"\b(?:i['’]?ve|i have|i)\s+(?:worked|helped|work|served|represented)\b[^.?!]{0,60}?"
    r"(?:\b(?:in|around|across|throughout|near)\s+(?:your|this|the|our)\s+(?:area|neighbou?rhood|community|town"
    r"|city|market|street|block|zip(?:\s?code)?|subdivision)\b"
    r"|\b(?:nearby|locally|near you|around here|in the area)\b"
    r"|\b(?:many|lots of|dozens of|hundreds of|countless|plenty of|other|\d+)\s+(?:local\s+)?(?:sellers|buyers"
    r"|homeowners|clients|families|owners|properties|homes|listings)\b)"
    r"|\bi know the [^.?!]{0,35} market\b|\bmy local experience\b",
    re.I,
)
PRECISE_TENURE = re.compile(
    r'\b(?:several years|since \d{4}|(?:for|over) \d+ years'
    r'|\d+\+?\s+years?\s+(?:of\s+)?(?:local\s+|real[-\s]estate\s+|industry\s+)?experience'
    r'|\d+\+?\s+years?\s+(?:in|as)\s+(?:real estate|the business|the industry|this area|your area|an? (?:realtor|agent|broker))'
    r'|decades? of experience)\b',
    re.I,
)
EXAMPLE_FACT_LEAK = re.compile(
    r"\b(?:sliding glass doors?|100 showings?|investor['’]?s note|27620 E Lakeview|Morgan|Grant|Dolby Haas)\b",
    re.I,
)
UNSUPPORTED_OUTCOME = re.compile(
    r'\b(?:get(?:ting)? homes? sold efficiently|generate real offers? quickly'
    r'|sell for the right price|attract the right buyer quickly)\b',
    re.I,
)
# Promising a sale price, sale, or timeline outcome. Negated forms ("I can't
# guarantee") do not match.
UNSUPPORTED_GUARANTEE = re.compile(
    r"\b(?:i|we)(?:['’]ll| will| can| do)?\s+guarantee\b"
    r"|\bguaranteed?\s+(?:to\s+(?:sell|get)|(?:a\s+)?(?:sale|price|offer|buyer)|\$\s?\d)",
    re.I,
)
GUARANTEE_NEGATION = re.compile(
    r"\b(?:can['’]?t|cannot|can not|won['’]?t|don['’]?t|do not|never|no one can|nobody can|not)\b[^.?!]{0,15}$", re.I)


def _unsupported_guarantee(text: str) -> str:
    for match in UNSUPPORTED_GUARANTEE.finditer(text):
        if not GUARANTEE_NEGATION.search(text[:match.start()]):
            return match.group(0)
    return ''


IDENTITY_ASSISTANT = re.compile(r"\b(?:my|the agent['’]?s) (?:AI )?assistant\b", re.I)
QUOTED_TEXT = re.compile(r'"[^"]*"|“[^”]*”')


# The owner asks whether a person or an AI is texting.
AI_IDENTITY_QUESTION = re.compile(
    r"\b(?:are|r)\s+(?:you|u)\s+(?:an?\s+|really\s+|actually\s+)?(?:ai|a\.i\.|bot|robot|chat\s?bot|human|real(?!\s+estate)"
    r"|(?:a\s+)?(?:real\s+)?person|automated|(?:a\s+)?machine)\b"
    r"|\bis\s+this\s+(?:an?\s+|really\s+|actually\s+)?(?:ai|bot|robot|automated|(?:a\s+)?(?:real\s+)?(?:person|human)"
    r"|real(?!\s+estate))\b"
    r"|\bam\s+i\s+(?:talking|speaking|texting|chatting)\s+(?:to|with)\s+(?:an?\s+|the\s+)?(?:ai|bot|robot|machine"
    r"|computer|(?:real\s+)?(?:person|human))\b"
    r"|\b(?:automated|auto-?generated|computer[-\s]generated)\s+(?:message|reply|text|response)s?\b",
    re.I,
)
AI_DISCLOSURE = re.compile(r'\b(?:AI|A\.I\.|automated|artificial intelligence)\b', re.I)
# Never claim a human is personally texting or deny being automated.
FALSE_HUMAN_CLAIM = re.compile(
    r"\b(?:I['’]?m|I\s+am)\s+(?:not\s+(?:an?\s+)?(?:ai|bot|robot|machine|automated)|(?:a\s+)?real\s+(?:person|human)"
    r"|(?:a\s+)?human)\b"
    r"|\bnot\s+(?:an?\s+)?(?:ai|bot|robot|automated)\b"
    r"|\b(?:this\s+is|it['’]s)\s+(?:really|actually)\s+me\b"
    r"|\bpersonally\s+(?:texting|typing|writing|replying)\b",
    re.I,
)


def _agent_name_pattern(agent_name: str = '') -> str:
    """Regex for the agent's full name or first name alone ("Dana" / "Dana Rivera")."""
    parts = [part for part in re.split(r'\s+', str(agent_name or '').strip()) if part]
    if not parts:
        return ''
    return re.escape(parts[0]) + ''.join(rf'(?:\s+{re.escape(part)})?' for part in parts[1:])


def is_ai_identity_question(text: str = '', agent_name: str = '') -> bool:
    """Is the owner asking whether a person, the agent personally, a bot or an AI is texting?"""
    value = QUOTED_TEXT.sub(' ', str(text or ''))
    if AI_IDENTITY_QUESTION.search(value):
        return True
    name = _agent_name_pattern(agent_name)
    if not name:
        return False
    personal = re.compile(
        rf"\b(?:are|r)\s+(?:you|u)\s+(?:really\s+|actually\s+)?{name}\b"
        rf"|\bis\s+this\s+(?:really\s+|actually\s+)?{name}\b"
        rf"|\bam\s+i\s+(?:talking|speaking|texting|chatting)\s+(?:to|with)\s+{name}\b",
        re.I,
    )
    return bool(personal.search(value))


def _false_human_claim(text: str, agent_name: str = '') -> bool:
    """Denying automation, or claiming the agent is personally typing ("this is really Dana")."""
    if FALSE_HUMAN_CLAIM.search(text):
        return True
    name = _agent_name_pattern(agent_name)
    if not name:
        return False
    return bool(re.search(
        rf"\b(?:I['’]?m|I\s+am)\s+(?:really|actually)\s+{name}\b"
        rf"|\b(?:this\s+is|it['’]s)\s+(?:really|actually)\s+{name}\b",
        text, re.I))


def _latest_owner_text(history: list | None) -> str:
    for item in reversed(history or []):
        if item.get('direction') == 'inbound':
            return str(item.get('text') or '')
    return ''


def _third_person_identity(text: str, agent_name: str = '') -> bool:
    """The agent talking about themselves by name, e.g. "Dana would" or "Dana Rivera's team".

    Matches the full name or the first name alone, as the original Bobbie check
    did. Self-introductions ("I'm Dana Rivera") and quoted owner text are removed
    first because they are not third-person self-reference.
    """
    parts = [part for part in re.split(r'\s+', str(agent_name or '').strip()) if part]
    if not parts:
        return False
    first = re.escape(parts[0])
    rest = ''.join(rf'(?:\s+{re.escape(part)})?' for part in parts[1:])
    name = rf'{first}{rest}'
    introduction = re.compile(
        rf"\b(?:I['’]m|I am|this is|it['’]s|here['’]s)\s+{name}\b", re.I)
    third_person = re.compile(
        rf'\b{name}\s+(?:would|will|can|could|needs?|has|had|focuses|is|was|previously|currently'
        rf'|recently|also|works|worked|joined|specializes|helps|leads|led)\b'
        rf"|\b{name}['’]s\s+(?:background|experience|career|work|team|brokerage|office|role|history|services)\b",
        re.I,
    )
    unquoted = QUOTED_TEXT.sub(' ', text)
    # "<name>'s assistant" is checked before introductions are stripped, so
    # "This is Dana Rivera's AI assistant" is still caught.
    assistant = re.compile(rf"\b{name}['’]s\s+(?:AI\s+)?assistant\b", re.I)
    return bool(assistant.search(unquoted) or third_person.search(introduction.sub('I', unquoted)))


PHONE_SOURCE_CLAIM_SUBJECT = re.compile(r'\b(?:number|contact info|contact information)\b', re.I)
PHONE_SOURCE_CLAIM_ORIGIN = re.compile(r'\b(?:public|property|listing|county|tax)\s+(?:record|records|data)\b', re.I)

STOP_WORDS = {
    'a', 'about', 'and', 'are', 'at', 'be', 'for', 'have', 'i', 'if', 'in', 'is', 'it', 'me',
    'my', 'of', 'on', 'or', 'the', 'this', 'to', 'we', 'what', 'with', 'would', 'you', 'your',
}


def clean_reply(value: str = '') -> str:
    text = re.sub(r'^(?:["\'“”])|(?:["\'“”])$', '', str(value or ''))
    return re.sub(r'\s+', ' ', text).strip()


def strip_trailing_signature(reply: str = '', agent_name: str = '', brokerage_name: str = '') -> str:
    """Remove a trailing name/brokerage sign-off from a conversational reply.

    The first outreach identifies the agent and brokerage; the model tends to
    copy that sign-off from the thread onto every later reply. Only a sign-off
    standing alone after the final sentence is removed (for example
    "... Claude Tester – Claude Test Brokerage"); a sentence that names the
    agent ("This is Claude Tester with ...") and any other text, including
    opt-out or disclosure wording, is never touched.
    """
    text = str(reply or '')
    name = ' '.join(str(agent_name or '').split())
    if not name:
        return text
    brokerage = ' '.join(str(brokerage_name or '').split())
    tail = (r'(?:[-–—]\s*)?' + re.escape(name)
            + (r'(?:\s*(?:[-–—|,]|\bwith\b|\bat\b)\s*' + re.escape(brokerage) + ')?' if brokerage else '')
            + r'\s*\.?\s*$')
    match = re.search(r'(?<=[.!?])\s+' + tail, text, re.I)
    if not match or not text[:match.start()].strip():
        return text
    return text[:match.start()].rstrip()


# Hard provider ceiling for one outbound message (the same 1600-character cap the
# manual send API enforces). This is a carrier/provider limit, not a style limit:
# AI replies are never rejected or trimmed for being longer than one segment.
SMS_PROVIDER_MAX_CHARS = 1600

# GSM 03.38 basic character set plus its extension table (extension chars count twice).
_GSM7_BASIC = set(
    '@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !"#¤%&\'()*+,-./0123456789:;<=>?'
    '¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà')
_GSM7_EXTENDED = set('^{}\\[~]|€\f')


def estimate_sms_segments(text: str = '') -> dict:
    """Estimated carrier segments for reporting only; never a reason to reject a reply."""
    value = str(text or '')
    if all(char in _GSM7_BASIC or char in _GSM7_EXTENDED for char in value):
        units = sum(2 if char in _GSM7_EXTENDED else 1 for char in value)
        encoding, single, multi = 'GSM-7', 160, 153
    else:
        # UCS-2 counts UTF-16 code units; curly quotes or emoji force this encoding.
        units = len(value.encode('utf-16-le')) // 2
        encoding, single, multi = 'UCS-2', 70, 67
    segments = 0 if units == 0 else (1 if units <= single else -(-units // multi))
    estimate = {'encoding': encoding, 'characters': len(value), 'segments': segments}
    try:
        price = float(os.getenv('SMS_COST_PER_SEGMENT_USD') or 0)
    except ValueError:
        price = 0.0
    if price > 0:
        estimate['estimated_cost_usd'] = round(price * segments, 4)
    return estimate


def fit_complete_sms(value: str, max_length: int = 240) -> str:
    """Trim to a complete sentence rather than cutting a word in half.

    Not used on AI replies any more: generated SMS are never silently truncated.
    """
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
    if re.search(r'\b(?:call|phone|meet|meeting|appointment|visit|walk-?through'
                 r'|(?:stop(?:ping)?|com(?:e|ing)|swing(?:ing)?|drop(?:ping)?) by|take a (?:quick )?look)\b', text):
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


def asked_questions(history: list | None = None) -> list:
    """Every question the agent has already sent in this conversation, oldest first."""
    asked = []
    for item in history or []:
        if item.get('direction') == 'outbound':
            asked.extend(question.strip() for question in _questions(item.get('text') or ''))
    return list(dict.fromkeys(question for question in asked if question))


# Re-asking these topics is a repeat even when worded differently
# ("What price were you hoping to get for it?" vs "…for 1 Test St?").
REPEAT_BY_INTENT = {'scheduled_meeting', 'pricing'}
# Steps at which the agent must not yet ask for a visit, call or meeting.
NO_MEETING_STEPS = {'answer_and_qualify', 'answer_only'}


# The prompt deliberately answers valuation, comps and fee questions with an
# in-person look, so a visit invitation is expected there.
VISIT_ANSWERS_QUESTION = re.compile(
    r'\b(?:price|pricing|value|valuation|worth|sell for|comps?|comparables?|fees?|commission)\b'
    r'|\bhow much\b[^.?!]{0,40}\b(?:sell|get|list)\b', re.I)


# A first-person visit offer stated rather than asked ("I'd like to stop by…").
MEETING_OFFER = re.compile(
    r"\b(?:i(?:['’]d| would)(?: also| really)? (?:like|love) to|i can|let me|i(?:['’]m| am) happy to"
    r"|i(?:['’]ll| will))\b[^.?!]{0,40}"
    r"\b(?:stop(?:ping)? by|come by|swing by|drop by|visit|meet|take a (?:quick )?look"
    r"|see (?:the|your) (?:home|house|property|place)(?: in person)?)\b",
    re.I,
)


def meeting_offer_permitted(next_step: str | None = None, latest_owner_text: str = '',
                            calendar_state: str = 'none') -> bool:
    """Single rule for whether this reply may offer a call or visit.

    The generator's instructions, the deterministic policy check and the
    reviewer payload all use this value, so they cannot disagree.
    """
    if calendar_state == 'call_declined' or next_step == 'close':
        return False
    if calendar_state == 'call_accepted':
        # The owner already agreed to meet; asking when suits them is expected.
        return True
    if next_step not in NO_MEETING_STEPS:
        return True
    latest = latest_owner_text or ''
    # Valuation/fee questions, a stated price, and a seller who already gave a
    # price or urgency are answered with an appointment offer rather than
    # another question.
    signals = seller_signals(latest)
    return bool(VISIT_ANSWERS_QUESTION.search(latest) or signals['strong'] or signals['target_price'])


# The owner refuses an in-person visit ("I don't want anyone coming to the house").
VISIT_REFUSAL = re.compile(
    r"\b(?:don['’]?t|do not|won['’]?t|not)\s+(?:want|need|like|allow|accept|do)\b[^.?!]{0,30}"
    r"\b(?:anyone|anybody|someone|people|visits?|visiting|com(?:e|ing) (?:by|over|out|to|in)|stop(?:ping)? by"
    r"|in[- ]person|walk-?throughs?|inside|showings?)\b"
    r"|\bno (?:visits?|walk-?throughs?|showings?|one (?:coming|in)|in[- ]person)\b",
    re.I,
)
# The owner also refuses calls or any meeting, so a phone alternative is not wanted either.
PHONE_REFUSAL = re.compile(
    r"\b(?:no|don['’]?t|do not|not|won['’]?t)\b[^.?!]{0,20}\b(?:calls?|calling|phone|talk|meet\w*)\b", re.I)


# Any owner wording that refuses, defers or limits a meeting, call or visit.
# Deliberately broad: it only decides whether a model's call_declined label is
# kept, so a false match keeps the safer "declined" state.
MEETING_REFUSAL = re.compile(
    r"\b(?:no|not|don['’]?t|do not|won['’]?t|will not|never|rather not|can['’]?t|cannot|stop)\b[^.?!]{0,30}"
    r"\b(?:calls?|calling|phone|talk(?:ing)?|meet\w*|visits?|visiting|com(?:e|ing)|stop(?:ping)? by|appointments?"
    r"|walk-?throughs?|in[- ]person|schedul\w*|see (?:the|my|our) (?:house|home|place|property))\b"
    r"|\bno,? thanks?\b|\bno thank you\b|\bnot interested\b|\b(?:just|only) (?:text|texting)\b|\btext only\b"
    r"|\b(?:keep it|stick) to text\b|\bnot (?:right )?now\b|\bnot yet\b|\bmaybe later\b|\bnot ready\b"
    r"|\btoo busy\b|\bleave me alone\b|\bpass\b",
    re.I,
)


def owner_declined_meeting(history: list | None = None) -> bool:
    """Whether any owner message in the thread refuses or defers a meeting, call or visit.

    A question, challenge or objection ("Why should I trust your approach?")
    is not a refusal.
    """
    for item in history or []:
        if item.get('direction') != 'inbound':
            continue
        text = QUOTED_TEXT.sub(' ', str(item.get('text') or ''))
        if MEETING_REFUSAL.search(text) or VISIT_REFUSAL.search(text):
            return True
    return False


def visit_only_refusal(text: str = '') -> bool:
    """The owner declined an in-person visit but not a phone conversation."""
    value = QUOTED_TEXT.sub(' ', str(text or ''))
    return bool(VISIT_REFUSAL.search(value)) and not PHONE_REFUSAL.search(value)


# The owner asks the automated thread to negotiate or to give legal, tax or
# financial advice. The AI declines by text and a human takes over.
ESCALATION_REQUEST = re.compile(
    r"\b(?:can|could|will|would)\s+(?:you|u)\b[^.?!]{0,30}\bnegotiat\w*"
    r"|\bnegotiat\w*\b[^.?!]{0,30}\b(?:for|on behalf of)\s+(?:me|us)\b"
    r"|\b(?:legal|tax|financial|investment)\s+advice\b"
    r"|\b(?:should|do)\s+i\s+(?:need|get|hire|talk to)\s+(?:a|an)\s+(?:lawyer|attorney|accountant|cpa)\b",
    re.I,
)


def escalation_reason(latest_owner_text: str = '') -> str:
    """'negotiation' or 'professional_advice' when a human must take the thread, else ''."""
    match = ESCALATION_REQUEST.search(QUOTED_TEXT.sub(' ', str(latest_owner_text or '')))
    if not match:
        return ''
    return 'negotiation' if 'negotiat' in match.group(0).lower() else 'professional_advice'


def premature_meeting_question(reply: str = '', next_step: str | None = None, latest_owner_text: str = '',
                               calendar_state: str = 'none') -> str:
    """The visit/meeting request or offer made before the disposition allows one, if any."""
    if calendar_state != 'call_declined' and meeting_offer_permitted(next_step, latest_owner_text, calendar_state):
        return ''
    question = next((question for question in _questions(reply)
                     if _question_intent(question) == 'scheduled_meeting'), '')
    if question:
        return question
    offer = MEETING_OFFER.search(clean_reply(reply))
    return offer.group(0) if offer else ''


def find_conversation_repetition(reply: str, history: list | None = None) -> str:
    """The agent must progress the conversation, never re-ask an answered question."""
    prior = [clean_reply(item.get('text') or '') for item in (history or [])
             if item.get('direction') == 'outbound']
    prior = [item for item in prior if item]
    current = clean_reply(reply)
    current_questions = _questions(current)
    for previous in prior:
        if current.lower() == previous.lower():
            ai_debug('REPEAT', 'repeated_reply: identical to an earlier agent message',
                     text=current, previous=previous)
            return 'repeated_reply'
        if len(_normalized_tokens(current)) >= 6 and similarity(current, previous) >= 0.86:
            ai_debug('REPEAT', 'repeated_reply: similarity >= 0.86 to an earlier agent message',
                     text=current, previous=previous, similarity=round(similarity(current, previous), 3))
            return 'repeated_reply'
        for current_question in current_questions:
            for previous_question in _questions(previous):
                intent = _question_intent(current_question)
                same_intent = bool(intent) and intent == _question_intent(previous_question)
                if (similarity(current_question, previous_question) >= 0.72
                        or (same_intent and intent in REPEAT_BY_INTENT)):
                    rule = ('question similarity >= 0.72'
                            if similarity(current_question, previous_question) >= 0.72
                            else f'both questions have the same {intent} intent')
                    ai_debug('REPEAT', f'repeated_question: {rule}', draft_question=current_question,
                             previous_question=previous_question,
                             similarity=round(similarity(current_question, previous_question), 3),
                             intent=intent or '')
                    return 'repeated_question'
    return ''


def remove_repeated_questions(reply: str, history: list | None = None) -> str:
    """The reply without the questions that repeat an earlier agent question.

    Used to repair a ``repeated_question`` block surgically: the rest of an
    otherwise useful answer is kept as written instead of being regenerated.
    """
    current = clean_reply(reply)
    prior = [question for item in (history or []) if item.get('direction') == 'outbound'
             for question in _questions(clean_reply(item.get('text') or ''))]
    kept = current
    for question in _questions(current):
        intent = _question_intent(question)
        if any(similarity(question, previous) >= 0.72
               or (intent in REPEAT_BY_INTENT and intent == _question_intent(previous)) for previous in prior):
            kept = kept.replace(question, ' ')
    return clean_reply(kept)


# Owner conversations are about a residential property, so any business or
# commercial experience is off-limits. "Laundry room" and "business days" are
# ordinary residential wording and are not matched.
BUSINESS_EXPERIENCE = re.compile(
    r"\b(?:laundromats?|coin[- ]?(?:op|laundr\w*)"
    r"|business(?:es)?\s+(?:sales?|brokerage|brokers?|owners?|acquisitions?|for sale|experience|background|deals?)"
    r"|(?:sold|sell(?:ing)?|bought|buy(?:ing)?|own(?:ed|ing)?|ran|run(?:ning)?)\s+(?:a |my |several |many |\d+\+?\s+)?"
    r"(?:small\s+)?business(?:es)?"
    r"|small[- ]business(?:es)?"
    r"|commercial\s+(?:real estate|propert\w*|sales?|deals?|buildings?|space|listings?|clients?)"
    r"|investment\s+propert\w*)\b",
    re.I,
)

EXPERIENCE_FALLBACK = ('Fair question. I don\u2019t want to overstate anything by text, so I won\u2019t quote sales '
                       'numbers I can\u2019t back up here. Would it help if I went over specific, verified examples '
                       'with you directly?')


def find_reply_policy_violations(reply: str, phone_source_known: bool | None = None,
                                 max_length: int | None = None, history: list | None = None,
                                 next_step: str | None = None,
                                 agent_name: str = '', calendar_state: str = 'none',
                                 allowed_phones: tuple = (), allowed_emails: tuple = (),
                                 tenure_published: bool = False) -> list:
    """Hard deterministic blocks only. Length is checked against the provider
    ceiling (``SMS_PROVIDER_MAX_CHARS``), never against a style target.

    ``allowed_phones`` (10-digit strings) and ``allowed_emails`` are the contact
    details the agent published on their Persona; only those may appear.
    ``tenure_published`` means the Persona states years of experience."""
    text = clean_reply(reply)
    violations = []
    if len(text) > (max_length or SMS_PROVIDER_MAX_CHARS):
        violations.append('over_provider_limit')
    if _false_human_claim(text, agent_name):
        violations.append('false_human_claim')
    if is_ai_identity_question(_latest_owner_text(history), agent_name):
        # A truthful answer must disclose automation; the system prompt supplies identity.
        if not AI_DISCLOSURE.search(text):
            violations.append('missing_ai_disclosure')
    elif IDENTITY_ASSISTANT.search(text) or _third_person_identity(text, agent_name):
        violations.append('identity_switch')
    allowed_email_set = {str(email).lower() for email in allowed_emails}
    if any(match.lower() not in allowed_email_set for match in EMAIL_ADDRESS.findall(text)):
        violations.append('invented_email')
    if any(re.sub(r'\D', '', match.group(0))[-10:] not in allowed_phones for match in PHONE_NUMBER.finditer(text)):
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
    if _unsupported_guarantee(text):
        violations.append('unsupported_guarantee')
    if PRECISE_TENURE.search(text) and not tenure_published:
        violations.append('unsupported_tenure')
    if BUSINESS_EXPERIENCE.search(text):
        violations.append('business_experience')
    if EXAMPLE_FACT_LEAK.search(text):
        violations.append('example_fact_leak')
    if (phone_source_known is False
            and PHONE_SOURCE_CLAIM_SUBJECT.search(text)
            and PHONE_SOURCE_CLAIM_ORIGIN.search(text)):
        violations.append('unsupported_phone_source')
    repetition = find_conversation_repetition(text, history or [])
    if repetition:
        violations.append(repetition)
    if premature_meeting_question(text, next_step, _latest_owner_text(history), calendar_state):
        violations.append('premature_meeting_request')
    return list(dict.fromkeys(violations))


# Questions about the resolved agent, as opposed to a property-sale timeline.
# Owners ask about the agent directly ("you") or in the third person ("she",
# "he", or by name; the name part is added per agent in _agent_question).
AGENT_TENURE_QUESTION = re.compile(
    r"\bhow long (?:has|have|had|did|was|were)\s+(?:you|the agent|she|he|her|him)\b[^?.!]{0,40}"
    r"\b(?:been|with|at|worked|working|in real estate|an? (?:agent|broker|realtor))\b",
    re.I,
)
AGENT_BACKGROUND_QUESTION = re.compile(
    r"\bwho(?:\s+is|['’]s)\s+(?:she|he)\b|\btell me about (?:yourself|your background|her|him)\b"
    r"|\b(?:which|what)\s+(?:brokerage|company|firm|office)\b"
    r"|\b(?:you|she|he)\b[^?.!]{0,40}\b(?:brokerage|company|firm|background|licensed|experience)\b",
    re.I,
)


def _agent_question(latest: str, agent_name: str = '') -> bool:
    """A question about the agent's identity, brokerage, background or tenure."""
    if AGENT_TENURE_QUESTION.search(latest) or AGENT_BACKGROUND_QUESTION.search(latest):
        return True
    name = _agent_name_pattern('' if agent_name == 'the agent' else agent_name)
    if not name:
        return False
    return bool(re.search(
        rf"\bwho(?:\s+is|['’]s)\s+{name}\b|\btell me about {name}\b"
        rf"|\bhow long (?:has|had|did|was)\s+{name}\b[^?.!]{{0,40}}"
        rf"\b(?:been|with|at|worked|working|in real estate|an? (?:agent|broker|realtor))\b"
        rf"|\b{name}\b[^?.!]{{0,40}}\b(?:brokerage|company|firm|background|licensed|experience)\b",
        latest, re.I))


def _agent_background_fallback(latest: str, brokerage_name: str = '') -> str:
    """Trusted account facts only, without invented history or tenure."""
    current = f'I’m with {brokerage_name} now' if brokerage_name else 'I don’t have a verified brokerage to share here'
    tenure = (' I don’t have a verified exact tenure to share, so I won’t guess.'
              if AGENT_TENURE_QUESTION.search(latest) or re.search(r'\bhow long\b', latest, re.I) else '')
    return f'{current}.{tenure}'


def safe_grounded_fallback(latest_inbound: str = '', violations: list | None = None,
                           history: list | None = None, plan: dict | None = None,
                           agent_name: str = 'the agent', brokerage_name: str = '') -> str:
    """Last-resort reply that never claims anything outside approved context."""
    violations = violations or []
    plan = plan or {}
    latest = str(latest_inbound or '')
    candidates = []

    if ('missing_ai_disclosure' in violations or 'false_human_claim' in violations
            or is_ai_identity_question(latest, '' if agent_name == 'the agent' else agent_name)):
        candidates.append(f'These replies are automated (AI) on {agent_name}’s behalf. '
                          'I can still help with questions about your property.')
    agent_question = _agent_question(latest, agent_name)
    from .knowledge import is_experience_question  # local import: knowledge loads the vector store
    if is_experience_question(latest):
        # Before seller-intent wording: "selling homes like mine" asks about the agent.
        candidates.append(EXPERIENCE_FALLBACK)
    elif agent_question:
        candidates.append(_agent_background_fallback(latest, brokerage_name))
    if re.search(r'\bguarantee', latest, re.I) or 'unsupported_guarantee' in violations:
        candidates.append('I can’t guarantee a sale price; buyers and the market set the final number. '
                          'I’d want to see the property and recent comparable sales before giving you a realistic view.')
    if visit_only_refusal(latest) and plan.get('calendar_state') != 'call_declined':
        candidates.append('Understood, no visit needed. We could do a quick phone call instead to talk through '
                          'your goals and the local market. Would that work for you?')
    signals = seller_signals(latest)
    if signals['strong'] and not visit_only_refusal(latest):
        goal = ' and '.join(part for part in (
            'you’re hoping to sell quickly' if signals['urgency'] else 'you’re looking to sell',
            f'are aiming for around {signals["target_price_text"]}' if signals['target_price'] else '',
        ) if part)
        permitted = meeting_offer_permitted(plan.get('next_step'), latest, plan.get('calendar_state') or 'none')
        if permitted:
            assessment = (' I’d suggest a quick visit to look at the property’s condition and review comparable '
                          'sales before recommending a listing price. When would be a convenient time?')
        elif signals['target_price']:
            assessment = (' I’d need to see the property and review comparable sales before recommending a '
                          'listing price.')
        else:
            assessment = ''
        candidates.append(f'Got it—{goal}.{assessment}')
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
    if (re.search(r'\b(?:timeline|timeframe|how long)\b', latest, re.I)
            and not agent_question):
        candidates.append('Timing depends on price, condition, and current demand, so I don’t have '
                          'verified data to promise a timeframe.')
    if re.search(r'\bnext step\b', latest, re.I):
        candidates.append('The next step would be a human review of the prior listing and current '
                          'market data; this chat can’t retrieve those records.')
    candidates.append('I’ve shared what I can verify here, and I don’t want to repeat myself or invent details.')

    for candidate in candidates:
        if not find_conversation_repetition(candidate, history or []):
            _debug_fallback_choice(candidate, candidates, latest, violations, plan)
            return candidate
        ai_debug('FALLBACK', 'candidate skipped because it was already sent', rule=_fallback_rule(candidate),
                 text=candidate)
    final = 'I don’t have anything new and verified to add, so I’ll leave it there.'
    _debug_fallback_choice(final, candidates, latest, violations, plan)
    return final


_FALLBACK_RULES = (
    ('These replies are automated', 'AI-identity question or disclosure violation'),
    ('Understood, no visit needed', 'owner declined a visit; phone alternative'),
    ('I’m with ', 'agent background/tenure question (approved brokerage)'),
    ('I’d rather not guess at my current brokerage', 'agent background/tenure question (no approved brokerage)'),
    ('I can’t guarantee a sale price', 'guarantee question or unsupported_guarantee'),
    ('Got it—you’re', 'seller intent with target price or urgency'),
    ('Thanks—that gives me enough', 'next_step=offer_call'),
    ('I have a property lead record', 'phone-source question or unsupported_phone_source'),
    ('I don’t have a verified callback number', 'callback question or invented_phone'),
    ('Fees and listing terms', 'fee/commission keyword or unsupported_fee'),
    ('This chat can’t retrieve verified comps', 'email/comps/report keyword or unsupported_delivery'),
    ('I don’t have a verified buyer', 'buyer keyword or unsupported_buyer'),
    ('I’d review the prior pricing', 'strategy/options keyword or example_fact_leak'),
    ('That expired-listing signal', 'why/reach-out keyword'),
    ('I’d compare the prior price', 'price/value/worth keyword'),
    ('Timing depends on price', 'timeline/timeframe/how-long keyword'),
    ('The next step would be', 'next-step keyword'),
    ('I’ve shared what I can verify', 'catch-all: no specific fallback rule matched'),
    ('I don’t have anything new', 'every candidate was already sent'),
)


def _fallback_rule(text: str) -> str:
    for prefix, rule in _FALLBACK_RULES:
        if text.startswith(prefix):
            return rule
    return 'unknown'


@ai_debug_safely
def _debug_fallback_choice(selected, candidates, latest, violations, plan) -> None:
    if ai_debug_enabled():
        ai_debug('FALLBACK', 'HARDCODED fallback selected', rule=_fallback_rule(selected), text=selected,
                 chars=len(selected), latest_owner_message=latest, violations=list(violations),
                 next_step=(plan or {}).get('next_step'),
                 candidates_in_order=[_fallback_rule(item) for item in candidates])


def explain_policy_violations(reply: str, violations: list, history: list | None = None,
                              max_length: int | None = None) -> dict:
    """Debug only: the text each violation matched. Does not affect the policy result."""
    text = clean_reply(reply)

    def found(pattern, value=text):
        match = pattern.search(value)
        return repr(match.group(0)) if match else 'no direct match'

    reasons = {}
    for violation in violations:
        if violation == 'over_provider_limit':
            reasons[violation] = f'{len(text)} chars > provider limit {max_length or SMS_PROVIDER_MAX_CHARS}'
        elif violation == 'unsupported_guarantee':
            reasons[violation] = repr(_unsupported_guarantee(text))
        elif violation == 'identity_switch':
            # The name-based patterns are built per agent in _third_person_identity;
            # this debug note only needs to say which kind of switch it was.
            assistant = IDENTITY_ASSISTANT.search(text)
            reasons[violation] = (f'assistant identity {assistant.group(0)!r}' if assistant
                                  else "third-person or assistant reference to the agent's own name")
        elif violation == 'false_human_claim':
            reasons[violation] = found(FALSE_HUMAN_CLAIM)
        elif violation == 'missing_ai_disclosure':
            reasons[violation] = 'owner asked if this is AI/a person; reply has no AI/automated wording'
        elif violation == 'invented_email':
            reasons[violation] = 'email address in reply'
        elif violation == 'invented_phone':
            match = PHONE_NUMBER.search(text)
            digits = re.sub(r'\D', '', match.group(0)) if match else ''
            reasons[violation] = f'phone number ending {digits[-4:]}' if digits else 'phone number in reply'
        elif violation == 'unsupported_delivery':
            reasons[violation] = found(DELIVERY_PROMISE)
        elif violation == 'unsupported_fee':
            reasons[violation] = found(UNSUPPORTED_FEE)
        elif violation == 'unsupported_buyer':
            reasons[violation] = found(UNSUPPORTED_BUYER, BUYER_DENIAL.sub(' ', text))
        elif violation == 'unsupported_production':
            reasons[violation] = found(UNSUPPORTED_PRODUCTION)
        elif violation == 'unsupported_local_experience':
            reasons[violation] = found(UNSUPPORTED_LOCAL_EXPERIENCE)
        elif violation == 'unsupported_outcome':
            reasons[violation] = found(UNSUPPORTED_OUTCOME)
        elif violation == 'unsupported_tenure':
            reasons[violation] = found(PRECISE_TENURE)
        elif violation == 'business_experience':
            reasons[violation] = found(BUSINESS_EXPERIENCE)
        elif violation == 'example_fact_leak':
            reasons[violation] = found(EXAMPLE_FACT_LEAK)
        elif violation == 'unsupported_phone_source':
            reasons[violation] = (f'{found(PHONE_SOURCE_CLAIM_SUBJECT)} + {found(PHONE_SOURCE_CLAIM_ORIGIN)} '
                                  'while the phone source is unknown')
        elif violation == 'premature_meeting_request':
            reasons[violation] = 'meeting/visit question before the disposition allows one'
        elif violation in ('repeated_reply', 'repeated_question'):
            reasons[violation] = 'see the [AI DEBUG][REPEAT] line above'
        else:
            reasons[violation] = 'see policy.py'
    return reasons
