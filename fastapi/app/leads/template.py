"""The campaign message template — one piece of copy, many recipients.

A broker writes the opening text once, with `{{token}}` placeholders for the
parts that differ per lead. Rendering fills them from the lead record and the
broker's own profile, so this stays factual: every token resolves to something
already imported or already on the account, never to invented copy.

Unknown tokens are a hard error rather than a silent blank. A campaign that
sends "reaching out about {{property}}" to two hundred owners is worse than one
that refuses to send, so the mistake is caught while the draft is being written.
"""

from __future__ import annotations

import re

from ..models import Lead, User
from .service import outreach_reason

TOKEN_PATTERN = re.compile(r'\{\{\s*([A-Za-z_]+)\s*\}\}')

# Shown beside the composer so the broker knows what each token becomes.
TOKEN_HELP: dict[str, str] = {
    'first_name': 'The owner’s first name, or “there” when the import has no name.',
    'owner_name': 'The owner’s full name as imported.',
    'address': 'The property address this lead is about.',
    'area': 'The neighbourhood or city from the import, if there is one.',
    'reason': 'Why this owner is being contacted, from the lead’s own signal.',
    'brokerage': 'Your brokerage name.',
    'agent_name': 'Your full name.',
}

DEFAULT_TEMPLATE = (
    'Hi {{first_name}}, this is the team lead at {{brokerage}}, I’m reaching out '
    'about {{address}}. {{reason}}'
)

# Two SMS segments of GSM-7. Past this a campaign starts costing three messages
# per lead, which is worth saying out loud before four hundred of them go out.
LONG_MESSAGE_CHARS = 320


def unknown_tokens(template: str) -> list[str]:
    """Tokens in the template that nothing can fill, in the order written."""
    seen: list[str] = []
    for name in TOKEN_PATTERN.findall(template or ''):
        key = name.lower()
        if key not in TOKEN_HELP and key not in seen:
            seen.append(key)
    return seen


def validate(template: str) -> None:
    """Raise ValueError with a sentence the composer can show as-is."""
    text = (template or '').strip()
    if not text:
        raise ValueError('Write the message this campaign should send.')
    if len(text) > 1600:
        raise ValueError('The message is too long to send as an SMS. Keep it under 1600 characters.')
    unknown = unknown_tokens(text)
    if unknown:
        known = ', '.join(f'{{{{{name}}}}}' for name in sorted(TOKEN_HELP))
        wrote = ', '.join(f'{{{{{name}}}}}' for name in unknown)
        raise ValueError(f'{wrote} is not a placeholder I can fill. Available: {known}.')


def _ends_sentence(value: str) -> bool:
    return bool(value) and value[-1] in '.?!'


def values_for(lead: Lead, user: User, reason_override: str = '') -> dict[str, str]:
    """What each token resolves to for one lead."""
    owner = (lead.owner_name or '').strip()
    reason = outreach_reason(lead, reason_override).strip()
    return {
        'first_name': owner.split()[0] if owner else 'there',
        'owner_name': owner or 'there',
        'address': (lead.property_address or '').strip(),
        'area': (lead.area or '').strip(),
        # The reason is a sentence in its own right wherever it lands in the
        # copy, so it carries its own full stop.
        'reason': reason if _ends_sentence(reason) else f'{reason}.',
        'brokerage': (user.brokerage_name or '').strip() or 'our team',
        'agent_name': (user.full_name or '').strip() or 'your agent',
    }


def render(template: str, lead: Lead, user: User, reason_override: str = '') -> str:
    """Fill the template for one lead. Assumes `validate` has already passed."""
    values = values_for(lead, user, reason_override)
    filled = TOKEN_PATTERN.sub(lambda match: values.get(match.group(1).lower(), ''), template or '')
    # A blank token (an import with no area, say) would otherwise leave a double
    # space or a stranded comma where it used to be.
    filled = re.sub(r'[ \t]+', ' ', filled)
    filled = re.sub(r'\s+([,.?!])', r'\1', filled)
    return filled.strip()
