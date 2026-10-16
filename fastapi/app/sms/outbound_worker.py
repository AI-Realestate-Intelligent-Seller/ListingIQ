"""Durable campaign SMS worker embedded by the FastAPI lifecycle.

FastAPI starts one process-local supervisor without blocking HTTP startup. A
Redis lease elects one active supervisor across overlapping Render instances;
that owner runs the fixed ten submission threads.
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from uuid import uuid4

import redis

from ..core.config import settings
from ..db import SessionLocal
from ..models import Campaign, Conversation, Lead, Message, OutboundSmsJob, User
from ..leads import campaign as campaign_service
from . import number_lookup
from . import service as sms_service

logger = logging.getLogger(__name__)
SMS_WORKER_COUNT = 10
TERMINAL = ('accepted', 'skipped', 'failed', 'uncertain')
_recipient_locks = tuple(threading.Lock() for _ in range(256))
_embedded_guard = threading.Lock()
_embedded_thread: threading.Thread | None = None
_embedded_stop: threading.Event | None = None
SUPERVISOR_LEASE_KEY = 'listingiq:sms:worker-supervisor'
SUPERVISOR_LEASE_SECONDS = 30
SUPERVISOR_RENEW_SECONDS = 10

_RENEW_LEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE_LEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class RecipientNoLongerEligible(RuntimeError):
    pass


class SupervisorLease:
    """Redis ownership for one embedded supervisor across overlapping deploys."""

    def __init__(self, client=None, token: str | None = None):
        self.client = client or redis.Redis.from_url(
            settings['redis_url'], socket_connect_timeout=2, socket_timeout=2,
            decode_responses=True,
        )
        self.token = token or f'{socket.gethostname()}:{os.getpid()}:{uuid4().hex}'

    def acquire(self) -> bool:
        return bool(self.client.set(
            SUPERVISOR_LEASE_KEY, self.token,
            nx=True, ex=SUPERVISOR_LEASE_SECONDS,
        ))

    def renew(self) -> bool:
        return bool(self.client.eval(
            _RENEW_LEASE_LUA, 1, SUPERVISOR_LEASE_KEY,
            self.token, SUPERVISOR_LEASE_SECONDS,
        ))

    def release(self) -> None:
        self.client.eval(
            _RELEASE_LEASE_LUA, 1, SUPERVISOR_LEASE_KEY, self.token,
        )


def _worker_name(slot: int) -> str:
    return f'{socket.gethostname()}:{os.getpid()}:{slot}'


def _recipient_lock(phone: str) -> threading.Lock:
    """Keep two properties for one owner on the existing single thread."""
    slot = hashlib.sha256(phone.encode('utf-8')).digest()[0]
    return _recipient_locks[slot]


def recover_stale_jobs(session) -> int:
    """Recover safe work and quarantine possibly-submitted work."""
    cutoff = datetime.utcnow() - timedelta(
        seconds=settings['sms_submission']['stale_after_seconds'])
    lookups = (session.query(OutboundSmsJob)
               .filter(OutboundSmsJob.status == 'looking_up',
                       OutboundSmsJob.claimed_at < cutoff).all())
    for job in lookups:
        job.status = 'awaiting_lookup'
        job.claimed_at = None
        job.worker_id = None
    submissions = (session.query(OutboundSmsJob)
                   .filter(OutboundSmsJob.status == 'submitting',
                           OutboundSmsJob.claimed_at < cutoff).all())
    for job in submissions:
        message = session.get(Message, job.message_id) if job.message_id else None
        if message is not None and message.telnyx_id:
            # The provider response was durably recorded before later campaign
            # side effects. Acceptance is therefore known and must not resend.
            job.status = 'accepted'
            job.telnyx_id = message.telnyx_id
            job.last_error = None
        else:
            # The provider may have accepted the request before this process
            # died. Without verified provider-side SMS idempotency, resending
            # is unsafe.
            job.status = 'uncertain'
            job.last_error = 'Worker stopped while provider submission was in flight; not retried.'
            if message:
                message.status = 'delivery_unconfirmed'
                message.failure_reason = job.last_error
    session.commit()
    return len(lookups) + len(submissions)


def _claim(session, statuses: tuple[str, ...], new_status: str, worker_id: str):
    now = datetime.utcnow()
    ready = (session.query(OutboundSmsJob)
             .filter(OutboundSmsJob.status.in_(statuses),
                     OutboundSmsJob.available_at <= now)
             .order_by(OutboundSmsJob.available_at, OutboundSmsJob.id))
    if session.get_bind().dialect.name != 'sqlite':
        # Neon/PostgreSQL workers lock different ready rows instead of racing
        # for the oldest row and idling nine threads behind one submitter.
        job = ready.with_for_update(skip_locked=True).first()
        if job is None:
            return None
        job.status = new_status
        job.claimed_at = now
        job.worker_id = worker_id
        session.commit()
        return job

    # SQLite has no SKIP LOCKED. The guarded update is the local-test fallback
    # and still guarantees that only one thread owns a row.
    candidate = ready.with_entities(OutboundSmsJob.id).first()
    if candidate is None:
        return None
    job_id = candidate[0]
    changed = (session.query(OutboundSmsJob)
               .filter(OutboundSmsJob.id == job_id,
                       OutboundSmsJob.status.in_(statuses),
                       OutboundSmsJob.available_at <= now)
               .update({
                   OutboundSmsJob.status: new_status,
                   OutboundSmsJob.claimed_at: now,
                   OutboundSmsJob.worker_id: worker_id,
               }, synchronize_session=False))
    session.commit()
    return session.get(OutboundSmsJob, job_id) if changed == 1 else None


def process_lookup_once(worker_id: str = 'lookup-coordinator') -> bool:
    """Process one lookup serially; concurrency is intentionally Phase 2."""
    session = SessionLocal()
    try:
        job = _claim(session, ('awaiting_lookup',), 'looking_up', worker_id)
        if job is None:
            return False
        lead = session.get(Lead, job.lead_id)
        result = number_lookup.lookup_number(job.to_number)
        if lead is not None:
            campaign_service._save_number_lookup(lead, result)
            details = campaign_service._stored_details(lead)
            parent_id = details.get('_parent_lead_id')
            parent = session.get(Lead, int(parent_id)) if parent_id else None
            if parent is not None:
                campaign_service._save_number_lookup(parent, result)
        reason = campaign_service._lookup_skip_reason(result)
        job.status = 'skipped' if reason else 'queued'
        job.last_error = reason
        job.claimed_at = None
        job.worker_id = None
        session.commit()
        if reason:
            finalize_campaign_if_complete(job.campaign_id)
        return True
    except Exception:
        session.rollback()
        logger.exception('Number Lookup job failed')
        return True
    finally:
        session.close()


def _opt_out_reason(session, job: OutboundSmsJob) -> str | None:
    """Why this recipient may no longer be texted, read at the last moment.

    An inbound STOP marks the owner's thread (``dnc_alert``/``lead_status``),
    not the queued lead, and the lead's own flag only syncs on a later page
    load. Every brokerage sends from one number, so an opt-out on any thread
    for this contact applies here.
    """
    lead = session.get(Lead, job.lead_id)
    if lead is None:
        return None
    session.refresh(lead)
    if lead.dnc:
        return 'On the do-not-contact list.'
    contacts = {value for value in (lead.phone, job.to_number) if value}
    opted_out = (session.query(Conversation.id)
                 .filter(Conversation.contact.in_(contacts),
                         (Conversation.dnc_alert.is_(True))
                         | (Conversation.lead_status == 'dnc'))
                 .first())
    if opted_out is not None:
        return 'Owner opted out (STOP); on the do-not-contact list.'
    return None


def _prepare_message(session, job: OutboundSmsJob):
    campaign = session.get(Campaign, job.campaign_id)
    lead = session.get(Lead, job.lead_id)
    user = session.get(User, job.user_id)
    if not campaign or not lead or not user:
        raise ValueError('Campaign recipient no longer exists.')
    if job.message_id:
        message = session.get(Message, job.message_id)
        if not message:
            raise ValueError('Queued message record no longer exists.')
        conversation = session.get(Conversation, message.conversation_id)
        return campaign, lead, user, conversation, message

    conversation, key = campaign_service._prepare_conversation(
        session, user, campaign, lead)
    message = Message(
        conversation_id=conversation.id,
        direction='outbound',
        from_number=sms_service.sending_number(),
        to_number=job.to_number,
        text=job.text,
        status='submission_queued',
        event_type='outreach.initial',
        campaign_id=campaign.id,
        property_key=key,
        created_at=datetime.utcnow(),
    )
    session.add(message)
    session.flush()
    job.message_id = message.id
    session.commit()
    return campaign, lead, user, conversation, message


def process_submission_once(worker_id: str) -> bool:
    session = SessionLocal()
    campaign_id = None
    submission_started = False
    recipient_lock = None
    try:
        job = _claim(session, ('queued', 'retry_wait'), 'submitting', worker_id)
        if job is None:
            return False
        campaign_id = job.campaign_id
        job.attempts += 1
        session.commit()
        recipient_lock = _recipient_lock(job.to_number)
        recipient_lock.acquire()
        # STOP can arrive while a large campaign waits in the durable queue.
        # Re-check before the owner's thread is touched: preparing the message
        # resets the thread's status, which would erase a 'dnc' marker.
        opted_out = _opt_out_reason(session, job)
        if opted_out:
            raise RecipientNoLongerEligible(opted_out)
        campaign, lead, user, conversation, message = _prepare_message(session, job)
        submission_started = True
        response = sms_service.send_sms({
            'to': job.to_number,
            'text': job.text,
            'simulation_event_type': 'outreach.initial',
        })
        provider_id = (response.get('data') or {}).get('id')
        message.status = 'queued'
        message.telnyx_id = provider_id
        message.failure_reason = None
        job.telnyx_id = provider_id
        # Record the provider identity before any later campaign bookkeeping.
        # Recovery can prove acceptance from this commit and never resubmit.
        session.commit()
        campaign_service._record_sent(
            session, user, campaign, lead, conversation, message,
            job.property_key or '', job.text,
        )
        job = session.get(OutboundSmsJob, job.id)
        job.status = 'accepted'
        job.claimed_at = None
        job.worker_id = None
        session.commit()
    except RecipientNoLongerEligible as error:
        session.rollback()
        job = session.get(OutboundSmsJob, job.id)
        message = session.get(Message, job.message_id) if job and job.message_id else None
        job.status = 'skipped'
        job.last_error = str(error)
        job.claimed_at = None
        job.worker_id = None
        if message:
            message.status = 'sending_failed'
            message.failure_reason = str(error)
        session.commit()
    except sms_service.SmsDeliveryError as error:
        session.rollback()
        if 'job' not in locals() or job is None:
            logger.exception('SMS worker failed before claiming a job')
            return True
        job = session.get(OutboundSmsJob, job.id)
        message = session.get(Message, job.message_id) if job and job.message_id else None
        if error.ambiguous:
            job.status = 'uncertain'
            if message:
                message.status = 'delivery_unconfirmed'
        elif error.retryable and job.attempts < settings['sms_submission']['max_attempts']:
            delay = error.retry_after or (
                settings['sms_submission']['retry_base_seconds'] * (2 ** (job.attempts - 1)))
            job.status = 'retry_wait'
            job.available_at = datetime.utcnow() + timedelta(seconds=delay)
            if message:
                message.status = 'submission_retry'
        else:
            job.status = 'failed'
            if message:
                message.status = 'sending_failed'
        job.last_error = str(error)[:2000]
        job.claimed_at = None
        job.worker_id = None
        if message:
            message.failure_reason = job.last_error
        session.commit()
    except Exception as error:  # explicit local failures are safe to retry
        session.rollback()
        if 'job' in locals() and job is not None:
            job = session.get(OutboundSmsJob, job.id)
            if job:
                message = session.get(Message, job.message_id) if job.message_id else None
                if job.status == 'accepted':
                    # Provider acceptance and the durable state both landed;
                    # only a later local side effect failed. Never resubmit.
                    pass
                elif submission_started:
                    if message is not None and message.telnyx_id:
                        job.status = 'accepted'
                        job.telnyx_id = message.telnyx_id
                        job.last_error = None
                    else:
                        job.status = 'uncertain'
                        job.last_error = ('Unexpected failure while provider submission was in flight: '
                                          f'{error}')[:2000]
                    job.claimed_at = None
                    job.worker_id = None
                    if message and job.status == 'uncertain':
                        message.status = 'delivery_unconfirmed'
                        message.failure_reason = job.last_error
                    session.commit()
                else:
                    job.status = ('retry_wait'
                                  if job.attempts < settings['sms_submission']['max_attempts']
                                  else 'failed')
                    job.available_at = datetime.utcnow() + timedelta(
                        seconds=settings['sms_submission']['retry_base_seconds'])
                    job.last_error = str(error)[:2000]
                    job.claimed_at = None
                    job.worker_id = None
                    session.commit()
        logger.exception('SMS submission job failed locally')
    finally:
        if recipient_lock is not None:
            recipient_lock.release()
        session.close()
    if campaign_id is not None:
        finalize_campaign_if_complete(campaign_id)
    return True


def finalize_campaign_if_complete(campaign_id: int) -> bool:
    """Release non-contacted recipients after every job has reached a terminal state."""
    session = SessionLocal()
    try:
        campaign = (session.query(Campaign).filter(Campaign.id == campaign_id)
                    .with_for_update().first())
        if campaign is None or campaign.status != 'sending':
            return False
        pending = (session.query(OutboundSmsJob.id)
                   .filter(OutboundSmsJob.campaign_id == campaign_id,
                           ~OutboundSmsJob.status.in_(TERMINAL)).first())
        if pending is not None:
            return False
        accepted = {row[0] for row in session.query(OutboundSmsJob.lead_id).filter(
            OutboundSmsJob.campaign_id == campaign_id,
            OutboundSmsJob.status == 'accepted').all()}
        jobs = session.query(OutboundSmsJob).filter(
            OutboundSmsJob.campaign_id == campaign_id).all()
        successful_parents: set[int] = set()
        for job in jobs:
            lead = session.get(Lead, job.lead_id)
            if lead is not None and lead.id in accepted:
                details = campaign_service._stored_details(lead)
                successful_parents.add(int(details.get('_parent_lead_id') or lead.id))
        for job in jobs:
            if job.lead_id in accepted:
                continue
            lead = session.get(Lead, job.lead_id)
            if lead is None:
                continue
            if lead.source == campaign_service.PROVIDER_CONTACT_SOURCE:
                session.delete(lead)
            elif (lead.source == campaign_service.PROVIDER_LEAD_SOURCE
                  and lead.id not in successful_parents):
                campaign_service._restore_provider_master(lead)
                lead.campaign_id = None
            elif lead.source != campaign_service.PROVIDER_LEAD_SOURCE:
                lead.campaign_id = None
        campaign.status = 'sent'
        campaign.sent_at = datetime.utcnow()
        session.commit()
        return True
    finally:
        session.close()


def _submission_loop(slot: int, stop: threading.Event) -> None:
    worker_id = _worker_name(slot)
    while not stop.is_set():
        try:
            worked = process_submission_once(worker_id)
        except Exception:  # noqa: BLE001 - one bad cycle must not retire a thread
            # A dropped database connection or a failed campaign finalization
            # escapes the per-job handlers. Without this, the thread would end
            # silently and the process would keep running with fewer senders.
            # Any job left 'submitting' is quarantined by stale recovery.
            logger.exception('SMS submission worker %s cycle failed', worker_id)
            stop.wait(1.0)
            continue
        if not worked:
            stop.wait(0.25)


def run_forever(stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    session = SessionLocal()
    try:
        recovered = recover_stale_jobs(session)
        if recovered:
            logger.warning('Recovered %s stale outbound SMS jobs', recovered)
    finally:
        session.close()
    # Recovery may have made the final in-flight job terminal. Complete any
    # campaigns that were waiting only for that stale job.
    recovery_session = SessionLocal()
    try:
        campaign_ids = [row[0] for row in recovery_session.query(Campaign.id).filter(
            Campaign.status == 'sending').all()]
    finally:
        recovery_session.close()
    for campaign_id in campaign_ids:
        finalize_campaign_if_complete(campaign_id)
    with ThreadPoolExecutor(max_workers=SMS_WORKER_COUNT,
                            thread_name_prefix='sms-submit') as pool:
        futures = [pool.submit(_submission_loop, slot, stop)
                   for slot in range(SMS_WORKER_COUNT)]
        try:
            while not stop.is_set():
                try:
                    worked = process_lookup_once()
                except Exception:  # noqa: BLE001 - keep the coordinator alive
                    logger.exception('Number Lookup coordinator cycle failed')
                    stop.wait(1.0)
                    continue
                if not worked:
                    stop.wait(0.25)
        except KeyboardInterrupt:
            stop.set()
        finally:
            stop.set()
            for future in futures:
                future.result()


def _run_embedded_supervisor(stop: threading.Event) -> None:
    """Wait for global ownership, then supervise the durable worker locally."""
    lease = SupervisorLease()
    while not stop.is_set():
        try:
            acquired = lease.acquire()
        except redis.RedisError as error:
            logger.warning('Embedded SMS worker is waiting for Redis: %s', error)
            stop.wait(2)
            continue
        if not acquired:
            # During a rolling Render deploy, the replacement web process waits
            # while the old process drains and releases ownership.
            stop.wait(2)
            continue

        worker_stop = threading.Event()
        worker = threading.Thread(
            target=run_forever,
            args=(worker_stop,),
            name='sms-outbound-worker',
            daemon=True,
        )
        worker.start()
        logger.info('Embedded outbound SMS worker acquired the global lease')
        lease_owned = True
        try:
            while not stop.wait(SUPERVISOR_RENEW_SECONDS):
                if not worker.is_alive():
                    logger.error('Embedded outbound SMS worker stopped unexpectedly')
                    break
                try:
                    if not lease.renew():
                        logger.warning('Embedded outbound SMS worker lost its global lease')
                        lease_owned = False
                        break
                except redis.RedisError as error:
                    # Stop rather than risk two supervisors after the lease
                    # expires during a Redis/network interruption.
                    logger.warning('Embedded SMS lease renewal failed: %s', error)
                    lease_owned = False
                    break
        finally:
            worker_stop.set()
            worker.join()
            if lease_owned:
                try:
                    lease.release()
                except redis.RedisError as error:
                    logger.warning('Could not release embedded SMS worker lease: %s', error)
        if not stop.is_set():
            stop.wait(2)


def start_embedded_worker() -> bool:
    """Start one non-blocking supervisor thread for this FastAPI process."""
    global _embedded_thread, _embedded_stop
    if not settings['sms_submission']['embedded_worker_enabled']:
        logger.info('Embedded outbound SMS worker is disabled')
        return False
    with _embedded_guard:
        if _embedded_thread is not None and _embedded_thread.is_alive():
            return False
        _embedded_stop = threading.Event()
        _embedded_thread = threading.Thread(
            target=_run_embedded_supervisor,
            args=(_embedded_stop,),
            name='sms-worker-supervisor',
            daemon=True,
        )
        _embedded_thread.start()
        return True


def stop_embedded_worker() -> bool:
    """Stop and join the process-local supervisor during FastAPI shutdown."""
    global _embedded_thread, _embedded_stop
    with _embedded_guard:
        thread = _embedded_thread
        stop = _embedded_stop
        if thread is None or stop is None:
            return False
        stop.set()
    thread.join()
    with _embedded_guard:
        if _embedded_thread is thread:
            _embedded_thread = None
            _embedded_stop = None
    return True


if __name__ == '__main__':
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
    run_forever()
