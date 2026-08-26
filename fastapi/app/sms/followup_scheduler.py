"""Persisted Bobbie follow-ups for campaign owners who have not replied."""

import threading
from datetime import datetime, timedelta

from ..core.config import settings
from ..db import SessionLocal
from ..logger import get_logger
from ..models import Conversation, Message
from . import bobbie
from .service import SmsDeliveryError, history_for, send_and_store_message

logger = get_logger(__name__)
_stop = threading.Event()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


def _hours(name: str) -> timedelta:
    return timedelta(hours=max(float(settings['sms'][name]), 0))


def _has_inbound(session, conversation: Conversation) -> bool:
    query = session.query(Message.id).filter(
        Message.conversation_id == conversation.id,
        Message.direction == 'inbound',
    )
    if conversation.followup_started_at:
        query = query.filter(Message.created_at >= conversation.followup_started_at)
    return query.first() is not None


def schedule_initial_followup(session, conversation: Conversation, now: datetime | None = None) -> None:
    """Start a fresh cadence after a successful campaign outreach."""
    if not settings['sms']['followup_enabled']:
        return
    now = now or datetime.utcnow()
    conversation.followup_attempt_count = 0
    conversation.followup_started_at = now
    conversation.final_followup_sent_at = None
    conversation.dead_at = None
    conversation.next_followup_at = now + _hours('followup_first_delay_hours')
    session.add(conversation)
    session.commit()


def cancel_followup_cadence(session, conversation: Conversation, owner_replied: bool = False) -> None:
    conversation.next_followup_at = None
    if owner_replied:
        conversation.dead_at = None
        if conversation.lead_status == 'no_response':
            conversation.lead_status = 'processing'
        if conversation.queue_status == 'dead':
            conversation.queue_status = 'idle'
    session.add(conversation)
    session.commit()


def resume_silent_followup_cadence(session, conversation: Conversation,
                                   now: datetime | None = None) -> None:
    if (not settings['sms']['followup_enabled'] or conversation.campaign_id is None
            or _has_inbound(session, conversation) or conversation.dnc_alert
            or conversation.meeting_booked or conversation.dead_at):
        return
    now = now or datetime.utcnow()
    delay = ('followup_first_delay_hours' if not conversation.followup_attempt_count
             else 'followup_gap_hours')
    conversation.next_followup_at = now + _hours(delay)
    session.add(conversation)
    session.commit()


def _eligible(session, conversation: Conversation) -> bool:
    return bool(
        conversation.ai_enabled
        and conversation.handled_by == 'bobbie'
        and not conversation.dnc_alert
        and not conversation.meeting_booked
        and conversation.lead_status not in ('not_interested', 'dnc')
        and not _has_inbound(session, conversation)
    )


def process_due_conversation(session, conversation_id: int,
                             now: datetime | None = None) -> str:
    """Process one claimed due row. Returns sent, dead, cancelled, or retry."""
    now = now or datetime.utcnow()
    conversation = session.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conversation or not _eligible(session, conversation):
        if conversation:
            cancel_followup_cadence(session, conversation)
        return 'cancelled'

    maximum = max(int(settings['sms']['followup_max_attempts']), 1)
    if conversation.followup_attempt_count >= maximum:
        conversation.next_followup_at = None
        conversation.dead_at = now
        conversation.lead_status = 'no_response'
        conversation.queue_status = 'dead'
        conversation.ai_enabled = False
        session.add(conversation)
        session.commit()
        logger.info('[followup] conversation=%s status=dead attempts=%s', conversation.id, maximum)
        return 'dead'

    attempt = conversation.followup_attempt_count + 1
    message = bobbie.generate_no_response_followup(
        conversation, history_for(session, conversation), attempt, maximum)
    try:
        send_and_store_message(session, conversation, message, f'ai.followup.{attempt}', True)
    except SmsDeliveryError as error:
        conversation.next_followup_at = now + timedelta(minutes=10)
        session.add(conversation)
        session.commit()
        logger.warning('[followup] conversation=%s attempt=%s retry=true error=%s',
                       conversation.id, attempt, error)
        return 'retry'

    conversation.followup_attempt_count = attempt
    conversation.next_followup_at = now + _hours('followup_gap_hours')
    if attempt >= maximum:
        conversation.final_followup_sent_at = now
    session.add(conversation)
    session.commit()
    logger.info('[followup] conversation=%s attempt=%s/%s sent=true', conversation.id, attempt, maximum)
    return 'sent'


def run_due_followups(now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.utcnow()
    counts = {'sent': 0, 'dead': 0, 'cancelled': 0, 'retry': 0}
    session = SessionLocal()
    try:
        ids = [row[0] for row in session.query(Conversation.id).filter(
            Conversation.next_followup_at.isnot(None),
            Conversation.next_followup_at <= now,
        ).order_by(Conversation.next_followup_at, Conversation.id).all()]
    finally:
        session.close()

    for conversation_id in ids:
        session = SessionLocal()
        try:
            # Atomically claim the due row so concurrent app workers cannot send it twice.
            claimed = session.query(Conversation).filter(
                Conversation.id == conversation_id,
                Conversation.next_followup_at.isnot(None),
                Conversation.next_followup_at <= now,
            ).update({Conversation.next_followup_at: None}, synchronize_session=False)
            session.commit()
            if not claimed:
                continue
            outcome = process_due_conversation(session, conversation_id, now)
            counts[outcome] += 1
        except Exception as error:  # noqa: BLE001 - one lead must not stop the scheduler
            session.rollback()
            logger.exception('[followup] conversation=%s processing failed: %s', conversation_id, error)
        finally:
            session.close()
    return counts


def _run() -> None:
    poll = max(float(settings['sms']['followup_poll_seconds']), 1)
    while not _stop.wait(poll):
        try:
            run_due_followups()
        except Exception as error:  # noqa: BLE001
            logger.exception('[followup] scheduler tick failed: %s', error)


def start_followup_scheduler() -> None:
    global _worker
    if not settings['sms']['followup_enabled']:
        logger.info('[followup] scheduler disabled')
        return
    with _worker_lock:
        if _worker and _worker.is_alive():
            return
        _worker = threading.Thread(target=_run, name='bobbie-followup-scheduler', daemon=True)
        _worker.start()
        logger.info('[followup] scheduler started')
