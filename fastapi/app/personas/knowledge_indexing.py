"""Durable, version-scoped indexing of eligible Persona source knowledge."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import threading
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import SessionLocal
from ..vector_store import vector_store
from .models import Persona, PersonaEvent, PersonaKnowledgeIndexJob, PersonaVersion

logger = logging.getLogger(__name__)
_stop = threading.Event()


def _now() -> datetime:
    return datetime.utcnow()


def eligible_chunks(version: PersonaVersion) -> list[dict]:
    """Select explicit owner-approved imported facts, never style/examples.

    `eligible` means authorized for retrieval, not independently verified. The
    source and owner-review status remain in every payload and prompt text.
    """
    review = dict((version.profile_config or {}).get('source_review') or {})
    imports = [item for item in review.get('imports') or [] if item.get('job_id')]
    if not imports:
        return []
    # Claims are merged across imports without per-claim attribution. With a
    # single import the job ID is exact; otherwise the source is this version's
    # owner-reviewed source review, and every contributing import is listed.
    import_ids = sorted({str(item['job_id']) for item in imports})
    default_source = import_ids[0] if len(import_ids) == 1 else f'source-review-v{version.id}'
    source_import_ids = ','.join(import_ids)[:900]
    selected = []
    for item in review.get('claims') or []:
        if item.get('owner_status') == 'owner_disputed' or not str(item.get('statement') or '').strip():
            continue
        statement = str(item['statement']).strip()
        source_status = str(item.get('source_status') or 'unverified')
        selected.append({
            'text': f'Owner-confirmed {source_status.replace("_", " ")} information: {statement}',
            'source_document_id': default_source,
            'source_import_ids': source_import_ids,
            'source_status': source_status,
            'approval_basis': 'owner_confirmed',
        })
    for item in review.get('unmapped_details') or []:
        if item.get('owner_status') == 'dismissed' or not str(item.get('detail') or '').strip():
            continue
        selected.append({
            'text': f'Owner-kept imported reference information: {str(item["detail"]).strip()}',
            'source_document_id': default_source,
            'source_import_ids': source_import_ids,
            'source_status': 'unverified',
            'approval_basis': 'owner_kept',
        })
    chunks = []
    for index, item in enumerate(selected):
        text = item['text'][:1200]
        chunks.append({
            **item,
            'chunk_index': index,
            'content_hash': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'text': text,
        })
    return chunks


def _selection_hash(chunks: list[dict]) -> str:
    wire = json.dumps(chunks, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(wire.encode('utf-8')).hexdigest()


def queue_version(session: Session, persona: Persona, version: PersonaVersion, actor_user_id: int) -> PersonaKnowledgeIndexJob | None:
    """Add an outbox row in the caller's publication/activation transaction."""
    existing = session.query(PersonaKnowledgeIndexJob).filter(
        PersonaKnowledgeIndexJob.version_id == version.id,
    ).first()
    if existing is not None:
        if existing.status == 'failed' and existing.attempt_count >= existing.max_attempts:
            # Re-activating a version whose indexing gave up earns a fresh,
            # bounded set of attempts; the job stays keyed to the same version.
            existing.status = 'pending'
            existing.attempt_count = 0
            existing.next_attempt_at = _now()
            existing.updated_at = _now()
        return existing
    chunks = eligible_chunks(version)
    if not chunks:
        prior = session.query(PersonaEvent).filter(
            PersonaEvent.persona_id == persona.id,
            PersonaEvent.persona_version_id == version.id,
            PersonaEvent.event_type == 'persona.indexing_not_required',
        ).first()
        if prior is None:
            session.add(PersonaEvent(
                brokerage_id=persona.brokerage_id, persona_id=persona.id,
                persona_version_id=version.id, event_type='persona.indexing_not_required',
                actor_user_id=actor_user_id, detail={'reason': 'no_eligible_source_knowledge'},
                created_at=_now(),
            ))
        return None
    row = PersonaKnowledgeIndexJob(
        id=uuid4().hex, persona_id=persona.id, version_id=version.id,
        owner_user_id=persona.owner_user_id, brokerage_id=persona.brokerage_id,
        source_document_id=chunks[0]['source_document_id'], status='pending',
        content_hash=_selection_hash(chunks), chunk_count=len(chunks), attempt_count=0,
        max_attempts=3, next_attempt_at=_now(), created_at=_now(), updated_at=_now(),
    )
    session.add(row)
    return row


LEASE_SECONDS = 600
PAYLOAD_INDEXES = {
    'owner_user_id': 'integer', 'brokerage_id': 'keyword',
    'published_version_id': 'integer', 'approval_status': 'keyword',
}


def recover_stale(session: Session, *, now: datetime | None = None) -> int:
    """Release jobs whose worker died mid-run so they can be retried.

    The interrupted attempt stays counted, so a job that keeps crashing a
    worker still stops at ``max_attempts``.
    """
    now = now or _now()
    result = session.execute(
        update(PersonaKnowledgeIndexJob)
        .where(
            PersonaKnowledgeIndexJob.status == 'indexing',
            PersonaKnowledgeIndexJob.claimed_at < now - timedelta(seconds=LEASE_SECONDS),
        )
        .values(status='failed', error_message='Indexing worker lease expired',
                next_attempt_at=now, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return result.rowcount or 0


def _claim_one(session: Session) -> PersonaKnowledgeIndexJob | None:
    now = _now()
    candidate = (
        session.query(PersonaKnowledgeIndexJob.id, PersonaKnowledgeIndexJob.attempt_count)
        .filter(
            PersonaKnowledgeIndexJob.status.in_(('pending', 'failed')),
            PersonaKnowledgeIndexJob.attempt_count < PersonaKnowledgeIndexJob.max_attempts,
            PersonaKnowledgeIndexJob.next_attempt_at <= now,
        )
        .order_by(PersonaKnowledgeIndexJob.created_at, PersonaKnowledgeIndexJob.id)
        .with_for_update(skip_locked=True)
        .first()
    )
    if candidate is None:
        session.rollback()
        return None
    # Compare-and-set: only one worker can move this attempt to 'indexing',
    # including on SQLite, which ignores FOR UPDATE SKIP LOCKED.
    claimed = session.execute(
        update(PersonaKnowledgeIndexJob)
        .where(
            PersonaKnowledgeIndexJob.id == candidate.id,
            PersonaKnowledgeIndexJob.status.in_(('pending', 'failed')),
            PersonaKnowledgeIndexJob.attempt_count == candidate.attempt_count,
        )
        .values(status='indexing', attempt_count=candidate.attempt_count + 1,
                worker_id=f'{socket.gethostname()}:{os.getpid()}'[:100],
                claimed_at=now, error_message=None, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    session.commit()
    if claimed.rowcount != 1:
        return None
    return session.get(PersonaKnowledgeIndexJob, candidate.id, populate_existing=True)


def _finish(session: Session, job: PersonaKnowledgeIndexJob, *, error: Exception | None = None) -> None:
    values = {'updated_at': _now()}
    if error is None:
        values.update(status='ready', completed_at=_now(), error_message=None)
    else:
        values.update(
            status='failed', error_message=str(error)[:500],
            next_attempt_at=_now() + timedelta(seconds=min(300, 2 ** job.attempt_count)),
        )
    result = session.execute(
        update(PersonaKnowledgeIndexJob)
        .where(
            PersonaKnowledgeIndexJob.id == job.id,
            PersonaKnowledgeIndexJob.status == 'indexing',
            PersonaKnowledgeIndexJob.attempt_count == job.attempt_count,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    session.commit()
    if result.rowcount != 1:
        raise RuntimeError('Persona indexing lease was lost')


def process_one(session: Session, store=None) -> bool:
    job = _claim_one(session)
    if job is None:
        return False
    store = store or vector_store
    try:
        version = session.get(PersonaVersion, job.version_id)
        persona = session.get(Persona, job.persona_id)
        if version is None or persona is None or version.persona_id != persona.id:
            raise RuntimeError('Immutable Persona indexing source is missing')
        if persona.owner_user_id != job.owner_user_id or persona.brokerage_id != job.brokerage_id:
            raise RuntimeError('Persona indexing ownership no longer matches')
        chunks = eligible_chunks(version)
        if not chunks or _selection_hash(chunks) != job.content_hash:
            raise RuntimeError('Immutable Persona knowledge selection no longer matches the job')
        collection = settings['sms']['agent_knowledge_collection']
        ids, texts, metadata = [], [], []
        for item in chunks:
            point_id = f'persona-v{version.id}-chunk-{item["chunk_index"]}-{item["content_hash"][:12]}'
            ids.append(point_id)
            texts.append(item['text'])
            metadata.append({
                'owner_user_id': job.owner_user_id,
                'brokerage_id': job.brokerage_id,
                'source_document_id': item['source_document_id'],
                'source_import_ids': item['source_import_ids'],
                'source_type': 'agent_knowledge',
                'published_version_id': version.id,
                'approval_status': 'eligible',
                'source_status': item['source_status'],
                'approval_basis': item['approval_basis'],
                'chunk_index': item['chunk_index'],
                'content_hash': item['content_hash'],
                'embedding_model': store.embedding_name,
                'text': item['text'],
            })
        # Deterministic IDs: a retry or a re-run upserts the same points. The
        # upsert creates a missing collection with the embedding's dimension.
        if not store.add_texts(collection, ids, texts, documents=texts, metadatas=metadata):
            raise RuntimeError('Vector upsert was unavailable')
        # The job is ready only once the authorization filter fields are indexed.
        ensure = getattr(store, 'ensure_payload_indexes', None)
        if ensure is not None and not ensure(collection, PAYLOAD_INDEXES):
            raise RuntimeError('Vector payload indexes were unavailable')
        _finish(session, job)
        return True
    except Exception as error:  # publication remains independent of this path
        logger.warning('Persona knowledge indexing failed: job_id=%s attempt=%s error=%s',
                       job.id, job.attempt_count, error)
        _finish(session, job, error=error)
        return True


def _worker() -> None:
    try:
        with SessionLocal() as session:
            recovered = recover_stale(session)
            if recovered:
                logger.warning('Recovered %s interrupted Persona indexing job(s)', recovered)
    except Exception:
        logger.exception('Persona indexing lease recovery failed')
    last_recovery = _now()
    while not _stop.is_set():
        processed = False
        try:
            with SessionLocal() as session:
                if _now() - last_recovery > timedelta(seconds=60):
                    recover_stale(session)
                    last_recovery = _now()
                processed = process_one(session)
        except Exception:
            logger.exception('Persona knowledge indexing worker iteration failed')
        _stop.wait(0.5 if processed else 3.0)


LOOPBACK_HOSTS = {'127.0.0.1', 'localhost', '::1'}


def local_qdrant_required_but_missing(store=None) -> bool:
    """PERSONA_INDEX_REQUIRE_LOCAL_QDRANT=true allows only a loopback Qdrant.

    Development sets it so the worker can never index into a remote (e.g.
    production Qdrant Cloud) store because of a configuration slip.
    """
    if os.getenv('PERSONA_INDEX_REQUIRE_LOCAL_QDRANT', '').strip().lower() not in {'1', 'true', 'yes', 'on'}:
        return False
    store = store or vector_store
    if getattr(store, 'backend', '') != 'qdrant':
        return True
    from urllib.parse import urlparse
    return (urlparse(str(getattr(store, 'url', '') or '')).hostname or '') not in LOOPBACK_HOSTS


def start_worker() -> threading.Thread | None:
    if os.getenv('PERSONA_INDEX_WORKER_ENABLED', 'true').strip().lower() not in {'1', 'true', 'yes', 'on'}:
        return None
    if local_qdrant_required_but_missing():
        logger.error('[persona-index] worker not started: PERSONA_INDEX_REQUIRE_LOCAL_QDRANT is set '
                     'but the vector store is not a localhost Qdrant')
        return None
    _stop.clear()
    thread = threading.Thread(target=_worker, daemon=True, name='persona-knowledge-index-worker')
    thread.start()
    return thread


def stop_worker() -> None:
    _stop.set()
