"""Signals and stages — the vocabulary of the lead pool.

Signals describe *why* a property is worth a conversation (it is for sale by
owner, the listing expired, taxes are delinquent…). They come from the imported
data. Stages describe *where the lead stands with us* and are always derived by
the system, never typed in by a broker.
"""

from __future__ import annotations

import re

# key -> (label, score weight). The weight only orders the pool for review; it
# is not a prediction, and the product treats score as advisory.
SIGNALS: dict[str, tuple[str, int]] = {
    'fsbo': ('FSBO', 16),
    'expired': ('Expired', 18),
    'pre_foreclosure': ('Pre-Foreclosure', 20),
    'tax_delinquent': ('Tax Delinquent', 16),
    'probate': ('Probate', 14),
    'divorce': ('Divorce', 14),
    'vacant': ('Vacant Property', 12),
    'absentee_owner': ('Absentee Owner', 12),
    'high_equity': ('High Equity', 10),
}

# Everything a CSV might call a signal, mapped onto the canonical key above.
_SIGNAL_ALIASES: dict[str, str] = {
    'fsbo': 'fsbo',
    'for sale by owner': 'fsbo',
    'forsalebyowner': 'fsbo',
    'expired': 'expired',
    'expired listing': 'expired',
    'expired listings': 'expired',
    'pre foreclosure': 'pre_foreclosure',
    'preforeclosure': 'pre_foreclosure',
    'pre-foreclosure': 'pre_foreclosure',
    'foreclosure': 'pre_foreclosure',
    'notice of default': 'pre_foreclosure',
    'nod': 'pre_foreclosure',
    'tax delinquent': 'tax_delinquent',
    'taxdelinquent': 'tax_delinquent',
    'delinquent taxes': 'tax_delinquent',
    'tax lien': 'tax_delinquent',
    'probate': 'probate',
    'inherited': 'probate',
    'divorce': 'divorce',
    'divorced': 'divorce',
    'vacant': 'vacant',
    'vacant property': 'vacant',
    'vacancy': 'vacant',
    'absentee': 'absentee_owner',
    'absentee owner': 'absentee_owner',
    'out of state owner': 'absentee_owner',
    'out-of-state': 'absentee_owner',
    'high equity': 'high_equity',
    'highequity': 'high_equity',
    'equity': 'high_equity',
}

# Stages, in the order the UI shows them. Derived, in this precedence.
STAGES: dict[str, str] = {
    'dnc': 'Verified DNC',
    'in_campaign': 'In campaign',
    'needs_review': 'Needs review',
    'new': 'New / Refreshed',
    'ready': 'Ready for Outreach',
}


def normalize_signal(value: str) -> str | None:
    """Map one raw CSV token onto a canonical signal key, or None if unknown."""
    token = re.sub(r'[\s_\-/]+', ' ', str(value or '').strip().lower())
    if not token:
        return None
    if token in _SIGNAL_ALIASES:
        return _SIGNAL_ALIASES[token]
    compact = token.replace(' ', '_')
    return compact if compact in SIGNALS else None


def parse_signals(value: str) -> list[str]:
    """Split a free-text signal cell ("FSBO; high equity") into canonical keys."""
    parts = re.split(r'[,;|]+', str(value or ''))
    found: list[str] = []
    for part in parts:
        key = normalize_signal(part)
        if key and key not in found:
            found.append(key)
    return found


def signal_label(key: str) -> str:
    return SIGNALS.get(key, (key.replace('_', ' ').title(), 0))[0]


def catalog() -> list[dict]:
    """The signal vocabulary, for the filter chips."""
    return [{'key': key, 'label': label} for key, (label, _weight) in SIGNALS.items()]


def stage_catalog() -> list[dict]:
    return [{'key': key, 'label': label} for key, label in STAGES.items()]
