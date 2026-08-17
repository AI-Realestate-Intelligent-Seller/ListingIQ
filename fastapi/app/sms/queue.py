"""Single-lane lead queue — port of lead-queue.js + processQueuedLead().

The prototype used BullMQ/Redis purely to run one simulated conversation at a
time (the recipient simulator is limited to one DeepSeek call in flight). An
in-process worker thread gives the same serialization without adding Redis as a
hard dependency; jobs are re-queued from the database on startup if needed.
"""

import queue
import threading
import time
from datetime import datetime

from ..core.config import settings
from ..db import SessionLocal
from ..logger import get_logger
from ..models import Conversation
from .service import mark_lead_completed, send_and_store_message, update_lead_progress

logger = get_logger(__name__)

_jobs: "queue.Queue[dict]" = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


def enqueue_lead(conversation_id: int, text: str) -> None:
    """Queue a simulated outreach conversation and make sure the worker runs."""
    start_worker()
    _jobs.put({'conversation_id': conversation_id, 'text': text})
    logger.info('[lead-queue] queued conversation %s', conversation_id)


def queue_depth() -> int:
    return _jobs.qsize()


def start_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker and _worker.is_alive():
            return
        _worker = threading.Thread(target=_run_worker, name='lead-queue-worker', daemon=True)
        _worker.start()


def _run_worker() -> None:
    while True:
        job = _jobs.get()
        try:
            _process_queued_lead(job)
        except Exception as error:
            logger.error('[lead-queue] failed conversation %s: %s', job.get('conversation_id'), error)
            _fail_job(job.get('conversation_id'))
        finally:
            _jobs.task_done()


def _fail_job(conversation_id: int | None) -> None:
    if not conversation_id:
        return
    session = SessionLocal()
    try:
        conversation = session.query(Conversation).filter(Conversation.id == conversation_id).first()
        if conversation:
            update_lead_progress(session, conversation, queue_status='failed')
    finally:
        session.close()


def _process_queued_lead(job: dict) -> None:
    conversation_id = job['conversation_id']
    cooldown = settings['sms']['queue_cooldown_seconds']
    timeout = settings['sms']['lead_job_timeout_seconds']

    session = SessionLocal()
    try:
        conversation = session.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not conversation:
            return
        logger.info('[lead-queue] started %s', conversation.contact)
        update_lead_progress(session, conversation, ai_enabled=True, recipient_ai_enabled=True,
                             queue_status='active', lead_status='processing')
        send_and_store_message(session, conversation, job['text'], 'outreach.bulk-simulation')
    finally:
        session.close()

    deadline = time.monotonic() + timeout if timeout > 0 else None
    while deadline is None or time.monotonic() < deadline:
        session = SessionLocal()
        try:
            conversation = session.query(Conversation).filter(Conversation.id == conversation_id).first()
            if not conversation or conversation.processed_at or not conversation.ai_enabled:
                if conversation and not conversation.processed_at:
                    mark_lead_completed(session, conversation)
                elif conversation:
                    update_lead_progress(session, conversation, queue_status='completed')
                logger.info('[lead-queue] completed conversation %s', conversation_id)
                _cooldown(cooldown, conversation_id)
                return
        finally:
            session.close()
        time.sleep(1.5)

    session = SessionLocal()
    try:
        conversation = session.query(Conversation).filter(Conversation.id == conversation_id).first()
        if conversation:
            update_lead_progress(session, conversation, ai_enabled=False)
            mark_lead_completed(session, conversation, lead_status='no_response',
                                processed_at=datetime.utcnow())
        logger.warning('[lead-queue] timed out conversation %s after %ss; moving to the next lead',
                       conversation_id, timeout)
    finally:
        session.close()
    _cooldown(cooldown, conversation_id)


def _cooldown(seconds: float, conversation_id: int) -> None:
    if seconds <= 0:
        return
    logger.info('[lead-queue] conversation %s finished; waiting %ss before the next lead',
                conversation_id, seconds)
    time.sleep(seconds)
