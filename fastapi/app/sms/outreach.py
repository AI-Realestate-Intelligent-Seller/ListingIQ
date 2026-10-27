"""Initial outreach copy and approved lead context — port of outreach.js."""

import re

from ..core.config import settings


def outreach_signature() -> str:
    """Bobbie's name, plus her brokerage only once the business has approved it."""
    brokerage = (settings.get('sms') or {}).get('approved_brokerage') or ''
    return f'Bobbie Fisher – {brokerage}' if brokerage else 'Bobbie Fisher'


def _clean_sentence(value: str = '') -> str:
    return re.sub(r'[\s.?!]+$', '', str(value or '').strip())


def build_single_lead_context(property_address: str, outreach_reason: str) -> dict:
    return {
        'available': True,
        'property_address': str(property_address).strip(),
        'lead_source': 'Manual single conversation',
        'outreach_reason': str(outreach_reason).strip(),
        'phone_number_source': {
            'available': False,
            'safe_response': 'The manually entered lead does not state how the phone number was sourced.',
        },
        'property_details': {},
    }


def build_initial_outreach(name: str, property_address: str, outreach_reason: str) -> str:
    first_name = str(name).strip().split()[0] if str(name).strip() else ''
    address = _clean_sentence(property_address)
    reason = _clean_sentence(outreach_reason)
    return (f'Hey {first_name}, I’m reaching out about {address}. {reason}. '
            f'Would you be open to a brief conversation? {outreach_signature()}')
