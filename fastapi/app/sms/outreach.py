"""Initial outreach copy and approved lead context — port of outreach.js."""

import re


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
            'Would you be open to a brief conversation? Bobbie Fisher – RE/MAX')
