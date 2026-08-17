"""Lead pool operations: import, filter, and hand leads to Bobbie.

The three right-hand columns of the pool — score, stage, last activity — are
system-owned. Stage in particular is *derived on read* from the lead's own
state, so it can never drift out of sync with the conversation it points at.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

import json

from ..models import Conversation, Lead, Message, User
from .catalog import SIGNALS, signal_label
from .importer import parse_upload, score_lead

# How long an imported or re-imported lead reads as "New / Refreshed".
FRESH_WINDOW = timedelta(hours=24)

# Ids per DELETE statement — comfortably under every driver's bind-parameter cap.
DELETE_CHUNK = 500


def split_signals(lead: Lead) -> list[str]:
    return [key for key in (lead.signals or '').split(',') if key]


def lead_details(lead: Lead) -> dict:
    """The imported property attributes, or {} if there were none."""
    if not lead.details:
        return {}
    try:
        parsed = json.loads(lead.details)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def derive_stage(lead: Lead, now: datetime | None = None) -> str:
    """Where this lead stands with us, in strict precedence."""
    if lead.dnc:
        return 'dnc'
    if lead.conversation_id is not None:
        return 'in_campaign'
    if not lead.phone:
        # Nothing can be sent without a number, however good the signals are.
        return 'needs_review'
    stamped = lead.refreshed_at or lead.created_at
    if stamped and (now or datetime.utcnow()) - stamped < FRESH_WINDOW:
        return 'new'
    return 'ready'


def serialize(lead: Lead, now: datetime | None = None) -> dict:
    signals = split_signals(lead)
    return {
        'id': lead.id,
        'owner_name': lead.owner_name,
        'phone': lead.phone,
        'property_address': lead.property_address,
        'area': lead.area,
        'signals': [{'key': key, 'label': signal_label(key)} for key in signals],
        'score': lead.score,
        'outreach_reason': lead.outreach_reason,
        'stage': derive_stage(lead, now),
        'last_activity_at': lead.last_activity_at,
        'conversation_id': lead.conversation_id,
        'created_at': lead.created_at,
    }


def score_breakdown(lead: Lead) -> list[dict]:
    """Why this lead scores what it scores, in the order the points are added.

    The score is advisory, so showing its arithmetic is the honest thing to do:
    a broker can see it is a sum of signals and contactability, not a model.
    """
    parts = [{'label': signal_label(key), 'points': SIGNALS.get(key, ('', 0))[1]}
             for key in split_signals(lead)]
    if lead.phone:
        parts.append({'label': 'Phone number on file', 'points': 20})
    if lead.property_address:
        parts.append({'label': 'Property address on file', 'points': 10})
    return parts


def detail(session: Session, lead: Lead) -> dict:
    """Everything the details panel shows, including the linked conversation."""
    item = serialize(lead)
    item['refreshed_at'] = lead.refreshed_at
    item['details'] = lead_details(lead)
    item['score_breakdown'] = score_breakdown(lead)
    item['conversation'] = None

    if lead.conversation_id is not None:
        conversation = (session.query(Conversation)
                        .filter(Conversation.id == lead.conversation_id)
                        .first())
        if conversation is not None:
            latest = (session.query(Message)
                      .filter(Message.conversation_id == conversation.id)
                      .order_by(Message.created_at.desc(), Message.id.desc())
                      .first())
            item['conversation'] = {
                'id': conversation.id,
                'handled_by': conversation.handled_by,
                'lead_status': conversation.lead_status,
                'meeting_booked': bool(conversation.meeting_booked),
                'message_count': (session.query(Message)
                                  .filter(Message.conversation_id == conversation.id).count()),
                'latest_message': latest.text if latest else None,
                'latest_message_at': latest.created_at if latest else None,
            }
    return item


def list_leads(session: Session, user: User, search: str = '', signals: list[str] | None = None,
               stage: str = '') -> list[dict]:
    """Leads for this broker, filtered by owner/address text, signals and stage.

    Signal and stage filters run in Python: signals are a packed column and
    stage is derived, so neither can be expressed as a portable SQL predicate.
    The pool is a per-broker working set, not a warehouse table.
    """
    query = session.query(Lead).filter(Lead.user_id == user.id)
    needle = (search or '').strip()
    if needle:
        like = f'%{needle}%'
        query = query.filter(
            Lead.owner_name.ilike(like) | Lead.property_address.ilike(like) | Lead.area.ilike(like)
        )

    now = datetime.utcnow()
    rows = query.order_by(Lead.score.desc(), Lead.id.desc()).all()
    wanted = {key for key in (signals or []) if key}
    result = []
    for lead in rows:
        if wanted and not wanted.issubset(set(split_signals(lead))):
            continue
        item = serialize(lead, now)
        if stage and item['stage'] != stage:
            continue
        result.append(item)
    return result


def facets(session: Session, user: User) -> dict:
    """Counts per signal and per stage across the broker's whole pool."""
    now = datetime.utcnow()
    signal_counts: dict[str, int] = {}
    stage_counts: dict[str, int] = {}
    total = 0
    for lead in session.query(Lead).filter(Lead.user_id == user.id).all():
        total += 1
        for key in split_signals(lead):
            signal_counts[key] = signal_counts.get(key, 0) + 1
        stage = derive_stage(lead, now)
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    return {'total': total, 'signals': signal_counts, 'stages': stage_counts}


def analyze_upload(session: Session, user: User, content: bytes, filename: str,
                   overrides: dict[str, str] | None = None) -> dict:
    """What this file would do, without writing anything.

    Every readable row becomes a lead: nothing is merged or matched against the
    pool. The broker needs the counts before choosing a row limit, and needs to
    see which column was read as what.
    """
    rows, warnings, meta = parse_upload(content, filename, overrides)

    return {
        'rows': rows,
        'columns': meta['columns'],
        'mapping': meta['mapping'],
        'signal_columns': meta['signal_columns'],
        'total_rows': len(rows),
        'importable': len(rows),
        'warnings': warnings,
        'sample': [
            {
                'owner_name': row['owner_name'],
                'phone': row['phone'],
                'property_address': row['property_address'],
                'area': row['area'],
                'signals': [signal_label(key) for key in row['signals']],
                'outreach_reason': row.get('outreach_reason'),
                'details': row.get('details') or {},
            }
            for row in rows[:5]
        ],
    }


# Rows per INSERT batch. A vendor list can run to six figures, and one flush of
# that size is far slower than a handful of batches.
IMPORT_CHUNK = 1000


def import_file(session: Session, user: User, content: bytes, filename: str,
                limit: int = 0, overrides: dict[str, str] | None = None) -> dict:
    """Add a CSV or XLSX to the broker's pool, one lead per readable row.

    `limit` caps how many rows are taken (0 means all). Rows are never matched
    against each other or against the pool — importing the same file twice adds
    every lead twice, which is the broker's call to make.
    """
    report = analyze_upload(session, user, content, filename, overrides)
    rows = report['rows']
    if not rows:
        return {'created': 0, 'total_rows': report['total_rows'], 'warnings': report['warnings']}

    if limit and limit > 0:
        rows = rows[:limit]

    now = datetime.utcnow()
    for start in range(0, len(rows), IMPORT_CHUNK):
        session.bulk_save_objects([
            Lead(
                user_id=user.id,
                owner_name=row['owner_name'],
                phone=row['phone'],
                property_address=row['property_address'],
                area=row['area'],
                signals=','.join(row['signals']),
                outreach_reason=row.get('outreach_reason'),
                details=json.dumps(row['details']) if row.get('details') else None,
                score=row['score'],
                dnc=False,
                created_at=now,
                refreshed_at=now,
            )
            for row in rows[start:start + IMPORT_CHUNK]
        ])
        session.commit()

    return {'created': len(rows), 'total_rows': report['total_rows'],
            'warnings': report['warnings']}


# What Bobbie says she is calling about, per signal. Keeping this factual
# matters: she is only allowed to state what the record actually shows.
_REASON_BY_SIGNAL: dict[str, str] = {
    'expired': 'The property appears to have come off the market without a recorded sale',
    'fsbo': 'The property appears to be listed for sale by the owner',
    'pre_foreclosure': 'Public records show a pre-foreclosure filing on the property',
    'tax_delinquent': 'Public records show delinquent property taxes',
    'probate': 'The property appears in probate records',
    'divorce': 'Public records suggest the property may be part of a change in ownership',
    'vacant': 'The property appears to be vacant',
    'absentee_owner': 'Records show the owner lives at a different address',
    'high_equity': 'Public records suggest the property carries significant equity',
}

FALLBACK_REASON = 'I came across the property while reviewing records in the area'


def outreach_reason(lead: Lead, override: str = '') -> str:
    """The one sentence Bobbie uses to explain why she reached out.

    A reason shipped with the lead wins over the generic per-signal wording:
    the vendor knows what the record actually shows.
    """
    if (override or '').strip():
        return override.strip()
    if (lead.outreach_reason or '').strip():
        return lead.outreach_reason.strip().rstrip('.')
    for key in split_signals(lead):
        if key in _REASON_BY_SIGNAL:
            return _REASON_BY_SIGNAL[key]
    return FALLBACK_REASON


def delete_leads(session: Session, user: User, lead_ids: list[int]) -> int:
    """Remove leads from this broker's pool. Ids that are not theirs are ignored.

    The ids are chunked because an IN clause is one bind parameter per id and
    every database caps those (SQLite historically at 999). Selecting a whole
    pool of several thousand leads has to work.

    The linked conversation is deliberately left alone: a thread Bobbie has
    already opened is a record of contact, not pool clutter.
    """
    unique = list(dict.fromkeys(lead_ids))
    deleted = 0
    for start in range(0, len(unique), DELETE_CHUNK):
        deleted += (session.query(Lead)
                    .filter(Lead.user_id == user.id,
                            Lead.id.in_(unique[start:start + DELETE_CHUNK]))
                    .delete(synchronize_session=False))
    if deleted:
        session.commit()
    return int(deleted)


def sync_activity(session: Session, user_id: int) -> None:
    """Refresh last-activity and DNC from the conversations leads point at."""
    leads = (session.query(Lead)
             .filter(Lead.user_id == user_id, Lead.conversation_id.isnot(None))
             .all())
    for lead in leads:
        conversation = session.query(Conversation).filter(Conversation.id == lead.conversation_id).first()
        if conversation is None:
            # The broker deleted the thread; the lead returns to the pool.
            lead.conversation_id = None
            continue
        latest = (session.query(Message)
                  .filter(Message.conversation_id == conversation.id)
                  .order_by(Message.created_at.desc(), Message.id.desc())
                  .first())
        if latest is not None:
            lead.last_activity_at = latest.created_at
        if conversation.dnc_alert:
            lead.dnc = True
    session.commit()
