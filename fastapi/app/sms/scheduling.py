"""Scheduling output guards — port of scheduling-state.js.

These are output safety guards only. Conversation intent and calendar state are
decided by DeepSeek's structured disposition response in bobbie.py.
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
UNBOOKED_CONFIRMATION = re.compile(
    r"\b(?:you(?:['’]re| are) all set|confirmed|booked"
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
