"""Calendar access for the Bobbie pipeline.

By default the calendar runs in-process (calendar_service). Setting
CALENDAR_API_URL delegates to the standalone Simulation/calendar.py service
instead, keeping the original HTTP contract.
"""

import re

import requests

from ..core.config import settings
from ..logger import get_logger, log_event
from . import calendar_service

logger = get_logger(__name__)


class SlotTakenError(RuntimeError):
    """The requested slot was booked by someone else (HTTP 409 / overlap)."""


def _base_url() -> str:
    return settings['sms']['calendar_url']


def fetch_availability(session=None, user_id: int | None = None) -> dict:
    source = 'http' if _base_url() else 'in_process'
    log_event(logger, 'sms.calendar.availability.started', user_id=user_id, source=source)
    if not _base_url():
        payload = calendar_service.availability(session, user_id)
        log_event(logger, 'sms.calendar.availability.completed', user_id=user_id,
                  source=source, slot_count=len(payload.get('slots') or []),
                  timezone=payload.get('timezone'))
        return payload

    response = requests.get(f'{_base_url()}/api/availability', timeout=7)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not response.ok:
        raise RuntimeError(payload.get('error') or f'Calendar returned HTTP {response.status_code}')
    log_event(logger, 'sms.calendar.availability.completed', user_id=user_id,
              source=source, slot_count=len(payload.get('slots') or []),
              timezone=payload.get('timezone'))
    return payload


def create_booking(conversation, slot: dict, session=None) -> dict:
    log_event(logger, 'sms.calendar.booking.started', conversation_id=getattr(conversation, 'id', None),
              user_id=conversation.user_id, source='http' if _base_url() else 'in_process',
              start_at=slot.get('start_at'), end_at=slot.get('end_at'))
    if not _base_url():
        try:
            booking = calendar_service.create_booking(
                session,
                user_id=conversation.user_id,
                phone=conversation.contact,
                name=conversation.name or '',
                title='Property visit with Bobbie',
                start_at=slot.get('start_at'),
                end_at=slot.get('end_at'),
                location_address=getattr(conversation, 'property_address', None),
            )
        except calendar_service.SlotTakenError as error:
            raise SlotTakenError(str(error)) from error
        # The confirmation SMS is sent by the caller through the normal pipeline,
        # so no separate delivery hop is involved.
        result = {**booking, 'message': calendar_service.confirmation_message(booking),
                  'confirmation_sent': False}
        log_event(logger, 'sms.calendar.booking.completed', conversation_id=getattr(conversation, 'id', None),
                  source='in_process', booking_id=booking.get('id'), start_at=booking.get('start_at'))
        return result

    response = requests.post(
        f'{_base_url()}/api/bookings',
        json={
            'phone': conversation.contact,
            'name': conversation.name or '',
            'title': 'Property visit with Bobbie',
            'start_at': slot.get('start_at'),
            'end_at': slot.get('end_at'),
            'location_address': getattr(conversation, 'property_address', None),
        },
        timeout=15,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code == 409:
        raise SlotTakenError(payload.get('error') or 'That slot is no longer available')
    if not response.ok:
        raise RuntimeError(payload.get('error') or f'Calendar returned HTTP {response.status_code}')
    log_event(logger, 'sms.calendar.booking.completed', conversation_id=getattr(conversation, 'id', None),
              source='http', booking_id=payload.get('id'), start_at=payload.get('start_at'))
    return payload


def prompt_context(availability: dict, note: str = '') -> str:
    slots = '\n'.join(f"- {slot.get('label')}" for slot in (availability.get('slots') or []))
    note_line = f'{note}\n' if note else ''
    return (
        f"Current time: {availability.get('current_time')}\n"
        f"Timezone: {availability.get('timezone')}\n"
        f"Each meeting is {availability.get('slot_minutes')} minutes.\n"
        f'{note_line}'
        f'Offer only these exact available starts; never invent availability:\n{slots}'
    )


def safe_available_offer(availability: dict) -> str:
    """Offer at most two slots, one per day, using only verified availability."""
    slots, days = [], set()
    for slot in (availability.get('slots') or []):
        day = str(slot.get('label') or '').split(' at ')[0]
        if day in days:
            continue
        days.add(day)
        slots.append(slot)
        if len(slots) == 2:
            break
    if not slots:
        return 'I don’t have an open time to confirm yet. What other day works for me to stop by?'
    labels = [_short_label(slot.get('label') or '') for slot in slots]
    if len(labels) == 1:
        return f'I can do {labels[0]}. Does that work?'
    return f'I can do {labels[0]} or {labels[1]}. Which works?'


def _short_label(label: str) -> str:
    return re.sub(r' America/.+$', '', re.sub(r', 20\d{2}', '', str(label)))
