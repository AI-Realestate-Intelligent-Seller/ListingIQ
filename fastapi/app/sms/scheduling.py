"""Scheduling output guards — port of scheduling-state.js.

These are output safety guards only. Conversation intent and calendar state are
decided by the configured AI provider's structured response in bobbie.py.
"""

import re

SCHEDULING_PRESSURE = re.compile(
    r'\b(?:schedule|book|appointment|what time works|when (?:can|could|would) (?:we|you)'
    r'|quick (?:call|conversation|chat|visit|look)|phone call|walk-?through|in person'
    r'|(?:stop|come|swing|drop) by|i can do .{0,60}(?:a\.?m\.?|p\.?m\.?))\b'
    r"|\b(?:would you|could we|can we|want to|prefer to|let['’]?s)\b[^.!?]{0,35}\b(?:call|talk|meet|speak|visit)\b",
    re.I,
)
TIME_OF_DAY = re.compile(r'\b(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)\b', re.I)
WEEKDAY = re.compile(r'\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', re.I)
AVAILABILITY_PHRASING = re.compile(
    r'\b(?:i can do|i have|available|open|would .{0,20} work|does .{0,20} work|tomorrow at|today at)\b', re.I
)
# "Confirmed" counts only as a booking claim ("you're confirmed", "your visit is
# confirmed"); biography wording such as "my tenure isn't confirmed" is not one.
UNBOOKED_CONFIRMATION = re.compile(
    r"\b(?:you(?:['’]re| are) all set"
    r"|(?:you|we|it|that|everything|all)(?:['’]re|['’]s| are| is)\s+(?:now\s+|all\s+)?confirmed"
    r"|(?:appointment|visit|meeting|call|walk-?through|showing|time|slot|booking|reservation)s?\s+"
    r"(?:is\s+|are\s+|has\s+been\s+|have\s+been\s+)?confirmed"
    r"|i(?:['’]ve| have) confirmed|confirmed\s+for|booked"
    r"|i(?:['’]ll| will) (?:book|confirm|reserve|schedule|set (?:it|that) up|put you down"
    r"|get (?:it|that) booked|give you a call|call you|ring you)"
    r"|see you (?:at|then|there)|talk (?:to you )?(?:then|at)|looking forward to (?:it|our call|our visit|meeting you|seeing))\b",
    re.I,
)


def contains_scheduling_pressure(text: str = '') -> bool:
    return bool(SCHEDULING_PRESSURE.search(str(text or '')))


def contains_time_proposal(text: str = '') -> bool:
    value = str(text or '')
    return bool((TIME_OF_DAY.search(value) or WEEKDAY.search(value)) and AVAILABILITY_PHRASING.search(value))


def contains_unbooked_confirmation(text: str = '') -> bool:
    return bool(UNBOOKED_CONFIRMATION.search(str(text or '')))


# The owner names a day, part of day or time window ("tomorrow afternoon", "Thursday after 5pm").
AVAILABILITY_TEXT = re.compile(
    r'\b(?:today|tonight|tomorrow|tmrw|this (?:week|weekend|morning|afternoon|evening)|next (?:week|weekend|\w+day)'
    r'|weekends?|weekdays?|mornings?|afternoons?|evenings?|noon|anytime|any time|after work|after \d'
    r'|monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tues?|wed|thur?s?|fri|sat|sun)\b'
    r'|\b(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)|\b(?:[01]?\d|2[0-3]):[0-5]\d\b',
    re.I,
)
_TRAILING_ACCEPTANCE = re.compile(
    r'[\s,]*(?:(?:would|will|should|could)?\s*(?:works?|work for me|is (?:good|fine|great|best|perfect))'
    r'|sounds? (?:good|great)|(?:is )?ok(?:ay)?)?\s*(?:for me)?[\s.!?]*$', re.I)


def mentions_availability(text: str = '') -> bool:
    return bool(AVAILABILITY_TEXT.search(str(text or '')))


def preferred_availability(requested_text: str = '', latest_owner_text: str = '') -> str:
    """The owner's own availability words, trimmed for an SMS acknowledgment ('' when none)."""
    for candidate in (requested_text, latest_owner_text):
        value = ' '.join(str(candidate or '').split())
        if not mentions_availability(value):
            continue
        value = _TRAILING_ACCEPTANCE.sub('', value).strip(' ,.;:!?"\'')
        value = re.sub(r'^(?:yes|yeah|yep|sure|ok(?:ay)?|great|perfect|sounds good)[,!.\s]+', '', value, flags=re.I)
        value = re.sub(r'^(?:an?\s+)?(?:phone\s+)?(?:call|visit|walk-?through)\s+(?:on\s+)?', '', value, flags=re.I)
        if value and len(value) <= 60 and mentions_availability(value):
            first = value.split()[0]
            if not re.match(r'(?:mon|tue|wed|thu|fri|sat|sun)', first, re.I):
                value = value[0].lower() + value[1:]
            return value
    return ''


MEETING_WORDS = re.compile(
    r'\b(?:visit|stop(?:ping)? by|come by|swing by|drop by|walk-?through|take a (?:quick )?look|meet(?:ing)?'
    r'|in person|(?:phone )?call|consultation)\b', re.I)
DECLINE_OR_DEFER = re.compile(
    r"\b(?:no|not|nope|can['’]?t|cannot|won['’]?t|doesn['’]?t|don['’]?t|busy|unavailable|never|maybe|later"
    r"|not sure|stop|unsubscribe)\b", re.I)


def invites_meeting_time(agent_text: str = '') -> bool:
    """The agent's message invited a visit/call ("When would be convenient?", "Would that work?")."""
    value = str(agent_text or '')
    return bool(MEETING_WORDS.search(value)) and '?' in value


def answers_meeting_invitation_with_time(previous_agent_text: str = '', owner_text: str = '') -> bool:
    """A plain availability answer ("Tomorrow afternoon works") to the agent's meeting invitation."""
    return (invites_meeting_time(previous_agent_text) and mentions_availability(owner_text)
            and not DECLINE_OR_DEFER.search(str(owner_text or '')))
