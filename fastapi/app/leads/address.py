"""Deciding when two written addresses are the same property.

The cross-brokerage claim is on the property, so "415 Northview Lane, Hoffman
Estates, IL, United States, 60169" and "415 Northview Ln, Hoffman Estates IL
60169" have to resolve to one key. Vendors format addresses however they like,
and a lock that only fires on an exact string match is a lock that never fires.

This is deliberately conservative. Two failure modes are not symmetric:

- Missing a match lets two brokerages text one owner, which is the situation
  that existed before any of this and which a broker can see and sort out.
- A false match silently blocks a brokerage from a property they have every
  right to work, and nobody can tell why.

So this only collapses differences that are certainly noise — case, punctuation,
spacing, the standard USPS suffix and directional abbreviations, and a trailing
country. It never guesses at missing components: an address with no city does
not match the same street with one.
"""

from __future__ import annotations

import re

# USPS C1 suffixes, written form -> canonical. Only unambiguous ones: "st" is
# left alone because expanding it guesses between Street and Saint.
_SUFFIXES = {
    'street': 'st', 'str': 'st',
    'avenue': 'ave', 'av': 'ave', 'aven': 'ave',
    'road': 'rd',
    'drive': 'dr', 'driv': 'dr',
    'lane': 'ln',
    'court': 'ct',
    'place': 'pl',
    'boulevard': 'blvd', 'boul': 'blvd', 'boulv': 'blvd',
    'circle': 'cir', 'circl': 'cir',
    'terrace': 'ter', 'terr': 'ter',
    'parkway': 'pkwy', 'parkwy': 'pkwy',
    'highway': 'hwy', 'highwy': 'hwy',
    'square': 'sq',
    'trail': 'trl',
    'crossing': 'xing',
    'heights': 'hts',
    'junction': 'jct',
    'landing': 'lndg',
    'meadows': 'mdws',
    'ridge': 'rdg',
    'trafficway': 'trfy',
    'turnpike': 'tpke',
    'valley': 'vly',
    'village': 'vlg',
    'expressway': 'expy',
    'freeway': 'fwy',
    'gardens': 'gdns',
    'harbor': 'hbr',
    'island': 'is',
    'mountain': 'mtn',
    'plaza': 'plz',
    'point': 'pt',
    'station': 'sta',
}

_DIRECTIONS = {
    'north': 'n', 'south': 's', 'east': 'e', 'west': 'w',
    'northeast': 'ne', 'northwest': 'nw', 'southeast': 'se', 'southwest': 'sw',
}

_UNITS = {
    'apartment': 'apt', 'apartments': 'apt',
    'suite': 'ste',
    'building': 'bldg',
    'floor': 'fl',
    'department': 'dept',
    'number': 'no',
}

# Dropped outright: they carry no distinguishing information for a US pool.
_NOISE = {'usa', 'us', 'united', 'states', 'unitedstates', 'america'}

_WORD_MAP = {**_SUFFIXES, **_DIRECTIONS, **_UNITS}


def canonical(address: str | None) -> str:
    """A comparison key for one property, or '' when there is nothing to compare.

    Returning '' for a blank or unusable address matters: an empty key must
    never match another empty key, so callers treat '' as "no claim possible"
    rather than as a property every blank address shares.
    """
    text = (address or '').strip().lower()
    if not text:
        return ''

    # '#' carries meaning ("apt #4"), so it becomes a word rather than vanishing.
    text = text.replace('#', ' no ')
    text = re.sub(r'[^a-z0-9]+', ' ', text)

    words: list[str] = []
    for word in text.split():
        if word in _NOISE:
            continue
        words.append(_WORD_MAP.get(word, word))

    # "united states" also appears as one token after punctuation stripping.
    if not words:
        return ''
    return ' '.join(words)


def same_property(left: str | None, right: str | None) -> bool:
    """True only when both addresses are usable and resolve to one key."""
    key = canonical(left)
    return bool(key) and key == canonical(right)
