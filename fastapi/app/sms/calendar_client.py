"""Calendar access for the Bobbie pipeline.

By default the calendar runs in-process (calendar_service). Setting
CALENDAR_API_URL delegates to the standalone Simulation/calendar.py service
instead, keeping the original HTTP contract.
"""

import re

import requests

from ..core.config import settings
from ..logger import get_logger
from . import calendar_service

logger = get_logger(__name__)


class SlotTakenError(RuntimeError):
    """The requested slot was booked by someone else (HTTP 409 / overlap)."""


def _base_url() -> str:
    return settings['sms']['calendar_url']


def fetch_availability(session=None, user_id: int | None = None) -> dict:
    if not _base_url():
        return calendar_service.availability(session, user_id)

    logger.info('[calendar-tool] availability request -> %s', _base_url())
    response = requests.get(f'{_base_url()}/api/availability', timeout=7)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not response.ok:
        raise RuntimeError(payload.get('error') or f'Calendar returned HTTP {response.status_code}')
    logger.info('[calendar-tool] availability received: %s open slots', len(payload.get('slots') or []))
    return payload


def create_booking(conversation, slot: dict, session=None) -> dict:
    logger.info('[calendar-tool] booking request for %s: %s', conversation.contact, slot.get('start_at'))
    if not _base_url():
        try:
            booking = calendar_service.create_booking(
                session,
                user_id=conversation.user_id,
                phone=conversation.contact,
                name=conversation.name or '',
                title='Property consultation with Bobbie',
                start_at=slot.get('start_at'),
                end_at=slot.get('end_at'),
            )
        except calendar_service.SlotTakenError as error:
            raise SlotTakenError(str(error)) from error
        # The confirmation SMS is sent by the caller through the normal pipeline,
        # so no separate delivery hop is involved.
        return {**booking, 'message': calendar_service.confirmation_message(booking),
                'confirmation_sent': False}

    response = requests.post(
        f'{_base_url()}/api/bookings',
        json={
            'phone': conversation.contact,
            'name': conversation.name or '',
            'title': 'Property consultation with Bobbie',
            'start_at': slot.get('start_at'),
            'end_at': slot.get('end_at'),
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
    logger.info('[calendar-tool] booking created for %s: %s', conversation.contact, payload.get('start_at'))
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
        return 'I don’t have an open time to confirm yet. What other day works for a quick call?'
    labels = [_short_label(slot.get('label') or '') for slot in slots]
    if len(labels) == 1:
        return f'I can do {labels[0]}. Does that work?'
    return f'I can do {labels[0]} or {labels[1]}. Which works?'


def _short_label(label: str) -> str:
    return re.sub(r' America/.+$', '', re.sub(r', 20\d{2}', '', str(label)))
