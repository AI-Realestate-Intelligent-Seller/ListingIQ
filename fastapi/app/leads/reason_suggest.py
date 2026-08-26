"""Suggesting the one reason a whole campaign shares.

A campaign is usually not a random forty leads: it is the expired listings in
one area, or the probate records from one month. When the set has a story in
common, `{{reason}}` should say it once in the broker's own voice rather than
falling back to a generic per-signal sentence.

The signal counting here is ordinary arithmetic over imported data. DeepSeek is
only asked to phrase what that arithmetic already found — it is given the signal
labels and nothing else about the owners, and it may not introduce a fact. If it
is unconfigured or unreachable, the catalog wording below is used instead, so
the field always offers something and the composer never blocks on the network.
"""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from ..logger import get_logger
from ..models import Campaign, Lead, User
from ..sms import deepseek
from ..tenancy import brokerage_user_ids
from .catalog import SIGNALS
from .service import split_signals

logger = get_logger(__name__)

# A signal carried by at least this share of the set is what the batch is about.
DOMINANT_SHARE = 0.5

MAX_SUGGESTIONS = 3
MAX_REASON_CHARS = 300

# Written the way a broker actually opens: it names the public fact, then asks a
# question the owner can answer in one line. The per-lead wording in
# leads.service is deliberately drier — it feeds Bobbie's grounding, not the
# first text an owner reads.
_CATALOG_SUGGESTIONS: dict[str, list[str]] = {
    'expired': [
        'I see that your property is no longer listed, have you thought about '
        'putting it back on the market?',
        'I noticed the listing came off the market without a sale — are you '
        'still open to selling?',
    ],
    'fsbo': [
        'I see you are selling the property yourself, how is that going so far?',
        'I noticed the property is for sale by owner — would a second opinion '
        'on pricing be useful?',
    ],
    'pre_foreclosure': [
        'I came across a pre-foreclosure filing on the property, and I wanted to '
        'ask whether selling is something you have considered.',
        'I work with owners in this situation and wanted to ask if you have '
        'looked at your options yet.',
    ],
    'tax_delinquent': [
        'I came across an unpaid tax balance on the property, and wanted to ask '
        'whether selling has crossed your mind.',
    ],
    'probate': [
        'I see the property is going through probate, and I wanted to ask '
        'whether selling it is something the family is weighing.',
        'I work with families settling an estate and wanted to ask what you are '
        'planning to do with the property.',
    ],
    'divorce': [
        'I wanted to ask whether selling the property is something you are '
        'weighing at the moment.',
    ],
    'vacant': [
        'I noticed the property appears to be sitting empty, have you thought '
        'about what you would like to do with it?',
        'I see the property looks vacant — would you consider selling it?',
    ],
    'absentee_owner': [
        'I see you own the property but live elsewhere, have you thought about '
        'selling it?',
        'I work with a lot of out-of-area owners and wanted to ask whether '
        'selling is on your mind.',
    ],
    'high_equity': [
        'Values in the area have moved a good deal — have you thought about what '
        'the property would sell for today?',
    ],
}

_GENERIC_SUGGESTIONS = [
    'I came across the property while reviewing records in the area, and wanted '
    'to ask whether selling is something you have considered.',
    'I wanted to ask whether you have thought about putting the property on the '
    'market.',
]


def signal_profile(session: Session, user: User, campaign: Campaign) -> list[dict]:
    """How often each signal appears across the campaign's leads, commonest first."""
    leads = (session.query(Lead)
             .filter(Lead.user_id.in_(brokerage_user_ids(session, user)),
                     Lead.campaign_id == campaign.id)
             .all())
    total = len(leads)
    if not total:
        return []

    counts: dict[str, int] = {}
    for lead in leads:
        for key in split_signals(lead):
            counts[key] = counts.get(key, 0) + 1

    profile = [{
        'key': key,
        'label': SIGNALS[key][0] if key in SIGNALS else key,
        'count': count,
        'share': round(count / total, 3),
    } for key, count in counts.items()]
    profile.sort(key=lambda row: (-row['count'], row['key']))
    return profile


def _dominant(profile: list[dict]) -> list[dict]:
    """The signals the set is actually about, or the commonest one if none is."""
    shared = [row for row in profile if row['share'] >= DOMINANT_SHARE]
    return shared or profile[:1]


def _catalog_suggestions(dominant: list[dict]) -> list[str]:
    out: list[str] = []
    for row in dominant:
        for text in _CATALOG_SUGGESTIONS.get(row['key'], []):
            if text not in out:
                out.append(text)
    for text in _GENERIC_SUGGESTIONS:
        if len(out) >= MAX_SUGGESTIONS:
            break
        if text not in out:
            out.append(text)
    return out[:MAX_SUGGESTIONS]


def _prompt(dominant: list[dict], profile: list[dict], total: int, area: str) -> list[dict]:
    facts = '\n'.join(
        f"- {row['label']}: {row['count']} of {total} leads" for row in profile)
    headline = ', '.join(row['label'] for row in dominant)
    return [
        {
            'role': 'system',
            'content': (
                'You write the single sentence a real-estate broker uses to explain why '
                'they are texting a property owner out of the blue.\n'
                'Rules:\n'
                '1. Use ONLY the record types listed by the user. Never invent a fact, a '
                'number, a price, a date or a name.\n'
                '2. One sentence. Under 200 characters. Plain spoken, first person, no '
                'greeting and no sign-off — it is dropped into the middle of a text.\n'
                '3. State what the record shows, then ask one easy question about selling.\n'
                '4. Never promise a valuation, a buyer or a price.\n'
                'Reply with JSON only: {"suggestions": ["...", "...", "..."]}'
            ),
        },
        {
            'role': 'user',
            'content': (
                f'This campaign texts {total} property owners'
                f"{f' in {area}' if area else ''}.\n"
                f'What the records show across the set:\n{facts}\n\n'
                f'The set is mainly about: {headline}.\n'
                f'Write {MAX_SUGGESTIONS} alternative reasons.'
            ),
        },
    ]


def _parse(content: str) -> list[str]:
    """Pull the suggestion list out of the model's reply, however it wrapped it."""
    text = (content or '').strip()
    # Models commonly fence JSON even when told not to.
    fenced = re.search(r'```(?:json)?\s*(.*?)```', text, re.S)
    if fenced:
        text = fenced.group(1).strip()

    payload = None
    try:
        payload = json.loads(text)
    except ValueError:
        block = re.search(r'\{.*\}', text, re.S)
        if block:
            try:
                payload = json.loads(block.group(0))
            except ValueError:
                payload = None

    raw = []
    if isinstance(payload, dict):
        raw = payload.get('suggestions') or []
    elif isinstance(payload, list):
        raw = payload

    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        cleaned = ' '.join(item.split()).strip().strip('"')
        if cleaned and cleaned not in out:
            out.append(cleaned[:MAX_REASON_CHARS])
    return out[:MAX_SUGGESTIONS]


def suggest(session: Session, user: User, campaign: Campaign) -> dict:
    """Reason options for this campaign, plus the signal counts behind them."""
    profile = signal_profile(session, user, campaign)
    if not profile:
        return {'signals': [], 'suggestions': _GENERIC_SUGGESTIONS[:MAX_SUGGESTIONS],
                'source': 'catalog', 'note': 'No signals on these leads to go on.'}

    dominant = _dominant(profile)
    fallback = _catalog_suggestions(dominant)

    total = (session.query(Lead)
             .filter(Lead.user_id.in_(brokerage_user_ids(session, user)),
                     Lead.campaign_id == campaign.id)
             .count())
    area = (session.query(Lead.area)
            .filter(Lead.user_id.in_(brokerage_user_ids(session, user)),
                    Lead.campaign_id == campaign.id,
                    Lead.area.isnot(None), Lead.area != '')
            .limit(1).scalar()) or ''

    if not deepseek.is_configured():
        return {'signals': profile, 'suggestions': fallback, 'source': 'catalog',
                'note': 'DeepSeek is not configured, so these are the standard wordings.'}

    try:
        message = deepseek.completion(_prompt(dominant, profile, total, area),
                                      temperature=0.7)
        suggestions = _parse(message.get('content') or '')
    except deepseek.AiUnavailableError as error:
        logger.warning('Reason suggestions fell back to the catalog: %s', error)
        return {'signals': profile, 'suggestions': fallback, 'source': 'catalog',
                'note': 'DeepSeek could not be reached, so these are the standard wordings.'}

    if not suggestions:
        return {'signals': profile, 'suggestions': fallback, 'source': 'catalog',
                'note': 'DeepSeek returned nothing usable, so these are the standard wordings.'}

    # The catalog wording is kept on the end as a safe choice the broker can pick.
    for text in fallback:
        if len(suggestions) >= MAX_SUGGESTIONS + 1:
            break
        if text not in suggestions:
            suggestions.append(text)
    return {'signals': profile, 'suggestions': suggestions, 'source': 'ai', 'note': ''}
