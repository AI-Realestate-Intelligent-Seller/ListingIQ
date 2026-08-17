"""In-process meeting calendar — port of Simulation/calendar.py.

Bookings live in the application database (the existing Booking model) instead of
a separate SQLite file, so the standalone service on port 5052 is no longer
needed. Availability and overlap checks are scoped to one broker, which the
single-file simulator could not do.
"""

from datetime import datetime, time as clock_time, timedelta, timezone
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from ..core.config import settings
from ..logger import get_logger
from ..models import Booking

logger = get_logger(__name__)

WORKDAY_START = clock_time(9, 0)
WORKDAY_END = clock_time(18, 0)


class SlotTakenError(RuntimeError):
    """The requested slot overlaps an existing booking for this broker."""


def _zone():
    name = settings['ai']['timezone']
    return ZoneInfo(name) if ZoneInfo else timezone.utc


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _parse(value, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError as error:
            raise ValueError(f'{field} must be an ISO date and time') from error
    else:
        raise ValueError(f'{field} is required')
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _busy_ranges(session, user_id: int) -> list:
    rows = session.query(Booking.start_at, Booking.end_at).filter(Booking.user_id == user_id).all()
    return [(start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start,
             end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end)
            for start, end in rows if start and end]


def list_available_slots(session, user_id: int, days: int | None = None,
                         slot_minutes: int | None = None) -> list:
    """Unbooked weekday slots from 9 AM to 6 PM in the configured timezone."""
    days = days or settings['sms']['calendar_days']
    slot_minutes = slot_minutes or settings['sms']['calendar_slot_minutes']
    zone = _zone()
    timezone_name = settings['ai']['timezone']
    now_utc = datetime.now(timezone.utc)
    local_today = now_utc.astimezone(zone).date()
    busy = _busy_ranges(session, user_id)

    slots = []
    for offset in range(days):
        day = local_today + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        cursor = datetime.combine(day, WORKDAY_START, zone)
        day_end = datetime.combine(day, WORKDAY_END, zone)
        while cursor + timedelta(minutes=slot_minutes) <= day_end:
            end = cursor + timedelta(minutes=slot_minutes)
            start_utc, end_utc = cursor.astimezone(timezone.utc), end.astimezone(timezone.utc)
            overlaps = any(start_utc < busy_end and end_utc > busy_start for busy_start, busy_end in busy)
            if start_utc > now_utc and not overlaps:
                slots.append({
                    'start_at': _iso(start_utc),
                    'end_at': _iso(end_utc),
                    'label': cursor.strftime(f'%A, %B %d, %Y at %I:%M %p {timezone_name}'),
                })
            cursor = end
    return slots


def availability(session, user_id: int) -> dict:
    zone = _zone()
    return {
        'timezone': settings['ai']['timezone'],
        'current_time': datetime.now(zone).isoformat(),
        'slot_minutes': settings['sms']['calendar_slot_minutes'],
        'slots': list_available_slots(session, user_id),
    }


def create_booking(session, user_id: int, phone: str, name: str, title: str,
                   start_at, end_at) -> dict:
    start = _parse(start_at, 'start_at')
    end = _parse(end_at, 'end_at')
    if end <= start:
        raise ValueError('end_at must be later than start_at')
    if (end - start).total_seconds() > 4 * 60 * 60:
        raise ValueError('a meeting cannot be longer than four hours')

    # Collision check inside the transaction that inserts the row.
    overlap = (session.query(Booking)
               .filter(Booking.user_id == user_id,
                       Booking.start_at < end.replace(tzinfo=None),
                       Booking.end_at > start.replace(tzinfo=None))
               .first())
    if overlap:
        raise SlotTakenError('That time overlaps an existing booking')

    booking = Booking(
        user_id=user_id,
        phone=phone,
        name=name or None,
        title=title or 'Property consultation',
        start_at=start.replace(tzinfo=None),
        end_at=end.replace(tzinfo=None),
        join_token=uuid4().hex,
        created_at=datetime.utcnow(),
    )
    session.add(booking)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise SlotTakenError('That time overlaps an existing booking')
    session.refresh(booking)
    return serialize(booking)


def serialize(booking: Booking) -> dict:
    return {
        'id': booking.id,
        'phone': booking.phone,
        'name': booking.name,
        'title': booking.title,
        'start_at': _iso(booking.start_at.replace(tzinfo=timezone.utc)),
        'end_at': _iso(booking.end_at.replace(tzinfo=timezone.utc)),
        'join_token': booking.join_token,
        'join_url': join_url(booking.join_token),
        'created_at': booking.created_at.isoformat() if booking.created_at else None,
    }


def join_url(token: str) -> str:
    """Meeting links now point at the Next.js app rather than the calendar port."""
    return f"{settings['frontend_url'].rstrip('/')}/meeting/{token}"


def confirmation_message(booking: dict, host: str | None = None) -> str:
    """The confirmation SMS for a booking.

    `host` is who the owner is actually meeting. It defaults to Bobbie because
    she books the overwhelming majority of meetings, but a broker or agent
    booking by hand must be named as themselves — telling an owner they are
    meeting Bobbie when a person arranged it is simply untrue.
    """
    zone = _zone()
    timezone_name = settings['ai']['timezone']
    start = datetime.fromisoformat(booking['start_at'].replace('Z', '+00:00')).astimezone(zone)
    end = datetime.fromisoformat(booking['end_at'].replace('Z', '+00:00')).astimezone(zone)
    # First name only, as in the initial outreach: the full legal name reads
    # like a form letter, not a text message.
    full_name = (booking.get('name') or '').strip()
    name = full_name.split()[0] if full_name else 'there'
    return (f"Thanks, {name}. Your meeting with {host or 'Bobbie'} is confirmed for "
            f"{start.strftime('%b %d, %Y at %I:%M %p')}–{end.strftime('%I:%M %p')} "
            f"{timezone_name}. Join here: {booking['join_url']}")


def list_bookings(session, user_id: int) -> list:
    bookings = (session.query(Booking)
                .filter(Booking.user_id == user_id)
                .order_by(Booking.start_at)
                .all())
    return [serialize(booking) for booking in bookings]


def booking_by_token(session, token: str) -> dict | None:
    booking = session.query(Booking).filter(Booking.join_token == token).first()
    return serialize(booking) if booking else None
