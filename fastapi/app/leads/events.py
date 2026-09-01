"""Writes and reads a lead's history: one row per stage move, assignment
change, AI/agent ownership handoff, or notable activity.

Callers log through `log_event` at the point a change already happens (a
handover function, a stage-change endpoint, an assignment patch) rather than
inferring history after the fact by diffing columns.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import Lead, LeadEvent, User

STAGE = 'stage'
ASSIGNMENT = 'assignment'
OWNERSHIP = 'ownership'
ACTIVITY = 'activity'


def log_event(
    session: Session,
    lead_id: int,
    event_category: str,
    event_type: str,
    actor_type: str = 'system',
    actor_id: int | None = None,
    target_id: int | None = None,
    from_value: str | None = None,
    to_value: str | None = None,
    reason: str | None = None,
    meta: dict | None = None,
    commit: bool = True,
) -> LeadEvent:
    event = LeadEvent(
        lead_id=lead_id,
        event_category=event_category,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        target_id=target_id,
        from_value=from_value,
        to_value=to_value,
        reason=reason,
        meta=json.dumps(meta) if meta else None,
        created_at=datetime.utcnow(),
    )
    session.add(event)
    if commit:
        session.commit()
    return event


def _user_label(user: User | None) -> str | None:
    if user is None:
        return None
    return user.full_name or user.email


def serialize(event: LeadEvent) -> dict:
    try:
        meta = json.loads(event.meta) if event.meta else None
    except (TypeError, ValueError):
        meta = None
    return {
        'id': event.id,
        'event_category': event.event_category,
        'event_type': event.event_type,
        'actor_type': event.actor_type,
        'actor_id': event.actor_id,
        'actor_name': _user_label(event.actor),
        'target_id': event.target_id,
        'target_name': _user_label(event.target),
        'from_value': event.from_value,
        'to_value': event.to_value,
        'reason': event.reason,
        'meta': meta,
        'created_at': event.created_at,
    }


def history_for(session: Session, lead: Lead) -> list[dict]:
    """The full timeline for one lead, oldest first.

    Intake has no discrete row of its own — a bulk CSV import (up to six
    figures of rows in one call) writes leads in a single batched insert for
    speed, and a per-row history write would undo that. `Lead.created_at` is
    already the intake timestamp, so it's synthesized as the first entry here
    instead of being persisted per lead.
    """
    events = (
        session.query(LeadEvent)
        .filter(LeadEvent.lead_id == lead.id)
        .order_by(LeadEvent.created_at.asc(), LeadEvent.id.asc())
        .all()
    )
    intake = {
        'id': 0,
        'event_category': STAGE,
        'event_type': 'intake',
        'actor_type': 'system',
        'actor_id': None,
        'actor_name': None,
        'target_id': None,
        'target_name': None,
        'from_value': None,
        'to_value': lead.source,
        'reason': None,
        'meta': None,
        'created_at': lead.created_at,
    }
    return [intake] + [serialize(event) for event in events]
