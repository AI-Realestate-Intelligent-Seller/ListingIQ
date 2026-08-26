"""Reading city, state and ZIP out of an address that was never split up.

A lead carries one free-text `property_address` — `"2800 Pine Ave, Mattoon, IL,
61938"` — and, when the vendor happened to supply the column, an `area`. There
are no location columns to filter on, so the location filters read the parts
back out of that string on the way past.

This is worth doing rather than matching substrings because a substring cannot
tell a state from a street ("Pine Ave, Washington, IL" contains "Washington"
twice, once as a town and once as nothing of the sort), and because a filter
menu needs to know that 61938 *is* Mattoon in order to offer them together.

Two ideas carry the whole module:

- **Read right to left.** The tail of a US address is the reliable part: a ZIP
  is unmistakable, a state is a closed set of 50-odd names. Whatever is left
  immediately before them is the locality. Nothing has to decide whether that
  locality is a city, a town, a village or a county — it never matters, because
  the state and ZIP on the same row are what anchor it.

- **Never guess.** Following app.leads.address: an address with no ZIP gets no
  ZIP, not the ZIP of a town that looks similar. A location filter that quietly
  invents a fact hides leads the broker has paid for.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import NamedTuple

# Written form -> USPS code. Territories are included because vendor lists carry
# them; anything not in here is simply not recognised as a state.
_STATE_NAMES = {
    'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR',
    'california': 'CA', 'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE',
    'florida': 'FL', 'georgia': 'GA', 'hawaii': 'HI', 'idaho': 'ID',
    'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA', 'kansas': 'KS',
    'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
    'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN',
    'mississippi': 'MS', 'missouri': 'MO', 'montana': 'MT', 'nebraska': 'NE',
    'nevada': 'NV', 'new hampshire': 'NH', 'new jersey': 'NJ',
    'new mexico': 'NM', 'new york': 'NY', 'north carolina': 'NC',
    'north dakota': 'ND', 'ohio': 'OH', 'oklahoma': 'OK', 'oregon': 'OR',
    'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
    'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT',
    'vermont': 'VT', 'virginia': 'VA', 'washington': 'WA',
    'west virginia': 'WV', 'wisconsin': 'WI', 'wyoming': 'WY',
    'district of columbia': 'DC', 'puerto rico': 'PR', 'virgin islands': 'VI',
    'guam': 'GU', 'american samoa': 'AS', 'northern mariana islands': 'MP',
}

STATE_LABELS = {code: name.title() for name, code in _STATE_NAMES.items()}
STATE_LABELS['DC'] = 'District of Columbia'

_STATE_CODES = set(_STATE_NAMES.values())

# ZIP or ZIP+4. Only the five-digit prefix is kept: the +4 is a delivery route,
# not a place, and splitting a town across it would be noise in the menu.
_ZIP = re.compile(r'^(\d{5})(?:-\d{4})?$')

# Dropped before parsing; they sit exactly where a state would and would
# otherwise be read as one. Mirrors the noise list in app.leads.address.
_COUNTRY = {'usa', 'us', 'u s a', 'united states', 'united states of america', 'america'}

# Words that describe a locality rather than name it. "Village of Oak Park" and
# "Oak Park" are one place; "Coles County" and "Coles" are not necessarily, so
# only the leading form is dropped.
_LOCALITY_PREFIX = re.compile(r'^(?:city|town|village|township|borough|municipality)\s+of\s+', re.I)

# "Saint"/"Sainte" are written both ways by every vendor; folding them keeps
# St. Charles from splitting into three towns in the filter menu.
_KEY_WORDS = {'saint': 'st', 'sainte': 'ste', 'mount': 'mt', 'fort': 'ft'}


class Location(NamedTuple):
    """What one address says about where it is. Any field may be blank."""
    city: str
    """Display form, as written on the lead: "Hoffman Estates"."""
    city_key: str
    """Match form: lower-cased, punctuation dropped, Saint/Mount folded."""
    state: str
    """Two-letter USPS code, or '' when the address does not name one."""
    postal: str
    """Five-digit ZIP, or '' when the address does not carry one."""

    @property
    def city_id(self) -> str:
        """Identifies a town across states — Springfield IL is not Springfield MO.

        Blank when there is no city, so that callers can test it as a boolean
        rather than carrying a separate "did this parse" flag.
        """
        return f'{self.city_key}|{self.state}' if self.city_key else ''

    @property
    def city_label(self) -> str:
        return f'{self.city}, {self.state}' if self.city and self.state else self.city


EMPTY = Location('', '', '', '')


def city_key(name: str | None) -> str:
    """The match form of a locality name, or '' if there is nothing to match."""
    text = _LOCALITY_PREFIX.sub('', (name or '').strip())
    text = re.sub(r'[^a-z0-9]+', ' ', text.lower()).strip()
    if not text:
        return ''
    return ' '.join(_KEY_WORDS.get(word, word) for word in text.split())


def _state_from(token: str) -> str:
    """The USPS code a token names, or '' if it names no state."""
    text = token.strip()
    if len(text) == 2 and text.upper() in _STATE_CODES:
        return text.upper()
    return _STATE_NAMES.get(re.sub(r'[^a-z ]+', '', text.lower()).strip(), '')


def _split(address: str) -> list[str]:
    """Comma-separated parts, with country noise and empties removed."""
    parts = []
    for part in address.split(','):
        cleaned = part.strip().strip('.')
        if not cleaned or re.sub(r'[^a-z ]+', '', cleaned.lower()).strip() in _COUNTRY:
            continue
        parts.append(cleaned)
    return parts


def _take_postal(parts: list[str]) -> str:
    """Pull a ZIP off the end of the address, editing `parts` in place."""
    if not parts:
        return ''
    words = parts[-1].split()
    match = _ZIP.match(words[-1]) if words else None
    if not match:
        return ''
    words.pop()
    if words:
        parts[-1] = ' '.join(words)
    else:
        parts.pop()
    return match.group(1)


def _take_state(parts: list[str]) -> str:
    """Pull a state off the end, editing `parts` in place.

    Tries the two-word names ("New York") before the one-word ones, so that
    "York" inside "New York" is never read as the whole of it.
    """
    if not parts:
        return ''
    words = parts[-1].split()
    for size in (3, 2, 1):
        if len(words) < size:
            continue
        state = _state_from(' '.join(words[-size:]))
        if not state:
            continue
        # A lone part that is nothing but the state name, or the tail of one.
        remaining = words[:-size]
        if remaining:
            parts[-1] = ' '.join(remaining)
        else:
            parts.pop()
        return state
    return ''


@lru_cache(maxsize=20000)
def parse(address: str | None, area: str | None = None) -> Location:
    """Where one lead is, as far as its own address is willing to say.

    Cached because the pool is filtered on every keystroke and the same few
    thousand address strings are re-read each time; the cache is keyed on the
    strings themselves, so it stays correct across brokerages and imports.
    """
    parts = _split(address or '')
    postal = _take_postal(parts)
    state = _take_state(parts)
    if not postal:
        # "Mattoon IL 61938" and "Mattoon 61938 IL" both occur in the wild.
        postal = _take_postal(parts)

    # The first part is the street. A locality needs something in front of it,
    # otherwise "2800 Pine Ave" would be read as a town called Pine Ave.
    city = parts[-1] if len(parts) > 1 else ''
    if not city:
        city = (area or '').strip()
    city = _LOCALITY_PREFIX.sub('', city).strip()

    key = city_key(city)
    if not key:
        return Location('', '', state, postal)
    return Location(city, key, state, postal)
