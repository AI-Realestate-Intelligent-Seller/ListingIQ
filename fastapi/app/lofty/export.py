"""Send to Lofty: preview, AI summary, and the resumable hand-off itself.

The hand-off runs in order and saves its place after every step:

1. lead  — find the owner in Lofty by exact email/phone; reuse a single match,
           create a lead only when there is none, stop when there are several.
2. mute  — mute Lofty's Sales Agent (Anna) for that lead and confirm it.
3. notes — store the summary and the full transcript as numbered notes.

A failure leaves the export at the failed step. Retrying resumes there, so a
saved Lofty lead is never created twice and notes already stored are skipped.
Nothing here texts or emails the owner.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..ai import client as ai_client
from ..core.config import settings
from ..leads.service import lead_details
from ..logger import get_logger, log_event
from ..models import Conversation, Lead, LoftyExport, Message, User
from .auth import LoftyNotAvailable, is_available, resolve_credentials
from .client import LoftyClient, LoftyError

logger = get_logger(__name__)

# A claim older than this belongs to a request that died; another may take over.
CLAIM_STALE_AFTER = timedelta(minutes=3)
NOTE_HEADER_RESERVE = 160
MAX_SUMMARY_TRANSCRIPT_CHARS = 24_000
STOPPED_STATUSES = {'dnc', 'realtor'}


class ExportInProgress(RuntimeError):
    """Another request is already sending this conversation."""


class ExportRejected(RuntimeError):
    """The request cannot be sent as given (missing data, opted-out owner, ...)."""


class SummaryUnavailable(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class LeadMatchAmbiguous(RuntimeError):
    def __init__(self, message: str, candidates: list[str]):
        super().__init__(message)
        self.candidates = candidates


# ---------------------------------------------------------------------------
# Lead data
# ---------------------------------------------------------------------------

def sender_label(message: Message) -> str:
    """AI, Owner or You, from the stored sender metadata.

    Inbound is always the owner. An outbound message is the human agent's when
    it carries the sending user's id or a ``broker.*`` event type (manual SMS
    and manual booking confirmations); every other outbound message was sent by
    ListingIQ's AI or automation (``ai.*``, ``outreach.initial``, ...).
    """
    if message.direction == 'inbound':
        return 'Owner'
    if message.sender_user_id is not None or str(message.event_type or '').startswith('broker.'):
        return 'You'
    return 'AI'


def _primary_lead(session: Session, conversation: Conversation) -> Lead | None:
    leads = (session.query(Lead).filter(Lead.conversation_id == conversation.id)
             .order_by(Lead.id).all())
    return next((lead for lead in leads if lead.property_address == conversation.property_address),
                leads[-1] if leads else None)


def _emails(lead: Lead | None) -> list[str]:
    if lead is None:
        return []
    details = lead_details(lead)
    values = details.get('emails') or []
    if isinstance(values, str):
        values = [values]
    if isinstance(details.get('email'), str):
        values = [*values, details['email']]
    found = []
    for value in values:
        text = str(value or '').strip()
        if re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', text) and text.lower() not in {
                item.lower() for item in found}:
            found.append(text)
    return found


def lofty_phone(value: str | None) -> str | None:
    """Phone as Lofty documents and was verified in Postman: 10 digits for US numbers."""
    digits = re.sub(r'\D', '', str(value or ''))
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits if 7 <= len(digits) <= 15 else None


def lead_fields(session: Session, conversation: Conversation) -> dict:
    lead = _primary_lead(session, conversation)
    name = ((lead.owner_name if lead else None) or conversation.name or '').strip()
    return {
        'listingiq_lead_id': lead.id if lead else None,
        'name': name or None,
        'property_address': (conversation.property_address
                             or (lead.property_address if lead else None) or None),
        'phone': conversation.contact or (lead.phone if lead else None) or None,
        'emails': _emails(lead),
    }


def transcript(session: Session, conversation: Conversation) -> list[Message]:
    return (session.query(Message)
            .filter(Message.conversation_id == conversation.id,
                    Message.text.isnot(None), Message.text != '')
            .order_by(Message.created_at, Message.id)
            .all())


def _not_delivered(message: Message) -> bool:
    return message.direction == 'outbound' and message.status in ('sending_failed', 'delivery_failed')


def _serialize_message(message: Message) -> dict:
    return {
        'id': message.id,
        'sender': sender_label(message),
        'text': message.text,
        'created_at': message.created_at,
        'not_delivered': _not_delivered(message),
    }


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def lofty_url(lead_id: str | None) -> str | None:
    if not lead_id:
        return None
    return settings['lofty']['crm_lead_url'].format(lead_id=lead_id)


def export_status(export: LoftyExport | None, candidates: list[str] | None = None) -> dict | None:
    if export is None:
        return None
    note_ids = json.loads(export.note_ids or '[]')
    return {
        'status': export.status,
        'step': export.step,
        'revision': export.revision,
        'lofty_lead_id': export.lofty_lead_id,
        'lofty_url': lofty_url(export.lofty_lead_id),
        'lead_action': export.lead_action,
        'mute_verified': bool(export.mute_verified),
        'notes_saved': sum(1 for value in note_ids if value),
        'notes_total': len(note_ids),
        'summary': export.summary,
        'last_message_id': export.last_message_id,
        'last_error': export.last_error,
        'candidate_lead_ids': candidates or [],
        'attempts': export.attempts,
        'completed_at': export.completed_at,
        'updated_at': export.updated_at,
    }


def _export_for(session: Session, conversation: Conversation) -> LoftyExport | None:
    return (session.query(LoftyExport)
            .filter(LoftyExport.conversation_id == conversation.id).first())


def preview(session: Session, conversation: Conversation, user: User) -> dict:
    """Everything the dialog shows. Reads only; calls neither Lofty nor the AI."""
    fields = lead_fields(session, conversation)
    messages = transcript(session, conversation)
    reason = None
    try:
        resolve_credentials(session, user)
    except LoftyNotAvailable as error:
        reason = str(error)
    if reason is None and _stopped(conversation):
        reason = 'This owner opted out or is a realtor, so they are not sent to Lofty.'
    return {
        'conversation_id': conversation.id,
        **fields,
        'messages': [_serialize_message(message) for message in messages],
        'available': reason is None,
        'unavailable_reason': reason,
        'export': export_status(_export_for(session, conversation)),
    }


def _stopped(conversation: Conversation) -> bool:
    return bool(conversation.dnc_alert) or conversation.lead_status in STOPPED_STATUSES


# ---------------------------------------------------------------------------
# AI summary
# ---------------------------------------------------------------------------

SUMMARY_RULES = """You summarise a real-estate SMS conversation for a CRM handoff note.
Write plain text (no markdown symbols) with these labelled lines, in this order:
Seller motivation:
Selling interest:
Timeline:
Property context:
Objections / concerns:
Important facts:
Recommended next action:

Rules:
- Use only what the transcript and lead details say. Never invent facts.
- When the conversation does not cover an item, write "Not discussed".
- Do not say an appointment is booked unless the transcript or lead details confirm it.
- Keep each line short (one or two sentences). No greeting, no sign-off."""


def _summary_transcript(messages: list[Message]) -> str:
    lines = [f"{sender_label(message)}: {' '.join((message.text or '').split())}"
             for message in messages]
    text = '\n'.join(lines)
    if len(text) > MAX_SUMMARY_TRANSCRIPT_CHARS:
        # Keep the most recent part; the start of a long thread is usually the opener.
        text = '[earlier messages omitted]\n' + text[-MAX_SUMMARY_TRANSCRIPT_CHARS:]
    return text


def generate_summary(session: Session, conversation: Conversation, user: User) -> str:
    resolve_credentials(session, user)
    messages = transcript(session, conversation)
    if not messages:
        raise SummaryUnavailable('There are no messages to summarise yet.', status_code=409)
    if not ai_client.is_configured():
        raise SummaryUnavailable('AI is not configured, so a summary cannot be generated. '
                                 'You can write one yourself.', status_code=503)
    fields = lead_fields(session, conversation)
    facts = [f'Property address: {fields["property_address"] or "Not on file"}',
             f'Appointment booked in ListingIQ: {"yes" if conversation.meeting_booked else "no"}']
    prompt = [
        {'role': 'system', 'content': SUMMARY_RULES},
        {'role': 'user', 'content': ('Lead details:\n' + '\n'.join(facts)
                                     + '\n\nTranscript (oldest first):\n'
                                     + _summary_transcript(messages))},
    ]
    try:
        reply = ai_client.completion(prompt, temperature=0.2, max_tokens=700,
                                     feature='followups.lofty_summary')
    except ai_client.AiUnavailableError as error:
        logger.warning('[lofty] summary failed for conversation %s: %s',
                       conversation.id, error.category)
        raise SummaryUnavailable('The AI summary could not be generated. Try again, '
                                 'or write the summary yourself.') from error
    summary = str((reply or {}).get('content') or '').strip()
    if not summary:
        raise SummaryUnavailable('The AI returned an empty summary. Try again, '
                                 'or write the summary yourself.')
    return summary


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def _timezone(user: User) -> ZoneInfo:
    for name in (user.timezone, settings['ai'].get('timezone'), 'UTC'):
        try:
            if name:
                return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ZoneInfo('UTC')


def _stamp(value: datetime | None, zone: ZoneInfo) -> str:
    if value is None:
        return 'time unknown'
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(zone)
    return local.strftime('%b %-d, %Y %-I:%M %p ') + (local.tzname() or '')


def _hard_split(text: str, limit: int) -> list[str]:
    """Split a block longer than ``limit`` on whitespace, losing nothing."""
    pieces, rest = [], text
    while len(rest) > limit:
        cut = rest.rfind(' ', 0, limit)
        if cut <= limit // 2:
            cut = limit
        pieces.append(rest[:cut])
        rest = rest[cut:]
    pieces.append(rest)
    return pieces


def note_reference(conversation_id: int, revision: int) -> str:
    return f'ListingIQ-{conversation_id}-r{revision}'


def build_note_parts(fields: dict, summary: str, messages: list[Message], *,
                     reference: str, zone: ZoneInfo, max_chars: int) -> list[str]:
    """Numbered notes, each within ``max_chars``, together holding everything once."""
    details = ['Lead details', f'Name: {fields.get("name") or "Not on file"}',
               f'Property: {fields.get("property_address") or "Not on file"}']
    if fields.get('phone'):
        details.append(f'Mobile: {fields["phone"]}')
    if fields.get('emails'):
        details.append(f'Email: {", ".join(fields["emails"])}')
    blocks = ['\n'.join(details), 'AI SUMMARY\n' + summary.strip(), 'FULL CONVERSATION']
    for message in messages:
        label = sender_label(message) + (' (not delivered)' if _not_delivered(message) else '')
        blocks.append(f'[{_stamp(message.created_at, zone)}] {label}:\n{message.text}')

    budget = max_chars - NOTE_HEADER_RESERVE
    bodies: list[str] = []
    current = ''
    for block in blocks:
        for piece in _hard_split(block, budget):
            candidate = f'{current}\n\n{piece}' if current else piece
            if len(candidate) <= budget:
                current = candidate
            else:
                bodies.append(current)
                current = piece
    if current:
        bodies.append(current)

    total = len(bodies)
    parts = []
    for index, body in enumerate(bodies, start=1):
        header = (f'ListingIQ — Qualified Seller (Part {index} of {total})\n'
                  f'Ref: {reference} · Part {index}/{total}')
        if index > 1:
            header += '\n(continued)'
        parts.append(f'{header}\n\n{body}')
    return parts


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

def _get_or_create(session: Session, conversation: Conversation, user: User,
                   lead_id: int | None) -> LoftyExport:
    export = _export_for(session, conversation)
    if export is not None:
        return export
    export = LoftyExport(conversation_id=conversation.id, lead_id=lead_id,
                         brokerage_id=user.brokerage_id, requested_by=user.id,
                         status='pending', step='lead', revision=1, attempts=0)
    session.add(export)
    try:
        session.commit()
    except IntegrityError:
        # A concurrent request created it first; use theirs.
        session.rollback()
        export = _export_for(session, conversation)
        assert export is not None
    return export


def _claim(session: Session, export: LoftyExport) -> bool:
    """Atomically mark this export as being sent by this request."""
    now = datetime.utcnow()
    result = session.execute(
        update(LoftyExport)
        .where(LoftyExport.id == export.id,
               or_(LoftyExport.status != 'in_progress',
                   LoftyExport.claimed_at.is_(None),
                   LoftyExport.claimed_at < now - CLAIM_STALE_AFTER))
        .values(status='in_progress', claimed_at=now, updated_at=now)
    )
    session.commit()
    session.refresh(export)
    return result.rowcount == 1


def _split_name(name: str) -> tuple[str, str | None]:
    parts = name.split()
    first = parts[0]
    last = ' '.join(parts[1:]) or None
    return first[:30], (last[:30] if last else None)


def _property(address: str | None) -> dict | None:
    """Best-effort split of "street, city, ST 12345" into Lofty's property fields."""
    if not address:
        return None
    parts = [part.strip() for part in address.split(',') if part.strip()]
    if parts and parts[-1].lower() in ('usa', 'us', 'united states'):
        parts = parts[:-1]
    if len(parts) >= 3:
        state_zip = re.fullmatch(r'([A-Za-z]{2})\s*(\d{5}(?:-\d{4})?)?', parts[-1])
        if state_zip:
            result = {'streetAddress': ', '.join(parts[:-2]), 'city': parts[-2],
                      'state': state_zip.group(1).upper()}
            if state_zip.group(2):
                result['zipCode'] = state_zip.group(2)
            return result
    return {'streetAddress': address.strip()}


def new_lead_payload(fields: dict) -> dict:
    config = settings['lofty']
    first, last = _split_name(fields['name'])
    payload: dict = {
        'firstName': first,
        'leadTypes': [1],  # Seller
        'source': config['lead_source'],
        'stage': config['lead_stage'],
        'tagsAdd': [config['lead_tag']],
    }
    if last:
        payload['lastName'] = last
    if fields.get('emails'):
        payload['emails'] = fields['emails']
    phone = lofty_phone(fields.get('phone'))
    if phone:
        payload['phones'] = [phone]
    prop = _property(fields.get('property_address'))
    if prop:
        payload['property'] = prop
    if config['assigned_user_id']:
        payload['assignedUserId'] = int(config['assigned_user_id'])
    return payload


def _ensure_lead(session: Session, export: LoftyExport, client: LoftyClient, fields: dict,
                 chosen_lead_id: str | None) -> None:
    if export.lofty_lead_id:
        export.step = 'mute'
        return
    phone = lofty_phone(fields.get('phone'))
    if not phone and not fields.get('emails'):
        raise ExportRejected('This lead has no phone number or email, so it cannot be '
                             'matched safely in Lofty.')
    candidates: list[str] = []
    for email in fields.get('emails') or []:
        candidates += client.find_leads(email=email)
    if phone:
        candidates += client.find_leads(phone=phone)
    candidates = list(dict.fromkeys(candidates))

    if chosen_lead_id:
        if chosen_lead_id not in candidates:
            raise LeadMatchAmbiguous('That Lofty lead ID does not match this owner\'s phone or '
                                     'email. Choose one of the matching leads.', candidates)
        lead_id, action = chosen_lead_id, 'existing'
    elif len(candidates) > 1:
        raise LeadMatchAmbiguous(
            f'{len(candidates)} Lofty leads match this owner\'s phone or email '
            f'({", ".join(candidates)}). Choose the right one so no duplicate is created.',
            candidates)
    elif candidates:
        lead_id, action = candidates[0], 'existing'
    else:
        if not fields.get('name'):
            raise ExportRejected('Lofty requires a first name, and this lead has no name on file.')
        lead_id, action = client.create_lead(new_lead_payload(fields)), 'created'

    # Save the lead id before anything else can fail.
    export.lofty_lead_id = lead_id
    export.lead_action = action
    session.commit()
    log_event(logger, 'lofty.lead.resolved', export_id=export.id, action=action)

    # Lofty may merge a POST into an existing record and ignore its tags, and an
    # existing lead never received them, so add the tag explicitly. tagsAdd keeps
    # every existing tag; owner, source and stage of an existing lead are untouched.
    client.update_lead(lead_id, {'tagsAdd': [settings['lofty']['lead_tag']]})
    export.step = 'mute'
    session.commit()


def _mute(session: Session, export: LoftyExport, client: LoftyClient) -> None:
    client.mute_sales_agent(export.lofty_lead_id)
    if not client.sales_agent_muted(export.lofty_lead_id):
        raise LoftyError('Lofty accepted the mute request, but the lead does not show as muted yet.')
    export.mute_verified = True
    export.step = 'notes'
    session.commit()


def _save_notes(session: Session, export: LoftyExport, client: LoftyClient,
                conversation: Conversation, user: User, fields: dict) -> None:
    resuming = export.note_parts is not None
    if not resuming:
        messages = transcript(session, conversation)
        parts = build_note_parts(
            fields, export.summary or '', messages,
            reference=note_reference(conversation.id, export.revision),
            zone=_timezone(user), max_chars=settings['lofty']['note_max_chars'])
        export.note_parts = json.dumps(parts)
        export.note_ids = json.dumps([None] * len(parts))
        export.last_message_id = messages[-1].id if messages else None
        session.commit()
    parts = json.loads(export.note_parts)
    note_ids = json.loads(export.note_ids or '[]') or [None] * len(parts)

    if resuming and not all(note_ids):
        # An earlier attempt may have saved a note without hearing back. Match
        # by the reference line so a retry never stores the same part twice.
        stored = client.list_notes(export.lofty_lead_id)
        for index, part in enumerate(parts):
            if note_ids[index]:
                continue
            marker = part.split('\n')[1]
            match = next((row for row in stored if marker in str(row.get('content') or '')), None)
            if match is not None:
                note_ids[index] = str(match.get('noteId') or match.get('id') or 'saved')
        export.note_ids = json.dumps(note_ids)
        session.commit()

    for index, part in enumerate(parts):
        if note_ids[index]:
            continue
        note_ids[index] = client.create_note(export.lofty_lead_id, part) or 'saved'
        export.note_ids = json.dumps(note_ids)
        session.commit()
    export.step = 'done'


def send(session: Session, conversation: Conversation, user: User, *, summary: str | None,
         resync: bool = False, lofty_lead_id: str | None = None,
         client: LoftyClient | None = None) -> dict:
    """Run (or resume) the hand-off. Returns the export status, never raises for Lofty errors."""
    credentials = resolve_credentials(session, user)
    if _stopped(conversation):
        raise ExportRejected('This owner opted out or is a realtor, so they are not sent to Lofty.')
    fields = lead_fields(session, conversation)
    export = _get_or_create(session, conversation, user, fields['listingiq_lead_id'])

    if export.status == 'completed' and not resync:
        return export_status(export)
    summary = (summary or '').strip()
    restarting_notes = export.status == 'completed' and resync
    if (restarting_notes or export.note_parts is None) and not summary:
        raise ExportRejected('Add a summary before sending to Lofty.')
    if not _claim(session, export):
        raise ExportInProgress('This conversation is already being sent to Lofty.')

    candidates: list[str] = []
    try:
        if restarting_notes:
            # A resync adds a new numbered set of notes to the same Lofty lead.
            export.revision += 1
            export.step = 'mute'
            export.mute_verified = False
            export.note_parts = None
            export.note_ids = None
            export.completed_at = None
        if export.note_parts is None:
            export.summary = summary
        export.attempts += 1
        export.last_error = None
        export.requested_by = user.id
        export.connection_ref = credentials.reference
        session.commit()

        client = client or LoftyClient(credentials)
        if export.step == 'lead':
            _ensure_lead(session, export, client, fields, (lofty_lead_id or '').strip() or None)
        if export.step == 'mute':
            _mute(session, export, client)
        if export.step == 'notes':
            _save_notes(session, export, client, conversation, user, fields)
        export.status = 'completed'
        export.completed_at = datetime.utcnow()
    except LeadMatchAmbiguous as error:
        export.status = 'needs_review'
        export.last_error = str(error)
        candidates = error.candidates
    except (LoftyError, ExportRejected) as error:
        export.status = 'failed'
        export.last_error = str(error)
    except Exception:
        session.rollback()
        export.status = 'failed'
        export.last_error = 'Unexpected error while sending to Lofty. Try again.'
        logger.exception('[lofty] export %s failed unexpectedly', export.id)
    finally:
        export.claimed_at = None
        session.commit()
    log_event(logger, 'lofty.export.finished', export_id=export.id, status=export.status,
              step=export.step, attempts=export.attempts)
    return export_status(export, candidates)


__all__ = [
    'ExportInProgress', 'ExportRejected', 'LoftyNotAvailable', 'SummaryUnavailable',
    'build_note_parts', 'export_status', 'generate_summary', 'is_available', 'preview',
    'send', 'sender_label',
]
