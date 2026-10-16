"""Telnyx Number Lookup gate for live campaign outreach.

Simulation remains fully local. In live Telnyx mode, a destination must have an
explicit ``mobile`` line type before campaign SMS is allowed to proceed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from ..core.config import settings
from ..logger import get_logger, log_event


logger = get_logger(__name__)

TELNYX_NUMBER_LOOKUP_URL = 'https://api.telnyx.com/v2/number_lookup'


@dataclass(frozen=True)
class NumberLookupResult:
    phone_number: str
    status: str
    line_type: str | None = None
    carrier_name: str | None = None
    country_code: str | None = None
    national_format: str | None = None
    checked_at: str | None = None
    error_code: str | None = None

    @property
    def is_mobile(self) -> bool:
        return self.status == 'verified' and self.line_type == 'mobile'

    @property
    def enforces_mobile_gate(self) -> bool:
        return self.status != 'simulation_bypass'

    def saved_metadata(self) -> dict | None:
        """The safe subset persisted with the lead after the batch finishes."""
        if not self.enforces_mobile_gate:
            return None
        return {
            'provider': 'telnyx',
            'status': self.status,
            'phone_number': self.phone_number,
            'line_type': self.line_type,
            'sms_eligible': self.is_mobile,
            'carrier_name': self.carrier_name,
            'country_code': self.country_code,
            'national_format': self.national_format,
            'checked_at': self.checked_at,
            'error_code': self.error_code,
        }


def _suffix(value: str) -> str:
    digits = ''.join(character for character in value if character.isdigit())
    return digits[-4:] if digits else 'unknown'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _error(phone_number: str, code: str) -> NumberLookupResult:
    log_event(
        logger, 'sms.number_lookup.failed',
        destination_suffix=_suffix(phone_number), error_code=code,
    )
    return NumberLookupResult(
        phone_number=phone_number,
        status='error',
        checked_at=_now(),
        error_code=code,
    )


def lookup_number(phone_number: str) -> NumberLookupResult:
    """Look up one number without ever raising a batch-stopping provider error."""
    mode = str(settings['telnyx'].get('mode') or 'simulation').lower()
    if mode != 'telnyx':
        # Campaign tests and local simulation must never spend lookup requests.
        return NumberLookupResult(phone_number=phone_number, status='simulation_bypass')

    api_key = settings['telnyx'].get('api_key')
    if not api_key:
        return _error(phone_number, 'missing_api_key')

    url = f"{TELNYX_NUMBER_LOOKUP_URL}/{quote(phone_number, safe='+')}"
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
    }
    log_event(
        logger, 'sms.number_lookup.started',
        destination_suffix=_suffix(phone_number),
    )
    try:
        response = requests.get(url, headers=headers, timeout=10)
    except requests.RequestException:
        return _error(phone_number, 'request_failed')

    try:
        body = response.json()
    except ValueError:
        return _error(phone_number, 'invalid_json')
    if not response.ok:
        errors = body.get('errors') if isinstance(body, dict) else None
        first = errors[0] if isinstance(errors, list) and errors else {}
        code = first.get('code') if isinstance(first, dict) else None
        return _error(phone_number, str(code or f'http_{response.status_code}'))

    data = body.get('data') if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return _error(phone_number, 'missing_data')
    portability = data.get('portability') if isinstance(data.get('portability'), dict) else {}
    carrier = data.get('carrier') if isinstance(data.get('carrier'), dict) else {}
    raw_line_type = portability.get('line_type') or carrier.get('type')
    line_type = str(raw_line_type).strip().lower() if raw_line_type else None
    result = NumberLookupResult(
        phone_number=str(data.get('phone_number') or phone_number),
        status='verified' if line_type else 'unknown',
        line_type=line_type,
        carrier_name=str(carrier.get('name')).strip() if carrier.get('name') else None,
        country_code=str(data.get('country_code')).strip() if data.get('country_code') else None,
        national_format=(
            str(data.get('national_format')).strip() if data.get('national_format') else None
        ),
        checked_at=_now(),
        error_code=None,
    )
    log_event(
        logger, 'sms.number_lookup.completed',
        destination_suffix=_suffix(phone_number),
        lookup_status=result.status,
        line_type=result.line_type,
        mobile=result.is_mobile,
    )
    return result
